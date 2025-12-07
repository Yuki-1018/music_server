# 1. README.md

プロジェクトのルートディレクトリに保存してください。

```markdown
# Personal Music Streaming Server

自分専用の音楽ストリーミングサーバーです。
Flaskをベースに構築されており、JSONファイルでデータベースレスに管理します。

## 機能
- **管理画面**: ブラウザからアーティスト、アルバム、楽曲の追加・編集・削除が可能（レスポンシブ対応）。
- **認証機能**: Basic認証による保護。
- **音楽再生**: ブラウザ上でのストリーミング再生。
- **YouTubeインポート**: URLを指定してYouTubeから最高音質(320kbps MP3)でダウンロード・インポート。
- **動画変換**: アップロードされた動画ファイル(mp4等)を自動で音声ファイル(mp3)に変換。
- **API**: クライアントアプリ向けのJSON APIを提供。

## 必要要件
- Python 3.8以上
- **FFmpeg** (動画変換・インポート処理に必須)

## インストール手順

### 1. FFmpegのインストール
サーバー上で必ずFFmpegをインストールし、パスを通してください。

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**CentOS/RHEL:**
```bash
sudo dnf install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

### 2. Pythonライブラリのインストール
プロジェクトフォルダ内で以下のコマンドを実行します。

```bash
pip install flask flask-cors yt-dlp gunicorn
```

## 設定変更（サブディレクトリでの運用）

`/music` などのサブディレクトリでこのアプリを公開する場合（例: `https://example.com/music/admin/`）、`app.py` に以下のミドルウェア設定を追加することを強く推奨します。

**app.py の修正:**
`app = Flask(__name__)` の直後あたりに以下のコードを挿入してください。

```python
# --- Subdirectory Middleware ---
class PrefixMiddleware(object):
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix
    def __call__(self, environ, start_response):
        if environ['PATH_INFO'].startswith(self.prefix):
            environ['PATH_INFO'] = environ['PATH_INFO'][len(self.prefix):]
            environ['SCRIPT_NAME'] = self.prefix
            return self.app(environ, start_response)
        else:
            start_response('404', [('Content-Type', 'text/plain')])
            return [b"Not Found"]

# ここでURLプレフィックスを設定 ("/music" の場合)
app.wsgi_app = PrefixMiddleware(app.wsgi_app, "/music")
```

## 起動方法

### 開発用サーバー（デバッグ）
```bash
python app.py
```
`http://localhost:5000/music/admin/` でアクセスできます。

### 本番運用 (Gunicorn)
```bash
gunicorn -w 4 -b 127.0.0.1:5000 app:app
```

## ディレクトリ構成
- `data/`: データベース用JSONファイル（自動生成）
- `music/`: 音楽ファイル（自動生成）
- `images/`: ジャケット画像（自動生成）
- `templates/`: HTMLテンプレート

## 管理画面ログイン情報
初期設定の認証情報は `app.py` 内で定義されています。
- **ユーザー名**: `admin`
- **パスワード**: `123456`

※ `app.py` 内の `ADMIN_USERNAME` と `ADMIN_PASSWORD` を書き換えて使用してください。
```

---

# 2. app.py への追加コード

ユーザー様より提示いただいたコードは非常に有効です。これを `app.py` に組み込んだ形は以下のようになります。
（`CORS(app)` の直後、設定ブロックの前あたりに入れるのが適切です）

```python
# ... (imports) ...

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------
# 【推奨設定】サブディレクトリ (/music) で運用するための設定
# Nginx等で http://domain.com/music/ として公開する場合に必要です。
# url_for が自動的に /music を付与してURLを生成するようになります。
# ---------------------------------------------------------
class PrefixMiddleware(object):
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        # SCRIPT_NAME をセットすることで Flask がルートパスを認識する
        if environ['PATH_INFO'].startswith(self.prefix):
            environ['PATH_INFO'] = environ['PATH_INFO'][len(self.prefix):]
            environ['SCRIPT_NAME'] = self.prefix
            return self.app(environ, start_response)
        else:
            # プレフィックスなしでアクセスされた場合は404等を返す
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b"Not Found"]

# 下記 "/music" を Nginx の location 設定と合わせてください
app.wsgi_app = PrefixMiddleware(app.wsgi_app, "/music")

# HTTPS強制のためのProxyFix（前回追加分）
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ... (以下、app.config 設定などが続く) ...
```

---

# 3. Nginx 設定ファイル

`/etc/nginx/sites-available/music-server` などとして作成します。
Pythonアプリは Gunicorn を使って `127.0.0.1:5000` で動いていると仮定します。

```nginx
server {
    listen 80;
    server_name your-domain.com; # 自分のドメインに変更

    # アップロードサイズ制限の緩和（動画ファイル等を上げるため必須）
    client_max_body_size 200M;

    # /music/ へのアクセスを Flask アプリへ転送
    location /music {
        # Gunicorn へのプロキシ
        proxy_pass http://127.0.0.1:5000;

        # ヘッダー情報の転送 (Flaskが正しいURLを生成するために必要)
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # タイムアウト設定の延長 (YouTubeダウンロードや変換処理待ち用)
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
    }

    # (オプション) 静的ファイルへの直接アクセスによる高速化
    # Flaskを通さずにNginxから直接配信する場合
    # location /music/stream/ {
    #     alias /path/to/your/app/music/;
    # }
    # location /music/image/ {
    #     alias /path/to/your/app/images/;
    # }
}
```

### 運用のポイント
1.  **タイムアウト**: YouTubeからのダウンロードや動画変換は時間がかかるため、Nginxの `proxy_read_timeout` を長め（例: 300秒）に設定しています。
2.  **ファイルサイズ**: `client_max_body_size` を大きめ（例: 200M）に設定しないと、大きな動画や音声ファイルのアップロード時に Nginx でエラー(413 Payload Too Large)になります。
