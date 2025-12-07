
# Music Server Setup

## 1. インストール
**必須ツール (FFmpeg) のインストール:**
*   Linux: `sudo apt install ffmpeg`
*   Mac: `brew install ffmpeg`

**ライブラリのインストール:**
```bash
pip install flask flask-cors yt-dlp ffmpeg
```

## 2. app.py の修正 (サブディレクトリ運用時のみ)
`/music` 等のパスで運用する場合のみ、`app.py` の `app = Flask(__name__)` の直後に以下を追加してください。

```python
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

# URLのプレフィックス ("/music") を設定
app.wsgi_app = PrefixMiddleware(app.wsgi_app, "/music")
```

## 3. 起動
```bash
python app.py
```
(ブラウザで `http://localhost:5000/music/admin/` にアクセス)

## 4. ログイン情報
*   **User:** `admin`
*   **Pass:** `123456`
```

---

### 2. Nginx設定 (オプション)
Nginxを使う場合の設定例です。使わなくても動作します。

```nginx
server {
    listen 80;
    server_name your-domain.com; 

    # 動画アップロード用にサイズ制限を緩和
    client_max_body_size 500M;

    location /music {
        # python app.py で起動しているポートへ
        proxy_pass http://127.0.0.1:5000;
        
        # 必須ヘッダー
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # タイムアウト延長 (変換・DL待ち用)
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
