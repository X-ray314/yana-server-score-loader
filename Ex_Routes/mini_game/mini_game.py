import os
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime
from sqlalchemy import func

# Blueprintの定義
mini_game_bp = Blueprint(
    'mini_game',
    __name__,
    url_prefix='/mini_game',
    template_folder='templates',
    static_folder='static',
    static_url_path='/mini_game_static'
)

db = None
MiniGameScore = None

# --- データベースモデル ---
def define_models(database):
    global db, MiniGameScore
    db = database

    # 既にモデル定義済みなら再定義しない
    if MiniGameScore is not None:
        return MiniGameScore

    class _MiniGameScore(db.Model):
        __tablename__ = 'mini_game_score'
        __table_args__ = {'extend_existing': True}
        __bind_key__ = 'minigame'

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, nullable=False)
        username = db.Column(db.String(50), nullable=False)
        game_name = db.Column(db.String(50), nullable=False)
        score = db.Column(db.Float, nullable=False)
        timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    MiniGameScore = _MiniGameScore
    return MiniGameScore

# --- 初期化関数 ---
def init_mini_game_db(app, database):
    define_models(database)
    with app.app_context():
        database.create_all(bind_key='minigame')
        print("MiniGame DB Initialized!")

# --- ルーティング ---
@mini_game_bp.route('/')
def index():
    return render_template('mini_game/index.html')

@mini_game_bp.route('/play/<game_name>')
@login_required
def play(game_name):
    return render_template(f'mini_game/{game_name}.html')

@mini_game_bp.route('/avoiding')
@login_required
def avoiding():
    return render_template('mini_game/avoiding.html')

@mini_game_bp.route('/api/submit_score', methods=['POST'])
@login_required
def submit_score():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    game_name = data.get('game_name')
    new_score_val = data.get('score')

    # --- 判定ロジックの整理 ---
    # 数値が小さい（昇順）方が良いゲームのリスト
    ASC_GAMES = ['reaction', 'lightsout', '2048_time', 'breakout_time']
    is_asc_game = game_name in ASC_GAMES or game_name.startswith('puzzle_') or game_name.startswith('hanoi_')

    existing_record = MiniGameScore.query.filter_by(
        user_id=current_user.id,
        game_name=game_name
    ).first()

    if existing_record:
        is_updated = False

        if game_name == 'stopwatch':
            if abs(new_score_val - 10.0) < abs(existing_record.score - 10.0):
                is_updated = True
        elif is_asc_game:
            if new_score_val < existing_record.score:
                is_updated = True
        else:
            if new_score_val > existing_record.score:
                is_updated = True

        if is_updated:
            existing_record.score = new_score_val
            existing_record.timestamp = datetime.utcnow()
            db.session.commit()
            return jsonify({"status": "success", "message": "High score updated!"})
        
        return jsonify({"status": "success", "message": "Score submitted (not a high score)."})

    # 新規レコード作成
    new_score = MiniGameScore(
        user_id=current_user.id,
        username=current_user.username,
        game_name=game_name,
        score=new_score_val
    )
    db.session.add(new_score)
    db.session.commit()
    return jsonify({"status": "success", "message": "First score saved!"})


@mini_game_bp.route('/ranking/<game_name>')
def ranking(game_name):
    # 昇順（小さい方が上位）として扱うゲームの定義
    # ここに 'breakout_time' を追加
    ASC_GAMES = ['reaction', 'lightsout', '2048_time', 'breakout_time']
    
    # game_name がリストにある、または puzzle_ や hanoi_ で始まる場合に True
    is_asc_game = (game_name in ASC_GAMES or 
                  game_name.startswith('puzzle_') or 
                  game_name.startswith('hanoi_'))
    
    # クエリのベース
    query = MiniGameScore.query.filter_by(game_name=game_name)

    # ソート順の決定
    if game_name == 'stopwatch':
        # ストップウォッチは10.0秒に近い順（絶対値の昇順）
        top_scores = query.order_by(func.abs(MiniGameScore.score - 10.0).asc()).limit(10).all()
        is_high_score_better = False
    elif is_asc_game:
        # タイムや手数など、数値が小さいほど上位
        top_scores = query.order_by(MiniGameScore.score.asc()).limit(10).all()
        is_high_score_better = False
    else:
        # スコアや距離など、数値が大きいほど上位
        top_scores = query.order_by(MiniGameScore.score.desc()).limit(10).all()
        is_high_score_better = True

    return render_template(
        'mini_game/ranking.html',
        scores=top_scores,
        game_name=game_name,
        is_high_score_better=is_high_score_better
    )