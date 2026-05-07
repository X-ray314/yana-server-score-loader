[README.md](https://github.com/user-attachments/files/27477986/README.md)
# yana-server-score-loader

**Sparebeat（スペアビート）非公式ファンサイト** — スコア管理・ランキング・譜面投稿・コミュニティ機能を備えた Webアプリケーション

> Sparebeat とは、日本の Web エンジニア suzukibakery 氏が制作したブラウザ向け 4 キーリズムゲームです。  
> 本サービスはその非公式コミュニティサイトとして、有志ユーザーによるスコア登録・管理・共有を目的として開発・運用しています。

---

## 📋 目次

- [機能一覧](#機能一覧)
- [技術スタック](#技術スタック)
- [システム構成](#システム構成)
- [ディレクトリ構成](#ディレクトリ構成)
- [セットアップ](#セットアップ)
- [主要エンドポイント](#主要エンドポイント)
- [レートシステム](#レートシステム)
- [開発経緯](#開発経緯)

---

## 機能一覧

### 🎵 スコア管理
- 難易度（★0〜★18）別の楽曲一覧表示
- スコアの一括登録・更新・削除
- スコアに基づくレート自動計算
- 理論値（1,000,000 点）達成スコアの特別表示

### 🏆 ランキング
- 楽曲別スコアランキング
- ユーザー総合レートランキング
- ユーザープロフィールページ（ベスト20・総合レート表示）
- ユーザー別平均順位・理論値取得数の集計

### 📊 統計ダッシュボード
- 全体スコア数・参加ユーザー数の集計
- 難易度別・レベル別スコア分布
- スコアティア分布（理論値 / ゴールド / シルバー / その他）
- レート分布グラフ・譜面別登録数 TOP20・単曲レート TOP30

### 📁 譜面管理
- 許可ユーザーによる譜面ファイル（JSON 形式）のアップロード・投稿
- 管理者による楽曲マスターデータの編集・削除
- 譜面更新ログ（最新 500 件保持）

### 🔓 解禁システム
- 特定スコア達成を条件とした楽曲ロック/アンロック機能
- `unlock_rules.json` による柔軟なルール設定

### 👤 ユーザー認証・管理
- Discord 連携による招待制会員登録（6 桁認証コード、5 分有効）
- bcrypt によるパスワードハッシュ化
- Flask-Login によるセッション管理
- ユーザーロールバッジ（スコア数・理論値数・譜面投稿数に応じて付与）

### 💬 リアルタイム通知
- WebSocket（Flask-SocketIO）による管理者からのリアルタイムブロードキャスト
- アニメーション付きメッセージ（フォントサイズ・カラー・グラデーション・方向・回転など細かくカスタマイズ可能）

### 🛠 ツール・その他
- osu!mania 4K → Sparebeat 譜面コンバーター
- JSON プレビュー・エディタ
- ミニゲーム（Blueprint 分離）
- クイズ機能（Blueprint 分離）
- `/sambo`・`/cube` などのサブページ

---

## 技術スタック

| カテゴリ | 使用技術 |
|---|---|
| **バックエンド** | Python 3 / Flask |
| **データベース** | SQLite（SQLAlchemy ORM） |
| **認証** | Flask-Login / Flask-Bcrypt |
| **リアルタイム通信** | Flask-SocketIO（WebSocket） |
| **フロントエンド** | HTML / CSS / JavaScript（Jinja2 テンプレート） |
| **Web サーバー** | Nginx（リバースプロキシ） + Gunicorn（WSGI） |
| **インフラ** | ConoHa VPS（Ubuntu） |
| **データ形式** | JSON（楽曲マスター・ログ・統計キャッシュ） |

---

## システム構成

```
ユーザー (ブラウザ)
     │
     ▼
  Nginx :80/:443
  （リバースプロキシ）
     │
     ▼
  Gunicorn :8000
  （WSGI サーバー）
     │
     ▼
  Flask Application (app.py)
  ├── users.db    （ユーザー・スコア情報）
  ├── auth.db     （Discord 連携認証トークン）
  ├── minigame.db （ミニゲームデータ）
  ├── songs.json  （楽曲マスターデータ）
  ├── user_stats.json  （統計キャッシュ）
  └── song_master_updates.json （更新ログ）
```

---

## ディレクトリ構成

```
yana-server-score-loader/
├── app.py                  # メインアプリケーション
├── songs.json              # 楽曲マスターデータ
├── unlock_rules.json       # 楽曲解禁ルール
├── static/
│   └── charts/             # アップロードされた譜面ファイル
├── templates/              # Jinja2 HTML テンプレート
│   ├── index.html
│   ├── songs.html
│   ├── ranking.html
│   ├── mypage.html
│   ├── statistics.html
│   └── ...
├── Ex_Routes/
│   ├── quiz/               # クイズ機能（Blueprint）
│   └── mini_game/          # ミニゲーム機能（Blueprint）
└── instance/
    ├── users.db
    ├── auth.db
    ├── user_stats.json
    └── song_master_updates.json
```

---

## セットアップ

### 必要環境

- Python 3.10 以上
- pip
- Nginx
- Gunicorn

### インストール

```bash
# リポジトリをクローン
git clone https://github.com/X-ray314/yana-server-score-loader.git
cd yana-server-score-loader

# 依存パッケージをインストール
pip install flask flask_sqlalchemy flask_login flask_socketio flask_bcrypt

# データベースを初期化してアプリを起動（開発用）
python app.py
```

### 本番環境（Gunicorn + Nginx）

```bash
# Gunicorn で起動
gunicorn -w 4 -b 127.0.0.1:8000 "app:create_app()" --worker-class eventlet

# Nginx 設定例（抜粋）
# server {
#     listen 80;
#     location / {
#         proxy_pass http://127.0.0.1:8000;
#         proxy_set_header Upgrade $http_upgrade;
#         proxy_set_header Connection "upgrade";
#     }
# }
```

---

## 主要エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/` | トップページ（おすすめ楽曲・更新ログ） |
| GET | `/songs` | 楽曲一覧（難易度選択） |
| GET | `/songs/<level>` | 難易度別楽曲一覧 |
| GET | `/songs/all` | 全楽曲一覧（ページネーション） |
| POST | `/add_score_bulk` | スコア一括登録・更新 |
| GET | `/song/<song_id>` | 楽曲別スコアランキング |
| GET | `/user/<username>` | ユーザープロフィール |
| GET | `/total-ranking` | 総合レートランキング |
| GET | `/statistics` | 統計ダッシュボード |
| GET/POST | `/upload_new_song` | 譜面ファイル投稿（許可ユーザーのみ） |
| GET/POST | `/admin/edit_songs` | 楽曲マスター編集（管理者のみ） |
| GET | `/converter` | osu!mania → Sparebeat コンバーター |
| GET | `/admin/broadcast` | リアルタイム通知送信（管理者のみ） |

---

## レートシステム

スコアと楽曲レベル（★0〜★18）からレートを算出します。

```
スコア閾値: 950,000 / 955,000 / ... / 1,000,000（12段階）
各レベルに閾値ごとのレートテーブルを定義 → 線形補間で算出
```

- ベスト 20 譜面のレートの合計が「総合レート」
- 理論値（1,000,000 点）で最高レートを獲得

---

## 開発経緯

2024 年 12 月後半、スペアビートの Discord コミュニティ内でスコア共有・管理の場を作りたいという動機から個人開発を開始。コーディング経験 0 の状態から AI を活用しながら設計・実装を進め、2025 年 2 月に ConoHa VPS 上で本番稼働を開始。

---

## ライセンス

本プロジェクトは非公式ファンサイトです。Sparebeat の著作権は suzukibakery 氏に帰属します。
