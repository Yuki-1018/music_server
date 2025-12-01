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

app.config['MUSIC_FOLDER'] = 'music'
app.config['IMAGES_FOLDER'] = 'images'
app.config['DATA_FOLDER'] = 'data'
app.config['INDEX_FILE'] = os.path.join(app.config['DATA_FOLDER'], 'index.json')
app.secret_key = 'super_secret_key_change_me'
PASSWORD = '123456'
ALLOWED_EXTENSIONS_IMG = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# --- 初期化 ---
for folder in [app.config['MUSIC_FOLDER'], app.config['IMAGES_FOLDER'], app.config['DATA_FOLDER']]:
    if not os.path.exists(folder):
        os.makedirs(folder)

if not os.path.exists(app.config['INDEX_FILE']):
    with open(app.config['INDEX_FILE'], 'w', encoding='utf-8') as f:
        json.dump([], f)

# --- データ操作ヘルパー ---

def load_index():
    try:
        with open(app.config['INDEX_FILE'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_index(data):
    with open(app.config['INDEX_FILE'], 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_artist_detail(artist_id):
    filepath = os.path.join(app.config['DATA_FOLDER'], f"{artist_id}.json")
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_artist_detail(artist_data):
    # ファイルの競合を避けるため、実際の運用ではロック機構が望ましいが
    # 個人利用レベルなら上書き保存で対応
    filepath = os.path.join(app.config['DATA_FOLDER'], f"{artist_data['id']}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(artist_data, f, indent=4, ensure_ascii=False)

    # 一覧(index.json)更新
    index_data = load_index()
    summary_data = {
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
            index_data[i] = summary_data
            found = True
            break
    
    if not found:
        index_data.append(summary_data)
        
    save_index(index_data)

def delete_artist_data(artist_id):
    filepath = os.path.join(app.config['DATA_FOLDER'], f"{artist_id}.json")
    if os.path.exists(filepath):
        os.remove(filepath)
    
    index_data = load_index()
    index_data = [a for a in index_data if a['id'] != artist_id]
    save_index(index_data)

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

def background_download_process(artist_id, album_id, url, temp_track_id, start_track_num):
    """
    バックグラウンドで実行されるダウンロード処理
    """
    # アプリケーションコンテキスト外で動くため、パス等はconfigから取得済み前提
    # ただし今回はシンプルに app.config を参照 (Flaskの仕様上、thread内でもconfigは読めることが多いが安全策をとるなら引数で渡す)
    
    try:
        # 1. まず情報を取得（フラット抽出で高速化）
        ydl_opts_info = {
            'quiet': True, 
            'extract_flat': 'in_playlist',
            'ignoreerrors': True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        if 'entries' in info:
            entries = list(info['entries']) # イテレータをリスト化
        else:
            entries = [info]

        # 2. JSONをロードして、「Initializing...」の仮トラックを削除し、
        #    代わりに全曲分の「Processing...」トラックを作成する
        
        # データの再読み込み（他スレッドでの変更を取り込むため）
        artist = load_artist_detail(artist_id)
        if not artist: return
        album = next((a for a in artist['albums'] if a['id'] == album_id), None)
        if not album: return

        # 仮トラック(temp_track_id)を削除
        album['tracks'] = [t for t in album['tracks'] if t['id'] != temp_track_id]

        # ダウンロード予定のリストを作成
        download_queue = []
        current_num = start_track_num

        for entry in entries:
            if not entry: continue
            
            track_id = str(uuid.uuid4())
            title = entry.get('title', 'Unknown Title')
            video_url = entry.get('url') or entry.get('webpage_url')
            
            # プレースホルダートラック（処理中フラグ付き）
            placeholder = {
                "id": track_id,
                "title": f"[Waiting] {title}",
                "track_number": current_num,
                "filename": None,
                "processing": True, # これが重要
                "original_url": video_url
            }
            album['tracks'].append(placeholder)
            download_queue.append(placeholder)
            current_num += 1
        
        # 一旦ソートして保存（ユーザーには準備中リストが見える）
        album['tracks'].sort(key=lambda x: x['track_number'])
        save_artist_detail(artist)

        # 3. 1曲ずつダウンロードして更新
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
            # 最新のデータを都度読み込む（ユーザーが他の曲を消したりしている可能性があるため）
            artist = load_artist_detail(artist_id)
            album = next((a for a in artist['albums'] if a['id'] == album_id), None)
            if not album: break

            # 対象のトラックデータを探す
            target_track = next((t for t in album['tracks'] if t['id'] == item['id']), None)
            if not target_track:
                continue # ユーザーによって消された場合はスキップ

            # ステータス更新: ダウンロード中
            target_track['title'] = f"[Downloading...] {item['title'].replace('[Waiting] ', '')}"
            save_artist_detail(artist)

            try:
                base_id = uuid.uuid4().hex
                save_path_base = os.path.join(app.config['MUSIC_FOLDER'], base_id)
                
                # 個別にオプション設定（出力ファイル名を指定）
                current_opts = ydl_opts_dl.copy()
                current_opts['outtmpl'] = save_path_base

                with yt_dlp.YoutubeDL(current_opts) as ydl:
                    # ダウンロード実行
                    dl_info = ydl.extract_info(item['original_url'], download=True)
                    real_title = dl_info.get('title', 'Unknown Title')

                # 完了後の更新
                target_track['title'] = real_title
                target_track['filename'] = f"{base_id}.mp3"
                target_track['processing'] = False # フラグ解除
                if 'processing' in target_track:
                    del target_track['processing'] # キーごと消してもOK

                save_artist_detail(artist)

            except Exception as e:
                # エラー時はタイトルにエラーを表示してフラグ解除
                target_track['title'] = f"[Error] {item['title']}"
                target_track['processing'] = False
                save_artist_detail(artist)
                print(f"Error downloading {item['original_url']}: {e}")

    except Exception as e:
        print(f"Background process error: {e}")
        # 仮トラックが残っていたら消すなどの処理があると丁寧だが、ここでは省略


# --- ルーティング ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['password'] == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid password')
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

# --- API ---
@app.route('/api/artists')
def api_get_artists():
    data = load_index()
    for artist in data:
        if artist.get('image'):
            artist['image_url'] = url_for('serve_image', filename=artist['image'], _external=True)
        artist['api_url'] = url_for('api_get_artist_detail', artist_id=artist['id'], _external=True)
    return jsonify(data)

@app.route('/api/artist/<artist_id>')
def api_get_artist_detail(artist_id):
    artist = load_artist_detail(artist_id)
    if not artist: return jsonify({"error": "Artist not found"}), 404
    
    if artist.get('image'):
        artist['image_url'] = url_for('serve_image', filename=artist['image'], _external=True)

    for album in artist['albums']:
        if album.get('cover_image'):
            album['cover_url'] = url_for('serve_image', filename=album['cover_image'], _external=True)
        
        for track in album['tracks']:
            # 処理中のトラックはストリームURLを生成しない、またはダミーを返す
            if not track.get('processing') and track.get('filename'):
                track['stream_url'] = url_for('stream_music', filename=track['filename'], _external=True)
            track['cover_url'] = album.get('cover_url')

    return jsonify(artist)

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
        "albums": []
    }
    save_artist_detail(new_artist)
    return redirect(url_for('index'))

@app.route('/artist/<artist_id>/edit', methods=['POST'])
@login_required
def edit_artist(artist_id):
    artist = load_artist_detail(artist_id)
    if artist:
        artist['name'] = request.form['name']
        artist['genre'] = request.form['genre']
        artist['description'] = request.form['description']
        new_img = save_image_file(request.files.get('image'))
        if new_img: artist['image'] = new_img
        save_artist_detail(artist)
    return redirect(url_for('index'))

@app.route('/artist/<artist_id>/delete', methods=['POST'])
@login_required
def delete_artist(artist_id):
    delete_artist_data(artist_id)
    return redirect(url_for('index'))

@app.route('/artist/<artist_id>')
@login_required
def view_artist(artist_id):
    artist = load_artist_detail(artist_id)
    if not artist: return "Not Found", 404
    return render_template('artist.html', artist=artist)

@app.route('/artist/<artist_id>/album/add', methods=['POST'])
@login_required
def add_album(artist_id):
    artist = load_artist_detail(artist_id)
    if artist:
        img_filename = save_image_file(request.files.get('image'))
        new_album = {
            "id": str(uuid.uuid4()),
            "title": request.form['title'],
            "year": request.form.get('year', ''),
            "type": request.form.get('type', 'Album'),
            "cover_image": img_filename,
            "tracks": []
        }
        artist['albums'].append(new_album)
        save_artist_detail(artist)
    return redirect(url_for('view_artist', artist_id=artist_id))

@app.route('/artist/<artist_id>/album/<album_id>/edit', methods=['POST'])
@login_required
def edit_album(artist_id, album_id):
    artist = load_artist_detail(artist_id)
    if artist:
        album = next((a for a in artist['albums'] if a['id'] == album_id), None)
        if album:
            album['title'] = request.form['title']
            album['year'] = request.form['year']
            album['type'] = request.form['type']
            new_img = save_image_file(request.files.get('image'))
            if new_img: album['cover_image'] = new_img
            save_artist_detail(artist)
    return redirect(url_for('view_artist', artist_id=artist_id))

@app.route('/artist/<artist_id>/album/<album_id>/delete', methods=['POST'])
@login_required
def delete_album(artist_id, album_id):
    artist = load_artist_detail(artist_id)
    if artist:
        artist['albums'] = [al for al in artist['albums'] if al['id'] != album_id]
        save_artist_detail(artist)
    return redirect(url_for('view_artist', artist_id=artist_id))

@app.route('/artist/<artist_id>/album/<album_id>')
@login_required
def view_album(artist_id, album_id):
    artist = load_artist_detail(artist_id)
    if not artist: return "Artist Not Found", 404
    album = next((a for a in artist['albums'] if a['id'] == album_id), None)
    if not album: return "Album Not Found", 404
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

    artist = load_artist_detail(artist_id)
    album = next((a for a in artist['albums'] if a['id'] == album_id), None)
    
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
        save_artist_detail(artist)

    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))

# --- トラック追加 (バックグラウンド対応版) ---
@app.route('/artist/<artist_id>/album/<album_id>/track/add_url', methods=['POST'])
@login_required
def add_track_url(artist_id, album_id):
    url = request.form.get('url')
    if not url: return "No URL provided", 400

    artist = load_artist_detail(artist_id)
    album = next((a for a in artist['albums'] if a['id'] == album_id), None)
    
    if not album: return "Album not found", 404

    # 開始トラック番号
    track_start_num = request.form.get('track_number')
    if track_start_num:
        current_track_num = int(track_start_num)
    else:
        current_track_num = len(album['tracks']) + 1

    # 仮のトラックを作成して即保存（ユーザーには「準備中」と表示される）
    temp_track_id = str(uuid.uuid4())
    temp_track = {
        "id": temp_track_id,
        "title": "Initializing Import...",
        "track_number": current_track_num,
        "filename": None,
        "processing": True # 処理中フラグ
    }
    album['tracks'].append(temp_track)
    album['tracks'].sort(key=lambda x: x['track_number'])
    save_artist_detail(artist)

    # 別スレッドでダウンロード処理を開始
    thread = threading.Thread(
        target=background_download_process,
        args=(artist_id, album_id, url, temp_track_id, current_track_num)
    )
    thread.start()

    # 即座に画面に戻る（処理は裏で続く）
    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))

@app.route('/artist/<artist_id>/album/<album_id>/track/<track_id>/edit', methods=['POST'])
@login_required
def edit_track(artist_id, album_id, track_id):
    artist = load_artist_detail(artist_id)
    album = next((a for a in artist['albums'] if a['id'] == album_id), None)
    if album:
        track = next((t for t in album['tracks'] if t['id'] == track_id), None)
        if track:
            track['title'] = request.form['title']
            try: track['track_number'] = int(request.form['track_number'])
            except: pass
            album['tracks'].sort(key=lambda x: x['track_number'])
            save_artist_detail(artist)
    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))

@app.route('/artist/<artist_id>/album/<album_id>/track/<track_id>/delete', methods=['POST'])
@login_required
def delete_track(artist_id, album_id, track_id):
    artist = load_artist_detail(artist_id)
    album = next((a for a in artist['albums'] if a['id'] == album_id), None)
    if album:
        track = next((t for t in album['tracks'] if t['id'] == track_id), None)
        if track:
            # 処理中のトラックも削除可能にする（スレッド側でエラーになるがtry-catchで無視される）
            if track.get('filename'):
                try: os.remove(os.path.join(app.config['MUSIC_FOLDER'], track['filename']))
                except: pass
            album['tracks'] = [t for t in album['tracks'] if t['id'] != track_id]
            save_artist_detail(artist)
    return redirect(url_for('view_album', artist_id=artist_id, album_id=album_id))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
