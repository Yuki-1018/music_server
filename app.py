import os
import json
import uuid
import threading
import time
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify, session
from werkzeug.utils import secure_filename
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

# --- 設定 ---
app.config['MUSIC_FOLDER'] = 'music'
app.config['IMAGES_FOLDER'] = 'images'
app.config['DATA_FOLDER'] = 'data'
app.config['ARTISTS_FOLDER'] = os.path.join(app.config['DATA_FOLDER'], 'artists')
app.config['ALBUMS_FOLDER'] = os.path.join(app.config['DATA_FOLDER'], 'albums')
app.config['INDEX_FILE'] = os.path.join(app.config['DATA_FOLDER'], 'index.json')
app.secret_key = 'super_secret_key_change_me'
PASSWORD = '123456'
ALLOWED_EXTENSIONS_IMG = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# --- 初期化 ---
for folder in [app.config['MUSIC_FOLDER'], app.config['IMAGES_FOLDER'], 
               app.config['DATA_FOLDER'], app.config['ARTISTS_FOLDER'], app.config['ALBUMS_FOLDER']]:
    if not os.path.exists(folder):
        os.makedirs(folder)

if not os.path.exists(app.config['INDEX_FILE']):
    with open(app.config['INDEX_FILE'], 'w', encoding='utf-8') as f:
        json.dump([], f)

# --- データ操作ヘルパー (分割保存対応) ---

def load_index():
    """全アーティスト一覧を取得"""
    try:
        with open(app.config['INDEX_FILE'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_index(data):
    with open(app.config['INDEX_FILE'], 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_artist(artist_id):
    """アーティスト詳細（アルバムリスト含む、トラックは含まない）"""
    filepath = os.path.join(app.config['ARTISTS_FOLDER'], f"{artist_id}.json")
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_artist(artist_data):
    """アーティスト情報を保存し、index.jsonも更新"""
    # 1. アーティストファイルの保存
    filepath = os.path.join(app.config['ARTISTS_FOLDER'], f"{artist_data['id']}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(artist_data, f, indent=4, ensure_ascii=False)

    # 2. index.json の更新
    index_data = load_index()
    summary = {
        "id": artist_data['id'],
        "name": artist_data['name'],
        "genre": artist_data.get('genre', ''),
        "description": artist_data.get('description', ''),
        "image": artist_data.get('image', ''),
        "album_count": len(artist_data['albums'])
    }
    
    found = False
    for i, item in enumerate(index_data):
        if item['id'] == artist_data['id']:
            index_data[i] = summary
            found = True
            break
    if not found:
        index_data.append(summary)
        
    save_index(index_data)

def load_album(album_id):
    """アルバム詳細（トラックデータを含む）"""
    filepath = os.path.join(app.config['ALBUMS_FOLDER'], f"{album_id}.json")
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_album(album_data):
    """アルバム情報を保存"""
    filepath = os.path.join(app.config['ALBUMS_FOLDER'], f"{album_data['id']}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(album_data, f, indent=4, ensure_ascii=False)

def delete_artist_data(artist_id):
    """アーティストと、その全アルバムを削除"""
    artist = load_artist(artist_id)
    if artist:
        # 関連アルバムの削除
        for alb_ref in artist['albums']:
            alb_path = os.path.join(app.config['ALBUMS_FOLDER'], f"{alb_ref['id']}.json")
            if os.path.exists(alb_path):
                os.remove(alb_path)
        
        # アーティストファイルの削除
        art_path = os.path.join(app.config['ARTISTS_FOLDER'], f"{artist_id}.json")
        if os.path.exists(art_path):
            os.remove(art_path)

    # indexからの削除
    index_data = load_index()
    index_data = [a for a in index_data if a['id'] != artist_id]
    save_index(index_data)

def delete_album_data(artist_id, album_id):
    """アルバム単体の削除（アーティスト側のリストからも削除）"""
    # アルバムファイルの削除
    alb_path = os.path.join(app.config['ALBUMS_FOLDER'], f"{album_id}.json")
    if os.path.exists(alb_path):
        os.remove(alb_path)

    # アーティスト情報の更新
    artist = load_artist(artist_id)
    if artist:
        artist['albums'] = [a for a in artist['albums'] if a['id'] != album_id]
        save_artist(artist)

# --- その他ヘルパー ---
def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_IMG

def save_image_file(file):
    if file and allowed_image(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(app.config['IMAGES_FOLDER'], filename))
        return filename
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- バックグラウンド処理 ---

def background_download_process(album_id, url, temp_track_id, start_track_num):
    """
    バックグラウンドダウンロード
    ※アルバムIDだけで操作可能になったため、artist_idは不要になりましたが、
    念のため整合性チェック等は必要であれば追加してください。
    """
    try:
        ydl_opts_info = {'quiet': True, 'extract_flat': 'in_playlist', 'ignoreerrors': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        if 'entries' in info: entries = list(info['entries'])
        else: entries = [info]

        # アルバムデータの読み込み
        album = load_album(album_id)
        if not album: return

        # 仮トラック削除
        album['tracks'] = [t for t in album['tracks'] if t['id'] != temp_track_id]

        download_queue = []
        current_num = start_track_num

        for entry in entries:
            if not entry: continue
            track_id = str(uuid.uuid4())
            title = entry.get('title', 'Unknown Title')
            video_url = entry.get('url') or entry.get('webpage_url')
            
            placeholder = {
                "id": track_id,
                "title": f"[Waiting] {title}",
                "track_number": current_num,
                "filename": None,
                "processing": True,
                "original_url": video_url
            }
            album['tracks'].append(placeholder)
            download_queue.append(placeholder)
            current_num += 1
        
        album['tracks'].sort(key=lambda x: x['track_number'])
        save_album(album)

        # ダウンロード実行
        ydl_opts_dl = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'ignoreerrors': True
        }

        for item in download_queue:
            album = load_album(album_id) # 最新状態をリロード
            if not album: break
            
            target_track = next((t for t in album['tracks'] if t['id'] == item['id']), None)
            if not target_track: continue

            target_track['title'] = f"[Downloading...] {item['title'].replace('[Waiting] ', '')}"
            save_album(album)

            try:
                base_id = uuid.uuid4().hex
                save_path_base = os.path.join(app.config['MUSIC_FOLDER'], base_id)
                current_opts = ydl_opts_dl.copy()
                current_opts['outtmpl'] = save_path_base

                with yt_dlp.YoutubeDL(current_opts) as ydl:
                    dl_info = ydl.extract_info(item['original_url'], download=True)
                    real_title = dl_info.get('title', 'Unknown Title')

                target_track['title'] = real_title
                target_track['filename'] = f"{base_id}.mp3"
                if 'processing' in target_track: del target_track['processing']
                save_album(album)

            except Exception as e:
                target_track['title'] = f"[Error] {item['title']}"
                if 'processing' in target_track: del target_track['processing']
                save_album(album)

    except Exception as e:
        print(f"Background process error: {e}")

# --- ルーティング ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['password'] == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else: return render_template('login.html', error='Invalid password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/stream/<path:filename>')
def stream_music(filename):
    return send_from_directory(app.config['MUSIC_FOLDER'], filename)

@app.route('/image/<path:filename>')
def serve_image(filename):
    return send_from_directory(app.config['IMAGES_FOLDER'], filename)

# --- API (構造変更) ---

@app.route('/api/artists')
def api_get_artists():
    """全アーティスト一覧"""
    data = load_index()
    for artist in data:
        if artist.get('image'):
            artist['image_url'] = url_for('serve_image', filename=artist['image'], _external=True)
        artist['api_url'] = url_for('api_get_artist_detail', artist_id=artist['id'], _external=True)
    return jsonify(data)

@app.route('/api/artist/<artist_id>')
def api_get_artist_detail(artist_id):
    """アーティスト詳細（アルバム一覧のみ。曲は含まない）"""
    artist = load_artist(artist_id)
    if not artist: return jsonify({"error": "Artist not found"}), 404
    
    if artist.get('image'):
        artist['image_url'] = url_for('serve_image', filename=artist['image'], _external=True)

    for album in artist['albums']:
        if album.get('cover_image'):
            album['cover_url'] = url_for('serve_image', filename=album['cover_image'], _external=True)
        # APIのリンクを追加
        album['api_url'] = url_for('api_get_album_detail', album_id=album['id'], _external=True)

    return jsonify(artist)

@app.route('/api/album/<album_id>')
def api_get_album_detail(album_id):
    """【新規】アルバム詳細（曲一覧を含む）"""
    album = load_album(album_id)
    if not album: return jsonify({"error": "Album not found"}), 404

    if album.get('cover_image'):
        album['cover_url'] = url_for('serve_image', filename=album['cover_image'], _external=True)

    for track in album['tracks']:
        if not track.get('processing') and track.get('filename'):
            track['stream_url'] = url_for('stream_music', filename=track['filename'], _external=True)
        track['cover_url'] = album.get('cover_url')

    return jsonify(album)

# --- 管理画面 ---

@app.route('/')
@login_required
def index():
    return render_template('index.html', artists=load_index())

@app.route('/artist/add', methods=['POST'])
@login_required
def add_artist():
    img_filename = save_image_file(request.files.get('image'))
    new_artist = {
        "id": str(uuid.uuid4()),
        "name": request.form['name'],
        "genre": request.form.get('genre', ''),
        "description": request.form.get('description', ''),
        "image": img_filename,
        "albums": [] # メタデータのみ
    }
    save_artist(new_artist)
    return redirect(url_for('index'))

@app.route('/artist/<artist_id>/edit', methods=['POST'])
@login_required
def edit_artist(artist_id):
    artist = load_artist(artist_id)
    if artist:
        artist['name'] = request.form['name']
        artist['genre'] = request.form['genre']
        artist['description'] = request.form['description']
        new_img = save_image_file(request.files.get('image'))
        if new_img: artist['image'] = new_img
        save_artist(artist)
    return redirect(url_for('index'))

@app.route('/artist/<artist_id>/delete', methods=['POST'])
@login_required
def delete_artist(artist_id):
    delete_artist_data(artist_id)
    return redirect(url_for('index'))

@app.route('/artist/<artist_id>')
@login_required
def view_artist(artist_id):
    artist = load_artist(artist_id)
    if not artist: return "Not Found", 404
    return render_template('artist.html', artist=artist)

@app.route('/artist/<artist_id>/album/add', methods=['POST'])
@login_required
def add_album(artist_id):
    artist = load_artist(artist_id)
    if artist:
        album_id = str(uuid.uuid4())
        img_filename = save_image_file(request.files.get('image'))
        
        # 1. アーティスト詳細には「参照情報」のみ保存
        album_ref = {
            "id": album_id,
            "title": request.form['title'],
            "year": request.form.get('year', ''),
            "type": request.form.get('type', 'Album'),
            "cover_image": img_filename
        }
        artist['albums'].append(album_ref)
        save_artist(artist)

        # 2. アルバム詳細ファイルを作成（ここにトラックが入る）
        new_album_detail = {
            "id": album_id,
            "artist_id": artist_id, # 親への参照
            "artist_name": artist['name'],
            "title": request.form['title'],
            "year": request.form.get('year', ''),
            "type": request.form.get('type', 'Album'),
            "cover_image": img_filename,
            "tracks": []
        }
        save_album(new_album_detail)

    return redirect(url_for('view_artist', artist_id=artist_id))

@app.route('/artist/<artist_id>/album/<album_id>/edit', methods=['POST'])
@login_required
def edit_album(artist_id, album_id):
    artist = load_artist(artist_id)
    album_detail = load_album(album_id)

    if artist and album_detail:
        # 両方を更新する必要がある
        title = request.form['title']
        year = request.form['year']
        atype = request.form['type']
        new_img = save_image_file(request.files.get('image'))

        # 1. アーティスト側の参照更新
        for ref in artist['albums']:
            if ref['id'] == album_id:
                ref['title'] = title
                ref['year'] = year
                ref['type'] = atype
                if new_img: ref['cover_image'] = new_img
                break
        save_artist(artist)

        # 2. アルバム詳細の更新
        album_detail['title'] = title
        album_detail['year'] = year
        album_detail['type'] = atype
        if new_img: album_detail['cover_image'] = new_img
        save_album(album_detail)

    return redirect(url_for('view_artist', artist_id=artist_id))

@app.route('/artist/<artist_id>/album/<album_id>/delete', methods=['POST'])
@login_required
def delete_album(artist_id, album_id):
    delete_album_data(artist_id, album_id)
    return redirect(url_for('view_artist', artist_id=artist_id))

@app.route('/artist/<artist_id>/album/<album_id>')
@login_required
def view_album(artist_id, album_id):
    artist = load_artist(artist_id)
    album = load_album(album_id)
    if not artist or not album: return "Not Found", 404
    return render_template('album.html', artist=artist, album=album)

@app.route('/artist/<artist_id>/album/<album_id>/track/add', methods=['POST'])
@login_required
def add_track(artist_id, album_id):
    if 'file' not in request.files: return "No file", 400
    file = request.files['file']
    if file.filename == '' or not file: return "No file", 400

    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
    file.save(os.path.join(app.config['MUSIC_FOLDER'], unique_filename))

    album = load_album(album_id)
    if album:
        track_num = request.form.get('track_number')
        if not track_num: track_num = len(album['tracks']) + 1

        new_track = {
            "id": str(uuid.uuid4()),
            "title": request.form.get('title') or filename,
            "track_number": int(track_num),
            "filename": unique_filename
        }
        album['tracks'].append(new_track)
        album['tracks'].sort(key=lambda x: x['track_number'])
        save_album(album)

    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))

@app.route('/artist/<artist_id>/album/<album_id>/track/add_url', methods=['POST'])
@login_required
def add_track_url(artist_id, album_id):
    url = request.form.get('url')
    if not url: return "No URL provided", 400

    album = load_album(album_id)
    if not album: return "Album not found", 404

    track_start_num = request.form.get('track_number')
    if track_start_num:
        current_track_num = int(track_start_num)
    else:
        current_track_num = len(album['tracks']) + 1

    temp_track_id = str(uuid.uuid4())
    temp_track = {
        "id": temp_track_id,
        "title": "Initializing Import...",
        "track_number": current_track_num,
        "filename": None,
        "processing": True
    }
    album['tracks'].append(temp_track)
    album['tracks'].sort(key=lambda x: x['track_number'])
    save_album(album)

    thread = threading.Thread(
        target=background_download_process,
        args=(album_id, url, temp_track_id, current_track_num)
    )
    thread.start()

    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))

@app.route('/artist/<artist_id>/album/<album_id>/track/<track_id>/edit', methods=['POST'])
@login_required
def edit_track(artist_id, album_id, track_id):
    album = load_album(album_id)
    if album:
        track = next((t for t in album['tracks'] if t['id'] == track_id), None)
        if track:
            track['title'] = request.form['title']
            try: track['track_number'] = int(request.form['track_number'])
            except: pass
            album['tracks'].sort(key=lambda x: x['track_number'])
            save_album(album)
    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))

@app.route('/artist/<artist_id>/album/<album_id>/track/<track_id>/delete', methods=['POST'])
@login_required
def delete_track(artist_id, album_id, track_id):
    album = load_album(album_id)
    if album:
        track = next((t for t in album['tracks'] if t['id'] == track_id), None)
        if track:
            if track.get('filename'):
                try: os.remove(os.path.join(app.config['MUSIC_FOLDER'], track['filename']))
                except: pass
            album['tracks'] = [t for t in album['tracks'] if t['id'] != track_id]
            save_album(album)
    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))
# --------- ここだけ追加 ---------
class PrefixMiddleware(object):
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        environ["SCRIPT_NAME"] = self.prefix
        path = environ.get("PATH_INFO", "")
        if path.startswith(self.prefix):
            environ["PATH_INFO"] = path[len(self.prefix):]
        return self.app(environ, start_response)

app.wsgi_app = PrefixMiddleware(app.wsgi_app, "/music")
# --------------------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
