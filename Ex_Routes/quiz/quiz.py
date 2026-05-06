import os
import json
from datetime import datetime

from flask import Blueprint, render_template, abort
from flask_login import current_user
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    ForeignKey, Table, MetaData
)
from sqlalchemy.orm import scoped_session, sessionmaker

# --- Blueprint定義 ---
quiz_bp = Blueprint(
    'quiz',
    __name__,
    url_prefix='/quiz',
    template_folder='templates'
)

# --- DB設定 ---
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
os.makedirs(instance_path, exist_ok=True)

QUIZ_DB_PATH = os.path.join(instance_path, 'quiz.db')
QUIZ_DB_URI = 'sqlite:///' + QUIZ_DB_PATH

engine = create_engine(QUIZ_DB_URI)
metadata = MetaData()

# --- テーブル定義 ---

# クイズ本体
quiz_forms_table = Table(
    'quiz_forms', metadata,
    Column('id', Integer, primary_key=True),
    Column('title', String(100), nullable=False),
    Column('description', Text),
    Column('creator_id', Integer, nullable=False),
    Column('creator_name', String(50)),
    Column('time_limit', Integer, default=0),       # 0=無制限
    Column('show_answers', Integer, default=1),     # 1=結果画面で正解表示
    Column('random_count', Integer, default=0),     # 0=全問出題
    Column('image_url', String(500)),
    Column('is_published', Integer, default=0),
    Column('created_at', DateTime, default=datetime.now),
    Column('updated_at', DateTime, default=datetime.now)
)

# 問題
quiz_questions_table = Table(
    'quiz_questions', metadata,
    Column('id', Integer, primary_key=True),
    Column('form_id', Integer, ForeignKey('quiz_forms.id'), nullable=False),
    Column('sort_order', Integer, default=0),
    Column('q_type', String(50), nullable=False),   # single_choice / multiple_choice / text
    Column('q_text', Text, nullable=False),
    Column('image_url', String(500)),
    Column('choices_json', Text),                   # JSON文字列で選択肢を保存
    Column('correct_answer_json', Text),            # JSON文字列で正解を保存
    Column('points', Integer, default=1)
)

# 受験結果
quiz_results_table = Table(
    'quiz_results', metadata,
    Column('id', Integer, primary_key=True),
    Column('form_id', Integer, ForeignKey('quiz_forms.id'), nullable=False),
    Column('user_id', Integer, nullable=False),
    Column('user_name', String(50)),
    Column('score', Integer),
    Column('total', Integer),
    Column('correct_count', Integer),
    Column('total_questions', Integer),
    Column('time_taken', Integer),
    Column('taken_at', DateTime, default=datetime.now)
)

# --- セッション取得 ---
def get_db():
    session = scoped_session(sessionmaker(bind=engine))()
    return session

# --- DB初期化（app.pyから呼び出す） ---
def init_quiz_db(app):
    with app.app_context():
        metadata.create_all(engine)
        print("quiz.db initialized!")

# --- ルート（仮置き・動作確認用） ---
from sqlalchemy import desc

@quiz_bp.route('/')
def index():
    db = get_db()
    forms = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.is_published == 1)
        .order_by(desc(quiz_forms_table.c.updated_at))
    ).fetchall()

    user_scores = {}
    if current_user.is_authenticated:
        for form in forms:
            result = db.execute(
                quiz_results_table.select()
                .where(quiz_results_table.c.form_id == form.id)
                .where(quiz_results_table.c.user_id == current_user.id)
                .order_by(desc(quiz_results_table.c.score))
            ).fetchone()
            if result:
                user_scores[form.id] = f"{result.score} / {result.total}"
    db.close()
    return render_template('quiz/index.html', forms=forms, user_scores=user_scores)

from flask import request, redirect, url_for, flash
from flask_login import login_required
from werkzeug.utils import secure_filename

# Ex-Routes/quiz/quiz.py の上部を差し替え
project_root = os.path.abspath(os.path.join(basedir, '..', '..'))
UPLOAD_FOLDER = os.path.join(project_root, 'static', 'uploads', 'quiz_imgs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@quiz_bp.route('/create', methods=['GET', 'POST'])
@login_required
def setup_form(): 
    if request.method == 'POST':
        db = get_db()  
        title = request.form.get('title')
        description = request.form.get('description')
        time_limit = request.form.get('time_limit', type=int) or 0
        show_answers = request.form.get('show_answers', type=int)
        if show_answers is None:
         show_answers = 1

        image_url = None
        file = request.files.get('icon_image')
        if file and allowed_file(file.filename):
            filename = secure_filename(f"icon_{datetime.now().timestamp()}_{file.filename}")
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            image_url = url_for('static', filename=f'uploads/quiz_imgs/{filename}')

        result = db.execute(
            quiz_forms_table.insert().values(
                title=title,
                description=description,
                time_limit=time_limit,
                show_answers=show_answers,
                image_url=image_url,
                creator_id=current_user.id,
                creator_name=current_user.username,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        )
        db.commit()
        db.close()
        flash(f"「{title}」を作成しました！問題を追加しましょう。")
        return redirect(url_for('quiz.edit_questions', form_id=result.lastrowid))

    return render_template('quiz/setup.html')
@quiz_bp.route('/edit/<int:form_id>', methods=['GET', 'POST'])
@quiz_bp.route('/edit/<int:form_id>/<int:q_id>', methods=['GET', 'POST'])
@login_required
def edit_questions(form_id, q_id=None):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()

    if not form:
        abort(404)
    if form.creator_id != current_user.id:
        abort(403)

    # 編集対象の問題を取得
    target_q = None
    if q_id:
        row = db.execute(
            quiz_questions_table.select()
            .where(quiz_questions_table.c.id == q_id)
        ).fetchone()
        if row:
            target_q = dict(row._asdict())
            target_q['choices'] = json.loads(target_q['choices_json'] or '[]')
            target_q['correct_answer'] = json.loads(target_q['correct_answer_json'] or '[]')

    if request.method == 'POST':
        q_type = request.form.get('q_type')
        q_text = request.form.get('q_text')
        points = request.form.get('points', type=int) or 1
        choices = [
            request.form.get(f'choice_{i}')
            for i in range(1, 11)
            if request.form.get(f'choice_{i}')
        ]

        correct_answer = []
        if q_type in ['single_choice', 'multiple_choice']:
            vals = request.form.getlist('correct_answer')
            correct_answer = [int(v) for v in vals]
        elif q_type == 'text':
         answers = request.form.getlist('text_correct_answers')
         correct_answer = [a.strip() for a in answers if a.strip()]

        if q_id:
            db.execute(
                quiz_questions_table.update()
                .where(quiz_questions_table.c.id == q_id)
                .values(
                    q_type=q_type,
                    q_text=q_text,
                    points=points,
                    choices_json=json.dumps(choices, ensure_ascii=False),
                    correct_answer_json=json.dumps(correct_answer, ensure_ascii=False)
                )
            )
            flash("問題を更新しました。")
        else:
            db.execute(
                quiz_questions_table.insert().values(
                    form_id=form_id,
                    q_type=q_type,
                    q_text=q_text,
                    points=points,
                    choices_json=json.dumps(choices, ensure_ascii=False),
                    correct_answer_json=json.dumps(correct_answer, ensure_ascii=False),
                    sort_order=0
                )
            )
            flash("問題を追加しました。")

        db.execute(
            quiz_forms_table.update()
            .where(quiz_forms_table.c.id == form_id)
            .values(updated_at=datetime.now())
        )
        db.commit()
        db.close()
        return redirect(url_for('quiz.edit_questions', form_id=form_id))

    # 登録済みの全問題を取得
    rows = db.execute(
        quiz_questions_table.select()
        .where(quiz_questions_table.c.form_id == form_id)
    ).fetchall()
    questions = []
    for r in rows:
        q = dict(r._asdict())
        q['choices'] = json.loads(q['choices_json'] or '[]')
        q['correct_answer'] = json.loads(q['correct_answer_json'] or '[]')
        questions.append(q)

    db.close()
    return render_template('quiz/edit_questions.html',
                           form=form, questions=questions, target_q=target_q)


@quiz_bp.route('/question/delete/<int:q_id>/<int:form_id>')
@login_required
def delete_question(q_id, form_id):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()
    if form.creator_id != current_user.id:
        abort(403)
    db.execute(
        quiz_questions_table.delete()
        .where(quiz_questions_table.c.id == q_id)
    )
    db.commit()
    db.close()
    flash("問題を削除しました。")
    return redirect(url_for('quiz.edit_questions', form_id=form_id))
import random
from flask import session as flask_session, make_response

@quiz_bp.route('/play/<int:form_id>', methods=['GET', 'POST'])
@login_required
def take_test(form_id):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()

    if not form:
        abort(404)

    all_rows = db.execute(
        quiz_questions_table.select()
        .where(quiz_questions_table.c.form_id == form_id)
    ).fetchall()

    session_key = f'quiz_start_{form_id}'
    order_key   = f'quiz_order_{form_id}'

    # --- GET ---
    if request.method == 'GET':
        # 開始時刻をセッションに記録
        if session_key not in flask_session:
            flask_session[session_key] = datetime.now().timestamp()

        all_questions = []
        for r in all_rows:
            q = dict(r._asdict())
            q['choices'] = json.loads(q['choices_json'] or '[]')
            q['correct_answer'] = json.loads(q['correct_answer_json'] or '[]')
            all_questions.append(q)

        # ランダム抽出
        if form.random_count and form.random_count > 0 and len(all_questions) > form.random_count:
            selected = random.sample(all_questions, form.random_count)
        else:
            selected = all_questions

        flask_session[order_key] = [q['id'] for q in selected]

        response = make_response(render_template(
            'quiz/take_test.html',
            form=form,
            questions=selected
        ))
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    # --- POST（採点） ---
    start_time = flask_session.get(session_key)
    q_ids      = flask_session.get(order_key)

    if not start_time or not q_ids:
        flash("セッションエラーです。最初からやり直してください。")
        return redirect(url_for('quiz.index'))

    # 出題された問題だけ採点
    q_map = {}
    for r in all_rows:
        q = dict(r._asdict())
        q['choices'] = json.loads(q['choices_json'] or '[]')
        q['correct_answer'] = json.loads(q['correct_answer_json'] or '[]')
        q_map[q['id']] = q

    questions = [q_map[qid] for qid in q_ids if qid in q_map]

    score = 0
    correct_count = 0
    total_score = sum(q.get('points', 1) for q in questions)
    results_detail = []

    for q in questions:
        user_ans = request.form.get(f'q_{q["id"]}')
        is_correct = False

        if q['q_type'] == 'single_choice':
            if user_ans and int(user_ans) == q['correct_answer'][0]:
                is_correct = True
        elif q['q_type'] == 'multiple_choice':
            user_list = [int(x) for x in request.form.getlist(f'q_{q["id"]}')]
            if set(user_list) == set(q['correct_answer']):
                is_correct = True
        elif q['q_type'] == 'text':
         if user_ans:
            user_ans_stripped = user_ans.strip()
            if any(user_ans_stripped == str(ans).strip() for ans in q['correct_answer']):
                is_correct = True

        if is_correct:
            score += q.get('points', 1)
            correct_count += 1

        results_detail.append({
            'q_text':      q['q_text'],
            'is_correct':  is_correct,
            'correct_ans': q['correct_answer'],
            'user_ans':    user_ans,
            'q_type':      q['q_type'],
            'choices':     q['choices']
        })

    time_taken = request.form.get('time_taken', type=int) or 0

    # 作成者以外のスコアを保存（ベストスコアのみ更新）
    if form.creator_id != current_user.id:
        existing = db.execute(
            quiz_results_table.select()
            .where(quiz_results_table.c.form_id == form_id)
            .where(quiz_results_table.c.user_id == current_user.id)
        ).fetchone()

        if existing:
            if score > existing.score:
                db.execute(
                    quiz_results_table.update()
                    .where(quiz_results_table.c.id == existing.id)
                    .values(
                        score=score,
                        total=total_score,
                        correct_count=correct_count,
                        total_questions=len(questions),
                        time_taken=time_taken,
                        taken_at=datetime.now()
                    )
                )
        else:
            db.execute(
                quiz_results_table.insert().values(
                    form_id=form_id,
                    user_id=current_user.id,
                    user_name=current_user.username,
                    score=score,
                    total=total_score,
                    correct_count=correct_count,
                    total_questions=len(questions),
                    time_taken=time_taken,
                    taken_at=datetime.now()
                )
            )
        db.commit()

    # セッションのクリア
    flask_session.pop(session_key, None)
    flask_session.pop(order_key, None)
    db.close()

    return render_template(
        'quiz/result.html',
        form=form,
        score=score,
        total_score=total_score,
        correct_count=correct_count,
        total_questions=len(questions),
        results=results_detail
    )

@quiz_bp.route('/publish/<int:form_id>', methods=['POST'])
@login_required
def publish_form(form_id):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()
    if form.creator_id != current_user.id:
        abort(403)
    db.execute(
        quiz_forms_table.update()
        .where(quiz_forms_table.c.id == form_id)
        .values(is_published=1, updated_at=datetime.now())
    )
    db.commit()
    db.close()
    flash("クイズを公開しました！")
    return redirect(url_for('quiz.edit_questions', form_id=form_id))


@quiz_bp.route('/unpublish/<int:form_id>', methods=['POST'])
@login_required
def unpublish_form(form_id):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()
    if form.creator_id != current_user.id:
        abort(403)
    db.execute(
        quiz_forms_table.update()
        .where(quiz_forms_table.c.id == form_id)
        .values(is_published=0, updated_at=datetime.now())
    )
    db.commit()
    db.close()
    flash("下書きに戻しました。")
    return redirect(url_for('quiz.edit_questions', form_id=form_id))


@quiz_bp.route('/leaderboard/<int:form_id>')
@login_required
def leaderboard(form_id):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()
    if not form:
        abort(404)
    results = db.execute(
        quiz_results_table.select()
        .where(quiz_results_table.c.form_id == form_id)
        .order_by(
            quiz_results_table.c.score.desc(),
            quiz_results_table.c.time_taken.asc()
        )
    ).fetchall()
    db.close()
    return render_template('quiz/leaderboard.html', form=form, results=results)


@quiz_bp.route('/mypage')
@login_required
def mypage():
    db = get_db()
    my_forms = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.creator_id == current_user.id)
        .order_by(quiz_forms_table.c.updated_at.desc())
    ).fetchall()

    my_results = db.execute(
        quiz_results_table.select()
        .where(quiz_results_table.c.user_id == current_user.id)
        .order_by(quiz_results_table.c.taken_at.desc())
        .limit(10)
    ).fetchall()

    # タイトルを結合
    results_with_title = []
    for r in my_results:
        f = db.execute(
            quiz_forms_table.select()
            .where(quiz_forms_table.c.id == r.form_id)
        ).fetchone()
        results_with_title.append({
            'title':    f.title if f else '不明',
            'score':    r.score,
            'total':    r.total,
            'taken_at': r.taken_at,
            'form_id':  r.form_id
        })

    db.close()
    return render_template('quiz/mypage.html',
                           my_forms=my_forms,
                           results=results_with_title)
@quiz_bp.route('/edit_setup/<int:form_id>', methods=['GET', 'POST'])
@login_required
def edit_setup(form_id):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()

    if not form:
        abort(404)
    if form.creator_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        title       = request.form.get('title')
        description = request.form.get('description')
        time_limit  = request.form.get('time_limit', type=int) or 0
        show_answers = request.form.get('show_answers', type=int)
        if show_answers is None:
         show_answers = 1

        image_url = form.image_url
        file = request.files.get('icon_image')
        if file and allowed_file(file.filename):
            filename = secure_filename(f"icon_{datetime.now().timestamp()}_{file.filename}")
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            image_url = url_for('static', filename=f'uploads/quiz_imgs/{filename}')

        db.execute(
            quiz_forms_table.update()
            .where(quiz_forms_table.c.id == form_id)
            .values(
                title=title,
                description=description,
                time_limit=time_limit,
                show_answers=show_answers,
                image_url=image_url,
                updated_at=datetime.now()
            )
        )
        db.commit()
        db.close()
        flash("設定を更新しました。")
        return redirect(url_for('quiz.edit_questions', form_id=form_id))

    db.close()
    return render_template('quiz/edit_setup.html', form=form)


@quiz_bp.route('/delete/<int:form_id>', methods=['POST'])
@login_required
def delete_form(form_id):
    db = get_db()
    form = db.execute(
        quiz_forms_table.select()
        .where(quiz_forms_table.c.id == form_id)
    ).fetchone()

    if not form:
        abort(404)
    if form.creator_id != current_user.id:
        abort(403)

    # 関連する問題と結果も全部削除
    db.execute(
        quiz_questions_table.delete()
        .where(quiz_questions_table.c.form_id == form_id)
    )
    db.execute(
        quiz_results_table.delete()
        .where(quiz_results_table.c.form_id == form_id)
    )
    db.execute(
        quiz_forms_table.delete()
        .where(quiz_forms_table.c.id == form_id)
    )
    db.commit()
    db.close()
    flash(f"「{form.title}」を削除しました。")
    return redirect(url_for('quiz.mypage'))