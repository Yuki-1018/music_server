import os
import json
import uuid
import threading
import subprocess
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify, Response, session
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config['BASE_DIR'] = os.path.dirname(os.path.abspath(__file__))
app.config['MUSIC_FOLDER'] = os.path.join(app.config['BASE_DIR'], 'music')
app.config['IMAGES_FOLDER'] = os.path.join(app.config['BASE_DIR'], 'images')
app.config['DATA_FOLDER'] = os.path.join(app.config['BASE_DIR'], 'data')
app.config['ARTISTS_FOLDER'] = os.path.join(app.config['DATA_FOLDER'], 'artists')
app.config['ALBUMS_FOLDER'] = os.path.join(app.config['DATA_FOLDER'], 'albums')
app.config['INDEX_FILE'] = os.path.join(app.config['DATA_FOLDER'], 'index.json')
app.config['UPLOAD_TEMP'] = os.path.join(app.config['BASE_DIR'], 'temp_upload')

app.secret_key = 'super_secret_key_change_me'

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = '123456'

ALLOWED_EXTENSIONS_IMG = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_EXTENSIONS_AUDIO = {'mp3', 'wav', 'm4a', 'aac', 'flac', 'mp4', 'mov', 'webm', 'mkv'}

for folder in [app.config['MUSIC_FOLDER'], app.config['IMAGES_FOLDER'], app.config['DATA_FOLDER'], 
               app.config['ARTISTS_FOLDER'], app.config['ALBUMS_FOLDER'], app.config['UPLOAD_TEMP']]:
    if not os.path.exists(folder):
        os.makedirs(folder)

if not os.path.exists(app.config['INDEX_FILE']):
    with open(app.config['INDEX_FILE'], 'w', encoding='utf-8') as f:
        json.dump([], f)

def check_auth(username, password):
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    return Response(
        '認証が必要です', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def load_index():
    try:
        with open(app.config['INDEX_FILE'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_index(data):
    with open(app.config['INDEX_FILE'], 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_artist(artist_id):
    filepath = os.path.join(app.config['ARTISTS_FOLDER'], f"{artist_id}.json")
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_artist(artist_data):
    filepath = os.path.join(app.config['ARTISTS_FOLDER'], f"{artist_data['id']}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(artist_data, f, indent=4, ensure_ascii=False)

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
    filepath = os.path.join(app.config['ALBUMS_FOLDER'], f"{album_id}.json")
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_album(album_data):
    filepath = os.path.join(app.config['ALBUMS_FOLDER'], f"{album_data['id']}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(album_data, f, indent=4, ensure_ascii=False)

def delete_artist_data(artist_id):
    artist = load_artist(artist_id)
    if artist:
        for alb_ref in artist['albums']:
            alb_path = os.path.join(app.config['ALBUMS_FOLDER'], f"{alb_ref['id']}.json")
            if os.path.exists(alb_path): os.remove(alb_path)
        art_path = os.path.join(app.config['ARTISTS_FOLDER'], f"{artist_id}.json")
        if os.path.exists(art_path): os.remove(art_path)
    
    index_data = load_index()
    index_data = [a for a in index_data if a['id'] != artist_id]
    save_index(index_data)

def delete_album_data(artist_id, album_id):
    alb_path = os.path.join(app.config['ALBUMS_FOLDER'], f"{album_id}.json")
    if os.path.exists(alb_path): os.remove(alb_path)
    artist = load_artist(artist_id)
    if artist:
        artist['albums'] = [a for a in artist['albums'] if a['id'] != album_id]
        save_artist(artist)

def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_IMG

def allowed_audio(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_AUDIO

def save_image_file(file):
    if file and allowed_image(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(app.config['IMAGES_FOLDER'], filename))
        return filename
    return None

def process_upload_file(file):
    filename = secure_filename(file.filename)
    base_id = uuid.uuid4().hex
    temp_path = os.path.join(app.config['UPLOAD_TEMP'], f"{base_id}_{filename}")
    file.save(temp_path)

    final_filename = f"{base_id}.mp3"
    hq_path = os.path.join(app.config['MUSIC_FOLDER'], final_filename)
    
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', temp_path,
            '-b:a', '320k', '-map', 'a',
            hq_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Conversion error: {e}")
        if os.path.exists(temp_path): os.remove(temp_path)
        return None
    
    if os.path.exists(temp_path): os.remove(temp_path)
    return final_filename

def background_download_process(album_id, url, temp_track_id, start_track_num):
    try:
        ydl_opts_info = {'quiet': True, 'extract_flat': 'in_playlist', 'ignoreerrors': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        if 'entries' in info: entries = list(info['entries'])
        else: entries = [info]

        album = load_album(album_id)
        if not album: return

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
                "title": f"【待機中】 {title}",
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

        ydl_opts_dl = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            'quiet': True,
            'ignoreerrors': True
        }

        for item in download_queue:
            album = load_album(album_id)
            if not album: break
            
            target_track = next((t for t in album['tracks'] if t['id'] == item['id']), None)
            if not target_track: continue

            target_track['title'] = f"【DL中...】 {item['title'].replace('【待機中】 ', '')}"
            save_album(album)

            try:
                base_id = uuid.uuid4().hex
                save_path_base = os.path.join(app.config['MUSIC_FOLDER'], base_id)
                current_opts = ydl_opts_dl.copy()
                current_opts['outtmpl'] = save_path_base

                with yt_dlp.YoutubeDL(current_opts) as ydl:
                    dl_info = ydl.extract_info(item['original_url'], download=True)
                    real_title = dl_info.get('title', 'Unknown Title')

                hq_file = f"{base_id}.mp3"

                target_track['title'] = real_title
                target_track['filename'] = hq_file
                if 'processing' in target_track: del target_track['processing']
                save_album(album)

            except Exception as e:
                target_track['title'] = f"【エラー】 {item['title']}"
                if 'processing' in target_track: del target_track['processing']
                save_album(album)

    except Exception as e:
        print(f"Background process error: {e}")

@app.route('/stream/<path:filename>')
def stream_music(filename):
    return send_from_directory(app.config['MUSIC_FOLDER'], filename)

@app.route('/image/<path:filename>')
def serve_image(filename):
    return send_from_directory(app.config['IMAGES_FOLDER'], filename)

@app.route('/api/artists')
def api_get_artists():
    data = load_index()
    for artist in data:
        if artist.get('image'):
            artist['image_url'] = url_for('serve_image', filename=artist['image'], _external=True, _scheme='https')
        artist['api_url'] = url_for('api_get_artist_detail', artist_id=artist['id'], _external=True, _scheme='https')
    return jsonify(data)

@app.route('/api/artist/<artist_id>')
def api_get_artist_detail(artist_id):
    artist = load_artist(artist_id)
    if not artist: return jsonify({"error": "Artist not found"}), 404
    
    if artist.get('image'):
        artist['image_url'] = url_for('serve_image', filename=artist['image'], _external=True, _scheme='https')

    for album in artist['albums']:
        if album.get('cover_image'):
            album['cover_url'] = url_for('serve_image', filename=album['cover_image'], _external=True, _scheme='https')
        album['api_url'] = url_for('api_get_album_detail', album_id=album['id'], _external=True, _scheme='https')

    return jsonify(artist)

@app.route('/api/album/<album_id>')
def api_get_album_detail(album_id):
    album = load_album(album_id)
    if not album: return jsonify({"error": "Album not found"}), 404

    if album.get('cover_image'):
        album['cover_url'] = url_for('serve_image', filename=album['cover_image'], _external=True, _scheme='https')

    for track in album['tracks']:
        if not track.get('processing') and track.get('filename'):
            track['stream_url'] = url_for('stream_music', filename=track['filename'], _external=True, _scheme='https')
        track['cover_url'] = album.get('cover_url')

    return jsonify(album)

@app.route('/')
def root_redirect():
    return redirect('/admin/')

@app.route('/admin/')
@requires_auth
def admin_index():
    return render_template('index.html', artists=load_index())

@app.route('/admin/artist/add', methods=['POST'])
@requires_auth
def admin_add_artist():
    img_filename = save_image_file(request.files.get('image'))
    new_artist = {
        "id": str(uuid.uuid4()),
        "name": request.form['name'],
        "genre": request.form.get('genre', ''),
        "description": request.form.get('description', ''),
        "image": img_filename,
        "albums": []
    }
    save_artist(new_artist)
    return redirect(url_for('admin_index'))

@app.route('/admin/artist/<artist_id>/edit', methods=['POST'])
@requires_auth
def admin_edit_artist(artist_id):
    artist = load_artist(artist_id)
    if artist:
        artist['name'] = request.form['name']
        artist['genre'] = request.form['genre']
        artist['description'] = request.form['description']
        new_img = save_image_file(request.files.get('image'))
        if new_img: artist['image'] = new_img
        save_artist(artist)
    return redirect(url_for('admin_index'))

@app.route('/admin/artist/<artist_id>/delete', methods=['POST'])
@requires_auth
def admin_delete_artist(artist_id):
    delete_artist_data(artist_id)
    return redirect(url_for('admin_index'))

@app.route('/admin/artist/<artist_id>')
@requires_auth
def admin_view_artist(artist_id):
    artist = load_artist(artist_id)
    if not artist: return "見つかりませんでした", 404
    return render_template('artist.html', artist=artist)

@app.route('/admin/artist/<artist_id>/album/add', methods=['POST'])
@requires_auth
def admin_add_album(artist_id):
    artist = load_artist(artist_id)
    if artist:
        album_id = str(uuid.uuid4())
        img_filename = save_image_file(request.files.get('image'))
        
        album_ref = {
            "id": album_id,
            "title": request.form['title'],
            "year": request.form.get('year', ''),
            "type": request.form.get('type', 'Album'),
            "cover_image": img_filename
        }
        artist['albums'].append(album_ref)
        save_artist(artist)

        new_album_detail = {
            "id": album_id,
            "artist_id": artist_id,
            "artist_name": artist['name'],
            "title": request.form['title'],
            "year": request.form.get('year', ''),
            "type": request.form.get('type', 'Album'),
            "cover_image": img_filename,
            "tracks": []
        }
        save_album(new_album_detail)

    return redirect(url_for('admin_view_artist', artist_id=artist_id))

@app.route('/admin/artist/<artist_id>/album/<album_id>/edit', methods=['POST'])
@requires_auth
def admin_edit_album(artist_id, album_id):
    artist = load_artist(artist_id)
    album_detail = load_album(album_id)

    if artist and album_detail:
        title = request.form['title']
        year = request.form['year']
        atype = request.form['type']
        new_img = save_image_file(request.files.get('image'))

        for ref in artist['albums']:
            if ref['id'] == album_id:
                ref['title'] = title
                ref['year'] = year
                ref['type'] = atype
                if new_img: ref['cover_image'] = new_img
                break
        save_artist(artist)

        album_detail['title'] = title
        album_detail['year'] = year
        album_detail['type'] = atype
        if new_img: album_detail['cover_image'] = new_img
        save_album(album_detail)

    return redirect(url_for('admin_view_artist', artist_id=artist_id))

@app.route('/admin/artist/<artist_id>/album/<album_id>/delete', methods=['POST'])
@requires_auth
def admin_delete_album(artist_id, album_id):
    delete_album_data(artist_id, album_id)
    return redirect(url_for('admin_view_artist', artist_id=artist_id))

@app.route('/admin/artist/<artist_id>/album/<album_id>')
@requires_auth
def admin_view_album(artist_id, album_id):
    artist = load_artist(artist_id)
    album = load_album(album_id)
    if not artist or not album: return "見つかりませんでした", 404
    return render_template('album.html', artist=artist, album=album)

@app.route('/admin/artist/<artist_id>/album/<album_id>/track/add', methods=['POST'])
@requires_auth
def admin_add_track(artist_id, album_id):
    if 'file' not in request.files: return "ファイルがありません", 400
    file = request.files['file']
    if file.filename == '' or not file: return "ファイルが選択されていません", 400
    if not allowed_audio(file.filename): return "対応していない形式です", 400

    final_filename = process_upload_file(file)
    if not final_filename:
        return "変換に失敗しました", 500

    album = load_album(album_id)
    if album:
        track_num = request.form.get('track_number')
        if not track_num: track_num = len(album['tracks']) + 1

        new_track = {
            "id": str(uuid.uuid4()),
            "title": request.form.get('title') or file.filename,
            "track_number": int(track_num),
            "filename": final_filename
        }
        album['tracks'].append(new_track)
        album['tracks'].sort(key=lambda x: x['track_number'])
        save_album(album)

    return redirect(url_for('admin_view_album', artist_id=artist_id, album_id=album_id))

@app.route('/admin/artist/<artist_id>/album/<album_id>/track/add_url', methods=['POST'])
@requires_auth
def admin_add_track_url(artist_id, album_id):
    url = request.form.get('url')
    if not url: return "URLがありません", 400

    album = load_album(album_id)
    if not album: return "アルバムが見つかりません", 404

    track_start_num = request.form.get('track_number')
    if track_start_num:
        current_track_num = int(track_start_num)
    else:
        current_track_num = len(album['tracks']) + 1

    temp_track_id = str(uuid.uuid4())
    temp_track = {
        "id": temp_track_id,
        "title": "インポート準備中...",
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

    return redirect(url_for('admin_view_album', artist_id=artist_id, album_id=album_id))

@app.route('/admin/artist/<artist_id>/album/<album_id>/track/<track_id>/edit', methods=['POST'])
@requires_auth
def admin_edit_track(artist_id, album_id, track_id):
    album = load_album(album_id)
    if album:
        track = next((t for t in album['tracks'] if t['id'] == track_id), None)
        if track:
            track['title'] = request.form['title']
            try: track['track_number'] = int(request.form['track_number'])
            except: pass
            album['tracks'].sort(key=lambda x: x['track_number'])
            save_album(album)
    return redirect(url_for('admin_view_album', artist_id=artist_id, album_id=album_id))

@app.route('/admin/artist/<artist_id>/album/<album_id>/track/<track_id>/delete', methods=['POST'])
@requires_auth
def admin_delete_track(artist_id, album_id, track_id):
    album = load_album(album_id)
    if album:
        track = next((t for t in album['tracks'] if t['id'] == track_id), None)
        if track:
            if track.get('filename'):
                try: os.remove(os.path.join(app.config['MUSIC_FOLDER'], track['filename']))
                except: pass
            album['tracks'] = [t for t in album['tracks'] if t['id'] != track_id]
            save_album(album)
    return redirect(url_for('admin_view_album', artist_id=artist_id, album_id=album_id))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
