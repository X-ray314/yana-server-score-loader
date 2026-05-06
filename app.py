# 機能の読み込み
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import json
from flask_socketio import SocketIO
from flask_socketio import join_room, emit
import re
import os
import random
from sqlalchemy import func
from flask_bcrypt import Bcrypt
from collections import Counter, defaultdict
# bpの登録
from Ex_Routes.quiz.quiz import quiz_bp, init_quiz_db
from Ex_Routes.mini_game.mini_game import mini_game_bp, init_mini_game_db
from datetime import datetime, timedelta
app = Flask(__name__)
socketio = SocketIO(app)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'develop-key-temporary')
 
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance') 
if not os.path.exists(instance_path): 
    os.makedirs(instance_path) 

SONG_MASTER_UPDATES_PATH = os.path.join(instance_path, 'song_master_updates.json')

SONG_UPDATE_FIELD_LABELS = {
    'name': '曲名',
    'difficulty': '難易度',
    'star': 'レベル(★)',
    'youtube_url': 'YouTube URL',
    'chart_file': '譜面ファイル',
    'creator': '製作者',
}


def _norm_field_val(v):
    if v is None:
        return ''
    return str(v).strip()


def _diff_song_records(before: dict, after: dict):
    changes = []
    for key, label in SONG_UPDATE_FIELD_LABELS.items():
        b = _norm_field_val(before.get(key))
        a = _norm_field_val(after.get(key))
        if b != a:
            changes.append({
                'field': key,
                'label': label,
                'before': b if b else '(空)',
                'after': a if a else '(空)',
            })
    return changes


def _load_song_master_updates():
    if not os.path.exists(SONG_MASTER_UPDATES_PATH):
        return []
    try:
        with open(SONG_MASTER_UPDATES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"song_master_updates load error: {e}")
        return []


def _save_song_master_updates(entries):
    with open(SONG_MASTER_UPDATES_PATH, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def append_song_master_update_log(song_id, song_name, action, changes, at=None):
    """
    changes: list of {field, label, before, after}
    action: 'update' | 'delete' | 'create'（新規譜面投稿）
    """
    entries = _load_song_master_updates()
    at = at or datetime.utcnow()
    entry = {
        'id': f"{int(at.timestamp() * 1000)}-{song_id}-{action}",
        'at': at.isoformat(timespec='seconds'),
        'song_id': song_id,
        'song_name': song_name or '',
        'action': action,
        'changes': changes or [],
    }
    entries.append(entry)
    if len(entries) > 500:
        entries = entries[-500:]
    _save_song_master_updates(entries)


def get_recent_song_master_updates(days=None, max_items=10):
    """最新から最大 max_items 件（新しい順）。days を指定したときだけその日数より古いものを除く。"""
    entries = _load_song_master_updates()
    if not entries:
        return []
    cutoff = datetime.utcnow() - timedelta(days=days) if days is not None else None
    out = []
    for e in reversed(entries):
        try:
            ts = datetime.fromisoformat(e.get('at', ''))
        except (TypeError, ValueError):
            continue
        if cutoff is not None and ts < cutoff:
            continue
        out.append(e)
        if len(out) >= max_items:
            break
    # テンプレ用: 難易度ページへのリンク（変更が star 以外でも現在のマスタから補完）
    for e in out:
        link_lv = ''
        if e.get('action') in ('update', 'create'):
            for ch in e.get('changes') or []:
                if ch.get('field') == 'star':
                    link_lv = _norm_field_val(ch.get('after')).replace('★', '')
                    break
            if not link_lv:
                sid = e.get('song_id')
                cur = next((s for s in SONGS_MASTER_DATA if s.get('song_id') == sid), None)
                if cur:
                    link_lv = _norm_field_val(cur.get('star', '')).replace('★', '')
        e['link_level'] = link_lv
    return out

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(instance_path, 'users.db')
# app.py の上部、config設定部分
mini_game_instance_path = os.path.join(app.root_path, 'Ex_Routes', 'mini_game', 'instance')
app.config['SQLALCHEMY_BINDS'] = {
    'auth': 'sqlite:///' + os.path.join(instance_path, 'auth.db'),
    'minigame': 'sqlite:///' + os.path.join(mini_game_instance_path, 'minigame.db')
}

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 200,
}

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

app.register_blueprint(quiz_bp)
app.register_blueprint(mini_game_bp)
init_quiz_db(app) 
init_mini_game_db(app, db)



login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,   # 接続が生きているか事前に確認するm
    "pool_recycle": 200,     # 5分（300秒）ごとに接続をリフレッシュする
}
basedir = os.path.abspath(os.path.dirname(__file__))

try:
    with open(os.path.join(basedir, 'songs.json'), 'r', encoding='utf-8') as f:
        SONGS_MASTER_DATA = json.load(f)
except Exception as e:
    print(f"JSON読み込みエラー: {e}")
    SONGS_MASTER_DATA = []
# --- ユーザーモデル定義 (db.create_allより前に記述) ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False) 
    scores = db.relationship('Score', backref='user', lazy=True)

class Score(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    song_id = db.Column(db.String(50), nullable=False)  # song_name から song_id へ変更
    score_val = db.Column(db.Integer, nullable=False)
# モデルの定義
class RegistrationToken(db.Model):
    __bind_key__ = 'auth'  # auth.db を使う指定
    id = db.Column(db.Integer, primary_key=True)
    discord_username = db.Column(db.String(100), nullable=False)
    token = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

USER_STATS_PATH = os.path.join(instance_path, 'user_stats.json')

def load_user_stats():
    if not os.path.exists(USER_STATS_PATH):
        return {}
    with open(USER_STATS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_user_stats(stats):
    with open(USER_STATS_PATH, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=4)

def refresh_user_stats(user_id):
    """DBから最新の数値を計算してJSONを更新する（整合性を保つため）"""
    stats = load_user_stats()
    uid_str = str(user_id)
    
    user = User.query.get(user_id)
    if not user: return

    score_count = Score.query.filter_by(user_id=user_id).count()
    perfect_count = Score.query.filter_by(user_id=user_id).filter(Score.score_val >= 1000000).count()
    mapped_count = sum(1 for s in SONGS_MASTER_DATA if s.get('creator') == user.username)

    stats[uid_str] = {
        "score_count": score_count,
        "perfect_count": perfect_count,
        "mapped_count": mapped_count
    }
    save_user_stats(stats)

# --- 3. データベーステーブルの作成 (gunicornでも確実に実行される場所) ---
with app.app_context():
    db.create_all()
    print("Database tables initialized successfully!")

# --- 以下、レートシステムやルーティング（変更なし） ---

SCORE_THRESHOLDS = [0, 950000, 955000, 960000, 965000, 970000, 975000, 980000, 985000, 990000, 995000, 1000000]
LEVEL_RATE_TABLE = {
    0:  [-169, 21, 21.5, 22, 22.5, 23, 23.5, 24, 24.5, 25, 25.25, 25.25],
    1:  [-168, 22, 22.5, 23, 23.5, 24, 24.5, 25, 25.5, 26, 26.25, 26.25],
    2:  [-167, 23, 23.5, 24, 24.5, 25, 25.5, 26, 26.5, 27, 27.25, 27.25],
    3:  [-166, 24, 24.5, 25, 25.5, 26, 26.5, 27, 27.5, 28, 28.25, 28.25],
    4:  [-165, 25, 25.5, 26, 26.5, 27, 27.5, 28, 28.5, 29, 29.25, 29.25],
    5:  [-164, 26, 26.5, 27, 27.5, 28, 28.5, 29, 29.5, 30, 30.25, 30.25],
    6:  [-163, 27, 27.5, 28, 28.5, 29, 29.5, 30, 30.5, 31, 31.25, 31.25],
    7:  [-162, 28, 28.5, 29, 29.5, 30, 30.5, 31, 31.25, 31.5, 31.75, 31.75],
    8:  [-161, 29, 29.5, 30, 30.5, 31, 31.25, 31.5, 31.75, 32, 32.5, 32.5],
    9:  [-160, 30, 30.5, 31, 31.25, 31.5, 31.75, 32, 32.5, 33, 33.25, 33.25],
    10: [-159, 31, 31.25, 31.5, 31.75, 32, 32.5, 33, 33.25, 33.5, 33.75, 33.75],
    11: [-158.5, 31.5, 31.75, 32, 32.5, 33, 33.25, 33.5, 33.75, 34, 34.5, 34.5],
    12: [-158, 32, 32.5, 33, 33.25, 33.5, 33.75, 34, 34.5, 35, 35.25, 35.25],
    13: [-157, 33, 33.5, 34, 34.25, 34.5, 34.75, 35, 35.5, 36, 36.25, 36.25],
    14: [-156, 34, 34.25, 34.5, 34.75, 35, 35.5, 36, 36.33333333, 36.6666666, 37, 37],
    15: [-155.25, 34.75, 35, 35.5, 36, 36.33333333, 36.6666666, 37, 37.25, 37.5, 38, 38],
    16: [-154.5, 35.5, 36, 36.33333333, 36.6666666, 37, 37.5, 38, 38.5, 39, 39.5, 39.5],
    17: [-153.3333334, 36.6666666, 37, 37.5, 38, 38.5, 39, 39.5, 40, 40.5, 41, 41],
    18: [-152, 38, 38.5, 39, 39.5, 40, 40.5, 41, 41.5, 42, 42.5, 42.5],
}

def calculate_rate(score, level_star):
    if level_star == "★?":
        return 0
    try:
        level = int(level_star.replace('★', ''))
    except:
        return 0
    if level not in LEVEL_RATE_TABLE:
        return 0
    rates = LEVEL_RATE_TABLE[level]
    idx = 0
    for i in range(len(SCORE_THRESHOLDS) - 1):
        if SCORE_THRESHOLDS[i] <= score < SCORE_THRESHOLDS[i+1]:
            idx = i
            break 
    else:
        if score >= 1000000: return rates[-1]
        return 0
    s_low, s_high = SCORE_THRESHOLDS[idx], SCORE_THRESHOLDS[idx+1]
    r_low, r_high = rates[idx], rates[idx+1]
    rate = r_low + (r_high - r_low) * (score - s_low) / (s_high - s_low)
    return max(round(rate, 2), 0)

def get_user_profile_data(user):
    songs_data = SONGS_MASTER_DATA 
    song_info_dict = {s['song_id']: s['star'] for s in songs_data}
    song_name_dict = {s['song_id']: s['name'] for s in songs_data}
    my_scores = Score.query.filter_by(user_id=user.id).all()
    
    scored_list = []
    for s in my_scores:
        star_str = song_info_dict.get(s.song_id)
        name_str = song_name_dict.get(s.song_id, "Unknown")
        
        # --- 追加：★を除いたレベル数字を取得 ---
        # "★10" なら "10"、"★?" なら "?" になります
        level_num = star_str.replace('★', '') if star_str else "0"
        
        actual_rate = calculate_rate(s.score_val, star_str) if star_str else 0
        scored_list.append({
            'song_id': s.song_id,           
            'song_name': name_str,
            'score': s.score_val, 
            'rate': actual_rate,
            'level_num': level_num  # ← これを辞書に加える
        })
    
    scored_list.sort(key=lambda x: x['rate'], reverse=True)
    best_20 = scored_list[:20]
    display_rate = round(sum(item['rate'] for item in best_20), 2)
    
    return {
        "user": user, 
        "best_20": best_20, 
        "total_count": len(my_scores), 
        "total_rate": display_rate
    }
CHARTS_DIR = os.path.join('static', 'charts')
if not os.path.exists(CHARTS_DIR):
    os.makedirs(CHARTS_DIR)
def ocr_extract_score(img_path):
    # OCRを外したので、とりあえず固定値を返すようにします
    # 後でここを手入力フォームなどに変えると良いです！
    return 100
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.route("/")
def index():
    all_users = User.query.all()
    recommended_song = random.choice(SONGS_MASTER_DATA) if SONGS_MASTER_DATA else None
    recommended_song_level = None
    if recommended_song:
        recommended_song_level = recommended_song.get("star", "").replace("★", "")
    return render_template(
        "index.html",
        users=all_users,
        recommended_song=recommended_song,
        recommended_song_level=recommended_song_level,
        recent_song_master_updates=get_recent_song_master_updates(max_items=10),
    )

@app.route("/about")
def about():
    return render_template("about.html", title="このサイトについて")
@app.route("/JSON-EDIT")
def json_edit():
    return render_template("JSON-EDIT.html", title="Json-Preview-Editor // X-ray")
@app.route("/converter")
def converter():
    return render_template("convert2 (gemini).html", title="osu!mania 4K → Sparebeat コンバーター")
@app.route("/guide")
def guide():
    return render_template("guide.html", title="Json-Preview-Editorの使い方")
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        site_username = request.form.get("username")     # サイト用
        discord_name = request.form.get("discord_name")  # Discord照合用
        password = request.form.get("password")
        auth_code = request.form.get("auth_code")

        # 1. auth.db から Discord名 で最新トークンを検索
        token_entry = RegistrationToken.query.filter_by(discord_username=discord_name).order_by(RegistrationToken.created_at.desc()).first()

        # 2. コードと有効期限のチェック
        if not token_entry or token_entry.token != auth_code:
            flash("認証コードが正しくないか、Discord名が間違っています。")
            return redirect(url_for('register'))
        
        if datetime.utcnow() - token_entry.created_at > timedelta(minutes=5):
            flash("認証コードの期限切れです。")
            return redirect(url_for('register'))

        # 3. サイト内ユーザー名の重複チェック (users.db側)
        if User.query.filter_by(username=site_username).first():
            flash("そのサイト内表示名は既に使われています。別の名前をお試しください。")
            return redirect(url_for('register'))

        # 4. ユーザー作成
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(username=site_username, password=hashed)
        
        try:
            db.session.delete(token_entry) # 認証完了したのでトークン削除
            db.session.add(new_user)
            db.session.commit()
            flash(f"ようこそ {site_username} さん！登録が完了しました。")
            return redirect(url_for('login'))
        except:
            db.session.rollback()
            flash("エラーが発生しました。")
            return redirect(url_for('register'))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        return "ユーザー名またはパスワードが違います。"
    return render_template("login.html")

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for('index'))
#楽曲一覧の表示
# --- 楽曲一覧のホーム（難易度選択） ---
@app.route("/songs")
@login_required
def song_index():
    # 1. 存在する全難易度(star)を抽出
    available_levels = set()
    for song in SONGS_MASTER_DATA:
        lv_str = song['star'].replace('★', '')
        available_levels.add(lv_str)
    
    def sort_key(x):
        try: return (0, int(x))
        except: return (1, x)
            
    levels = sorted(list(available_levels), key=sort_key)

    # 2. 全曲の「現在の1位データ」を取得（検索結果に表示するため）
    all_top_scores = Score.query.order_by(Score.score_val.desc()).all()
    top_players_dict = {}
    for s in all_top_scores:
        if s.song_id not in top_players_dict:
            top_players_dict[s.song_id] = {
                'username': s.user.username,
                'score': s.score_val
            }

    # 3. 検索用に全楽曲データをリスト化
    search_songs = []
    for s in SONGS_MASTER_DATA:
        s_info = s.copy()
        sid = s['song_id']
        top = top_players_dict.get(sid)
        
        s_info['top_user'] = top['username'] if top else None
        s_info['top_score'] = top['score'] if top else 0
        s_info['level_num'] = s['star'].replace('★', '') # リンク用
        s_info['creator'] = s.get('creator', 'Unknown')  # 製作者名
        search_songs.append(s_info)

    return render_template("song_index.html", 
                           levels=levels, 
                           all_songs=search_songs)
# --- 特定の難易度の表示（levelを文字列として受け取る） ---
@app.route("/songs/all")
@login_required
def song_all_list():
    # 1. ページ番号の取得 (デフォルトは1ページ目)
    page = request.args.get('page', 1, type=int)
    per_page = 50

    # 2. 全曲の「現在の1位データ」を辞書化 (マスターデータ用)
    all_top_scores = Score.query.order_by(Score.score_val.desc()).all()
    top_players_dict = {}
    for s in all_top_scores:
        if s.song_id not in top_players_dict:
            top_players_dict[s.song_id] = {
                'username': s.user.username,
                'score': s.score_val
            }

    # 3. ユーザー自身のスコアを取得
    user_scores = {s.song_id: s.score_val for s in current_user.scores}

    # 4. マスターデータをIDの数値部分で降順（大きい順）にソート
    # filter(str.isdigit) を使って 'song_123' から 123 を取り出す
    sorted_master = sorted(
        SONGS_MASTER_DATA,
        key=lambda x: int(''.join(filter(str.isdigit, x['song_id'])) or 0),
        reverse=True
    )

    # 5. 手動ページネーションの作成
    total = len(sorted_master)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_songs_raw = sorted_master[start:end]

    # 6. 表示用データの整形
    display_songs = []
    for s in paginated_songs_raw:
        s_info = s.copy()
        sid = s['song_id']
        top = top_players_dict.get(sid)
        
        s_info['score'] = user_scores.get(sid, 0) # 自分のスコア
        s_info['top_user'] = top['username'] if top else None
        s_info['top_score'] = top['score'] if top else 0
        s_info['level_num'] = s['star'].replace('★', '')
        s_info['creator'] = s.get('creator', 'Unknown')
        # 全曲表示ではロック機能はオフにするか、必要ならここで判定
        s_info['is_locked'] = False 
        display_songs.append(s_info)

    # 7. ページネーション用メタデータ
    has_prev = page > 1
    has_next = end < total
    total_pages = (total + per_page - 1) // per_page

    return render_template("song_all.html", 
                           songs=display_songs, 
                           page=page, 
                           total_pages=total_pages,
                           has_prev=has_prev,
                           has_next=has_next)
@app.route("/songs/<level>") 
@login_required
def song_list(level):
    sort_type = request.args.get('sort', 'star')
    target_star = f"★{level}" 
    
    # 1. ルールの読み込み
    unlock_rules = {}
    if os.path.exists('unlock_rules.json'):
        try:
            with open('unlock_rules.json', 'r', encoding='utf-8') as f:
                unlock_rules = json.load(f)
        except Exception as e:
            print(f"Unlock rules load error: {e}")

    # 2. ユーザー自身の全スコアを取得
    my_scores = Score.query.filter_by(user_id=current_user.id).all()
    score_dict = {s.song_id: s.score_val for s in my_scores} 
    
    # 3. 全曲の「1位データ」をあらかじめ取得（効率化のため）
    # 全てのスコアをスコア順に並べて取得し、曲ごとに一番上の人だけを採用する
    all_top_scores = Score.query.order_by(Score.score_val.desc()).all()
    # {曲ID: (ユーザー名, スコア)} の辞書を作る
    top_players_dict = {}
    for s in all_top_scores:
        if s.song_id not in top_players_dict:
            top_players_dict[s.song_id] = {
                'username': s.user.username,
                'score': s.score_val
            }

    songs_data = []
    for song in SONGS_MASTER_DATA:
        if song['star'] == target_star:
            s_copy = song.copy()
            sid = s_copy['song_id']
            
            # --- スコア設定 ---
            score = score_dict.get(sid, 0)
            s_copy['score'] = score
            
            # --- 譜面製作者の取得 ---
            s_copy['creator'] = song.get('creator', 'Unknown')
            
            # --- 1位データのセット ---
            top_info = top_players_dict.get(sid)
            if top_info:
                s_copy['top_user'] = top_info['username']
                s_copy['top_score'] = top_info['score']
            else:
                s_copy['top_user'] = None
                s_copy['top_score'] = 0

            # --- 解禁判定 ---
            s_copy['is_locked'] = False
            s_copy['lock_msg'] = ""
            if sid in unlock_rules:
                rule = unlock_rules[sid]
                req_best = score_dict.get(rule['req_id'], 0)
                if req_best < rule['req_score']:
                    s_copy['is_locked'] = True
                    s_copy['lock_msg'] = f"[{rule['msg']}] で解放"

            # --- レート計算 ---
            rate = calculate_rate(score, s_copy['star']) if score > 0 else 0
            s_copy['tmp_rate'] = rate
            songs_data.append(s_copy)

    # 4. ソート処理
    if sort_type == 'rate':
        # レート順（降順）
        songs_data.sort(key=lambda x: x['tmp_rate'], reverse=True)
    elif sort_type == 'score':
        # スコア順（降順）
        songs_data.sort(key=lambda x: x['score'], reverse=True)
    else:
        # どんなデータが来てもエラーを出さず、数値としてソートする
        def powerful_id_sort(x):
            sid = x.get('song_id')
            if sid is None:
                return 0
            
            # 文字列から数字だけを抽出する（例: "ID1023" -> 1023 / "099" -> 99）
            import re
            nums = re.findall(r'\d+', str(sid))
            return int(nums[0]) if nums else 0

        songs_data.sort(key=powerful_id_sort, reverse=True)

    return render_template("songs.html", 
                           songs=songs_data, 
                           level=level, 
                           current_sort=sort_type)
# --- 一括登録後のリダイレクト先を修正 ---
@app.route("/add_score_bulk", methods=["POST"])
@login_required
def add_score_bulk():
    current_lv = request.form.get("current_level", "12")
    
    # 削除処理
    delete_sid = request.form.get("delete_song")
    if delete_sid:
        # ユーザーIDと曲IDが一致するものをすべて削除（重複掃除のため）
        Score.query.filter_by(user_id=current_user.id, song_id=delete_sid).delete()
        db.session.commit()
        return redirect(url_for('song_list', level=current_lv))

    # 更新・追加処理
    for key, score_str in request.form.items():
        if key.startswith("scores["):
            sid = key[7:-1] 
            
            if score_str and score_str.strip() != "":
                try:
                    val = int(score_str)
                    if 1 <= val <= 1000000:
                        # ★修正ポイント：既存のスコアを「1つだけ」取得
                        existing = Score.query.filter_by(user_id=current_user.id, song_id=sid).first()
                        
                        if existing:
                            # 既にあれば値を更新するだけ
                            existing.score_val = val
                        else:
                            # なければ新しく作成
                            db.session.add(Score(user_id=current_user.id, song_id=sid, score_val=val))
                except ValueError: continue
    
    db.session.commit()
    refresh_user_stats(current_user.id)
    return redirect(url_for('song_list', level=current_lv))

@app.route("/song/<song_id>")
@login_required
def song_ranking(song_id):
    # 1. マスターデータから曲情報を取得
    song_info = next((s for s in SONGS_MASTER_DATA if s.get('song_id') == song_id), None)
    
    if not song_info:
        # IDで見つからない場合の救済策
        song_info = next((s for s in SONGS_MASTER_DATA if s.get('name') == song_id), None)
        
    if not song_info:
        return "楽曲が見つかりませんでした", 404

    # 2. 表示用データの準備
    song_name = song_info.get('name', '不明な曲名')
    star_val = song_info.get('star', '★15')
    level = star_val.replace('★', '')

    print(f"DEBUG: song_id={song_id}, song_name={song_name}, found_level={level}")

    rankings = Score.query.filter_by(song_id=song_id).order_by(Score.score_val.desc()).all()
    
    return render_template("ranking.html", 
                           song_name=song_name, 
                           rankings=rankings, 
                           level=level)


@app.route("/upload_new_song", methods=["GET", "POST"])
@login_required
def upload_new_song():
    # --- 修正：投稿を許可するユーザー名のリスト ---
    allowed_users = ["X-ray", "yanaaaaaaa", "pentatonic", "kakuteru", "SenKa", "とらさん大好き！", "GokuLLLLL", "ma", "とある牛丼マスター", "かあらげ", "lpmmoyojs", "sattyann118", "はるちん", "afu","ばうむくーへん","minakolin_3752","tubumame"]
    
    if current_user.username not in allowed_users:
        flash("お前に投稿権限はない")
        return redirect(url_for('song_index'))
    # -----------------------------------
    if request.method == "POST":
        yt_url = request.form.get("youtube_url")
        star = request.form.get("star", "15")
        difficulty = request.form.get("difficulty", "H")
        file = request.files.get("chart_file")

        # 1. ファイル未選択チェック
        if not file or file.filename == '':
            flash("譜面ファイル(.json)を選択してください。")
            return redirect(request.url)

        if not yt_url:
            flash("YouTube音源URLを入力してください。")
            return redirect(request.url)

        if file and file.filename.endswith('.json'):
            try:
                # 2. JSONの中身を読み込んで曲名を取得
                chart_content = json.load(file)
                song_name = chart_content.get("title", "無題の曲")
                
                # 3. 日本語対応のファイル名生成（曲名_難易度.json）
                # OSで禁止されている記号を削除
                clean_song_name = re.sub(r'[\\/:*?"<>|]', '', song_name)
                clean_difficulty = re.sub(r'[\\/:*?"<>|]', '', difficulty)
                
                # ファイル名を生成
                chart_filename = f"{clean_song_name}_{clean_difficulty}.json"
                file_path = os.path.join(CHARTS_DIR, chart_filename)

                # 4. 重複チェック（同じ曲の同じ難易度が既にないか）
                if os.path.exists(file_path):
                    flash(f"エラー：同名の譜面「{chart_filename}」が既に存在します。内容を更新したい場合は、一度既存の譜面を削除してください。")
                    return redirect(request.url)

                # 5. ファイルを保存
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(chart_content, f, ensure_ascii=False, indent=4)

                # 6. ID自動採番
                global SONGS_MASTER_DATA
                max_num = 0
                for s in SONGS_MASTER_DATA:
                    try:
                        num = int(s['song_id'].split('_')[1])
                        if num > max_num: max_num = num
                    except: continue
                new_id = f"song_{max_num + 1:03d}"

                # 7. マスターデータ(songs.json)へ書き込み
                new_song = {
                    "song_id": new_id,
                    "name": song_name,
                    "difficulty": difficulty,
                    "star": f"★{star}" if "★" not in star else star,
                    "youtube_url": yt_url,
                    "chart_file": chart_filename,
                    "creator": current_user.username
                }

                SONGS_MASTER_DATA.append(new_song)
                with open(os.path.join(basedir, 'songs.json'), 'w', encoding='utf-8') as f:
                    json.dump(SONGS_MASTER_DATA, f, ensure_ascii=False, indent=4)

                create_changes = [
                    {'field': 'difficulty', 'label': '譜面難易度', 'before': '—', 'after': _norm_field_val(difficulty) or '(空)'},
                    {'field': 'star', 'label': 'レベル(★)', 'before': '—', 'after': _norm_field_val(new_song['star'])},
                    {'field': 'creator', 'label': '製作者', 'before': '—', 'after': _norm_field_val(current_user.username)},
                ]
                append_song_master_update_log(new_id, song_name, 'create', create_changes)

                flash(f"「{song_name}」の譜面({difficulty})を登録しました！")
                return redirect(url_for('song_index'))

            except Exception as e:
                flash(f"エラーが発生しました: {e}")
                return redirect(request.url)

    # GET時は投稿画面を表示（ここが抜けるとエラーになります）
    return render_template("upload_new_song.html")
# --- さんぼｗ専用 楽曲編集ページ ---
@app.route("/admin/edit_songs", methods=["GET", "POST"])
@login_required
def edit_songs():
    # 編集を許可するユーザー名のリスト
    allowed_admins = ["X-ray", "とある牛丼マスター"]

    # 権限チェック：リストに含まれていない場合は拒否
    if current_user.username not in allowed_admins:
        return f"権限がありません。管理者（{' or '.join(allowed_admins)}）としてログインしてください。", 403

    global SONGS_MASTER_DATA

    if request.method == "POST":
        songs_json_path = os.path.join(basedir, 'songs.json')
        
        # 1. 処理の直前に最新のファイルを読み込む（データの先祖返り防止）
        with open(songs_json_path, 'r', encoding='utf-8') as f:
            current_data = json.load(f)

        # --- A. 削除処理 ---
        delete_target_id = request.form.get("delete_id")
        if delete_target_id:
            updated_data = [s for s in current_data if s['song_id'] != delete_target_id]
            deleted_song = next((s for s in current_data if s['song_id'] == delete_target_id), None)
            
            if deleted_song:
                with open(songs_json_path, 'w', encoding='utf-8') as f:
                    json.dump(updated_data, f, ensure_ascii=False, indent=4)
                
                SONGS_MASTER_DATA = updated_data
                append_song_master_update_log(delete_target_id, deleted_song.get('name', ''), 'delete', [])
                flash(f"楽曲 {delete_target_id} を削除しました。")
            return redirect(url_for('edit_songs'))

        # --- B. 更新処理（1行ごとの保存に対応） ---
        update_id = request.form.get("update_id") # HTML側の hidden input から取得
        if update_id:
            change_occurred = False
            for song in current_data:
                if song['song_id'] == update_id:
                    # フォームから新しい値を取得
                    new_song = {
                        "song_id": update_id,
                        "name": request.form.get(f"name_{update_id}"),
                        "difficulty": request.form.get(f"diff_{update_id}"),
                        "star": request.form.get(f"star_{update_id}"),
                        "youtube_url": request.form.get(f"yt_{update_id}"),
                        "chart_file": request.form.get(f"file_{update_id}"),
                        "creator": request.form.get(f"creator_{update_id}") # 製作者を追加
                    }
                    
                    # 差分があるかチェック
                    diff = _diff_song_records(song, new_song)
                    if diff:
                        song.update(new_song) # 最新データの中の該当曲だけ上書き
                        
                        # ファイルに書き出し
                        with open(songs_json_path, 'w', encoding='utf-8') as f:
                            json.dump(current_data, f, ensure_ascii=False, indent=4)
                        
                        # グローバル変数とログの更新
                        SONGS_MASTER_DATA = current_data
                        display_name = _norm_field_val(new_song.get('name')) or song.get('name', '')
                        append_song_master_update_log(update_id, display_name, 'update', diff)
                        flash(f"楽曲「{display_name}」を更新しました。")
                        change_occurred = True
                    break # 対象の曲が見つかったらループ終了
            
            if not change_occurred:
                flash("変更はありませんでした。")
                
            return redirect(url_for('edit_songs'))

    return render_template("edit_songs.html", songs=SONGS_MASTER_DATA)
@app.route("/mypage")
@login_required
def mypage():
    return redirect(url_for('user_profile', username=current_user.username))


@app.route("/user/<username>")
def user_profile(username):
    target_user = User.query.filter_by(username=username).first_or_404()
    
    created_songs = []
    try:
        with open('songs.json', 'r', encoding='utf-8') as f:
            all_songs = json.load(f)
            for s in all_songs:
                if s.get('creator') == username:
                    star_str = str(s.get('star', '★?')) 
                    s['level_num'] = star_str.replace('★', '').strip()
                    created_songs.append(s)
            created_songs.reverse()
    except Exception as e:
        print(f"JSON Error in user_profile: {e}")
        created_songs = []

    # --- 既存のプロフィールデータ取得 ---
    data = get_user_profile_data(target_user)
    
    # 1位獲得数の計算
    top_one_count = 0
    user_scores = Score.query.filter_by(user_id=target_user.id).all()
    for s in user_scores:
        if s.score_val > 0:
            max_score = db.session.query(func.max(Score.score_val))\
                .filter(Score.song_id == s.song_id).scalar()
            if s.score_val == max_score:
                top_one_count += 1
    
    data['top_one_count'] = top_one_count
    data['is_bot'] = False

    # --- 追加：JSONキャッシュによるロール判定 ---
    stats_data = load_user_stats()  # 事前に作成した読み込み関数
    stats = stats_data.get(str(target_user.id), {
        "score_count": 0, 
        "perfect_count": 0, 
        "mapped_count": 0
    })
    
    user_roles = []

    # 1. 登録スコア数ロール
    score_rules = [
        (500, "やな鯖のすべてを知る者", "#b9f2ff"),
        (250, "やな鯖ガチ勢", "#ffd700"),
        (100, "上級やな鯖民", "#c0c0c0"),
        (50,  "やな鯖の一般人", "#cd7f32")
    ]
    for limit, name, color in score_rules:
        if stats['score_count'] >= limit:
            user_roles.append({'name': name, 'color': color})
            break

    # 2. 理論値数ロール
    perfect_rules = [
        (100, "精度マスター", "linear-gradient(45deg, #ff0000, #ff7f00, #ffff00, #00ff00, #0000ff, #4b0082, #8b00ff)"),
        (50,  "精度ガチ勢", "#e5e4e2"),
        (25,  "精度勢", "#ffd700"),
        (10,  "精度勢見習い", "#c0c0c0")
    ]
    for limit, name, color in perfect_rules:
        if stats['perfect_count'] >= limit:
            user_roles.append({'name': name, 'color': color})
            break

    # 3. 譜面投稿数ロール
    mapped_rules = [
        (250, "Mappingの神", "#ff0000"),
        (100, "Mappingマスター", "#ff4500"),
        (50,  "中堅Mapper", "#ff8c00"),
        (25,  "一般Mapper", "#ffa500"),
        (10,  "見習いMapper", "#ffd700")
    ]
    for limit, name, color in mapped_rules:
        if stats['mapped_count'] >= limit:
            user_roles.append({'name': name, 'color': color})
            break

    # render_template に created_songs と user_roles を渡す
    return render_template("mypage.html", 
                           created_songs=created_songs, 
                           user_roles=user_roles, 
                           **data)
@app.route("/total-ranking")
def total_ranking():
    users = User.query.all()
    ranking_data = []
    for user in users:
        profile = get_user_profile_data(user)
        ranking_data.append({'username': user.username, 'total_rate': profile['total_rate'], 'total_count': profile['total_count']})
    ranking_data.sort(key=lambda x: x['total_rate'], reverse=True)
    return render_template("rate-ranking.html", ranking=ranking_data)
@app.route("/admin/update_creators", methods=["GET", "POST"])
@login_required
def update_creators():

    # 投稿権限を持つユーザーリスト（既存のリストを利用）
    allowed_users = ["X-ray", "yanaaaaaaa", "pentatonic", "kakuteru", "SenKa", 
                     "とらさん大好き！", "GokuLLLLL", "ma", "とある牛丼マスター", 
                     "かあらげ", "lpmmoyojs", "sattyann118", "はるちん", "afu",
                     "ばうむくーへん", "minakolin_3752", "tubumame","Fibonacci"]

    if request.method == "POST":
        global SONGS_MASTER_DATA
        for song in SONGS_MASTER_DATA:
            sid = song['song_id']
            # フォームから送信された新しい製作者名を取得
            new_creator = request.form.get(f"creator_{sid}")
            if new_creator:
                song['creator'] = new_creator # jsonのcreator項目を更新
        
        # songs.json に保存
        with open(os.path.join(basedir, 'songs.json'), 'w', encoding='utf-8') as f:
            json.dump(SONGS_MASTER_DATA, f, ensure_ascii=False, indent=4)
        
        flash("製作者情報を更新しました。")
        return redirect(url_for('song_index'))

    return render_template("admin_update_creators.html", 
                           songs=SONGS_MASTER_DATA, 
                           users=allowed_users)


@app.route('/statistics')
@login_required
def statistics():
    all_scores   = Score.query.all()
    all_users    = User.query.all()
    song_info    = {s['song_id']: s for s in SONGS_MASTER_DATA}
    uid_to_name  = {u.id: u.username for u in all_users}
 
    total_songs  = len(SONGS_MASTER_DATA)
    total_scores = len(all_scores)
    unique_users = db.session.query(Score.user_id).distinct().count()
 
    # --------------------------------------------------
    # 難易度・レベル・クリエイター別
    # --------------------------------------------------
    diff_count    = Counter(s.get('difficulty', '') for s in SONGS_MASTER_DATA if s.get('difficulty'))
    creator_count = Counter(s.get('creator', 'Unknown') for s in SONGS_MASTER_DATA)
    level_songs   = Counter()
    level_scores  = Counter()
 
    for s in SONGS_MASTER_DATA:
        lv = s.get('star', '').replace('★', '')
        if lv.isdigit():
            level_songs[int(lv)] += 1
 
    for sc in all_scores:
        song = song_info.get(sc.song_id)
        if song:
            lv = song.get('star', '').replace('★', '')
            if lv.isdigit():
                level_scores[int(lv)] += 1
 
    # --------------------------------------------------
    # スコア帯
    # --------------------------------------------------
    tier_mm     = sum(1 for s in all_scores if s.score_val >= 1000000)
    tier_gold   = sum(1 for s in all_scores if 990000 <= s.score_val < 1000000)
    tier_silver = sum(1 for s in all_scores if 950000 <= s.score_val < 990000)
    tier_other  = sum(1 for s in all_scores if s.score_val < 950000)
 
    # --------------------------------------------------
    # レート分布（2刻みバケツ）
    # --------------------------------------------------
    rate_distribution = {}
    for sc in all_scores:
        song = song_info.get(sc.song_id)
        if not song:
            continue
        rate = calculate_rate(sc.score_val, song.get('star', ''))
        if rate > 0:
            bucket = int(rate // 2) * 2
            rate_distribution[bucket] = rate_distribution.get(bucket, 0) + 1
 
    # --------------------------------------------------
    # ユーザー別登録数 TOP10
    # --------------------------------------------------
    user_score_count = Counter(sc.user_id for sc in all_scores)
    user_top10 = [
        [uid_to_name.get(uid, f'user_{uid}'), cnt]
        for uid, cnt in user_score_count.most_common(10)
    ]
 
    # --------------------------------------------------
    # 譜面別 スコア登録数ランキング TOP20
    # --------------------------------------------------
    song_score_count = Counter(sc.song_id for sc in all_scores)
    song_ranking_top20 = []
    for song_id, cnt in song_score_count.most_common(20):
        s = song_info.get(song_id, {})
        song_ranking_top20.append({
            'song_id':  song_id,
            'name':     s.get('name', song_id),
            'star':     s.get('star', '★?'),
            'difficulty': s.get('difficulty', ''),
            'count':    cnt,
        })
 
    # --------------------------------------------------
    # 単曲レート TOP30（全ユーザー × 全譜面）
    # --------------------------------------------------
    all_rates = []
    for sc in all_scores:
        song = song_info.get(sc.song_id)
        if not song:
            continue
        rate = calculate_rate(sc.score_val, song.get('star', ''))
        if rate > 0:
            all_rates.append({
                'username':   uid_to_name.get(sc.user_id, f'user_{sc.user_id}'),
                'song_name':  song.get('name', sc.song_id),
                'star':       song.get('star', '★?'),
                'difficulty': song.get('difficulty', ''),
                'score':      sc.score_val,
                'rate':       rate,
            })
    all_rates.sort(key=lambda x: x['rate'], reverse=True)
    top_rates_30 = all_rates[:30]
 
    # --------------------------------------------------
    # 理論値（1,000,000点）取得数ランキング
    # --------------------------------------------------
    perfect_count = Counter(
        sc.user_id for sc in all_scores if sc.score_val >= 1000000
    )
    perfect_ranking = [
        [uid_to_name.get(uid, f'user_{uid}'), cnt]
        for uid, cnt in perfect_count.most_common(10)
    ]
 
    # --------------------------------------------------
    # ユーザー別 平均順位
    # (各譜面において自分のスコアが何位か → 全譜面の平均)
    # --------------------------------------------------
    # 譜面ごとにスコアを降順ソート → 順位付け
    song_scores_map = defaultdict(list)
    for sc in all_scores:
        song_scores_map[sc.song_id].append((sc.user_id, sc.score_val))
 
    user_rank_sum   = defaultdict(int)
    user_rank_count = defaultdict(int)
 
    for song_id, entries in song_scores_map.items():
        sorted_entries = sorted(entries, key=lambda x: x[1], reverse=True)
        for rank, (uid, _) in enumerate(sorted_entries, start=1):
            user_rank_sum[uid]   += rank
            user_rank_count[uid] += 1
 
    avg_rank_list = []
    for uid, total_rank in user_rank_sum.items():
        count = user_rank_count[uid]
        if count >= 5:  # 5曲以上登録しているユーザーのみ対象
            avg_rank_list.append({
                'username': uid_to_name.get(uid, f'user_{uid}'),
                'avg_rank': round(total_rank / count, 2),
                'count':    count,
            })
    avg_rank_list.sort(key=lambda x: x['avg_rank'])
    avg_rank_top10 = avg_rank_list[:10]
 
    # --------------------------------------------------
    # 平均スコア登録率
    # --------------------------------------------------
    avg_score_rate = round(total_scores / total_songs, 1) if total_songs else 0
 
    stats = {
        'total_songs':        total_songs,
        'total_scores':       total_scores,
        'unique_users':       unique_users,
        'unique_creators':    len(set(s.get('creator') for s in SONGS_MASTER_DATA if s.get('creator'))),
        'avg_score_rate':     avg_score_rate,
        'diff_count':         dict(diff_count),
        'level_songs':        {str(k): v for k, v in level_songs.items()},
        'level_scores':       {str(k): v for k, v in level_scores.items()},
        'creator_top10':      creator_count.most_common(10),
        'tiers':              {'mm': tier_mm, 'gold': tier_gold, 'silver': tier_silver, 'other': tier_other},
        'rate_distribution':  {str(k): v for k, v in rate_distribution.items()},
        'user_top10':         user_top10,
        'song_ranking_top20': song_ranking_top20,
        'top_rates_30':       top_rates_30,
        'perfect_ranking':    perfect_ranking,
        'avg_rank_top10':     avg_rank_top10,
    }
    return render_template('statistics.html', stats=stats)

@app.route("/sambo")
def sambo():
    return render_template("sambo.html")
@app.route("/cube")
def cube():
    return render_template("cube.html")
# app.py の末尾付近に追加
ADMIN_BROADCAST_ALLOWED = ["X-ray", "yanaaaaaaa","SenKa"]
@socketio.on('join')
def on_join():
    if current_user.is_authenticated:
        join_room(current_user.username)
        print(f"User {current_user.username} joined their private room.")

@socketio.on('send_admin_message')
def handle_admin_message(data):
    # 特定のリストに含まれるユーザーのみ許可
    if current_user.is_authenticated and current_user.username in ADMIN_BROADCAST_ALLOWED:
        target_user = data.get('target_user')
        message = data.get('message', '').strip()
        if target_user and message:
            emit('receive_comment', {
                'message': message,
                'content_type': data.get('content_type', 'text'),
                'font_size': data.get('font_size', '2rem'),
                'font_color': data.get('font_color', 'white'),
                'anim_speed': data.get('anim_speed', 5),
                'direction': data.get('direction', 'left'),
                'text_anim': data.get('text_anim', 'none'),
                'rotate_deg': data.get('rotate_deg', 0),
                'use_gradient': data.get('use_gradient', False),
                'grad_color1': data.get('grad_color1', '#00d4ff'),
                'grad_color2': data.get('grad_color2', '#ff00ff'),
            }, to=target_user)

@app.route("/admin/broadcast")
@login_required
def admin_broadcast():
    # ここでもリストに含まれているかチェック
    if current_user.username not in ADMIN_BROADCAST_ALLOWED:
        return "アクセス権限がありません", 403
    
    all_users = User.query.all()
    return render_template("admin_broadcast.html", users=all_users)

if __name__ == "__main__":
    # port を 8000 に変更（Nginxの設定と合わせる）
    socketio.run(app, host="127.0.0.1", port=8000, debug=True)
