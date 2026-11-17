"""ワークスペースの枠（例：「2026年度 2年生 理数科」）。

枠は生徒ではなく、ワークスペース（グループ・個人スペース）が所属する入れ物。
1つのワークスペースは0〜1個の枠に所属する（所属しないものは「枠なし」）。
枠の作成・変更・削除と、先生の画面からワークスペースの枠を付け替えるのは super_teacher だけ。
生徒はワークスペースのメンバーなら、そのワークスペースの枠をいつでも変えられる。
"""
import datetime
import re
import secrets
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request

FRAME_CSRF_KEY = 'workspace_frame_csrf'
NAME_MAX = 50
DESCRIPTION_MAX = 200
FILTER_ALL = 'all'
FILTER_NONE = 'none'
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def ensure_frame_schema(db):
    """枠の表と、ワークスペースの所属先の列を用意する（何度呼んでもよい）。"""
    db.execute('''CREATE TABLE IF NOT EXISTS workspace_frame (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(school, name)
    )''')
    if _table_exists(db, 'tekne_chat_group') and not has_frame_column(db):
        db.execute('ALTER TABLE tekne_chat_group ADD COLUMN frame_id INTEGER')


def has_frame_column(db):
    if not _table_exists(db, 'tekne_chat_group'):
        return False
    return 'frame_id' in {row[1] for row in db.execute('PRAGMA table_info(tekne_chat_group)')}


def clean_frame_name(value):
    if not isinstance(value, str):
        return None
    name = ' '.join(value.replace('　', ' ').split())
    if not name or len(name) > NAME_MAX or _CONTROL.search(name):
        return None
    return name


def clean_frame_description(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        return None
    text = value.strip().replace('\r\n', '\n')
    if len(text) > DESCRIPTION_MAX or _CONTROL.search(text.replace('\n', '')):
        return None
    return text


def school_frames(db, school, *, with_counts=False):
    """学校の枠を名前順で返す。"""
    if not _table_exists(db, 'workspace_frame'):
        return []
    rows = db.execute('SELECT id,name,description FROM workspace_frame WHERE school=? ORDER BY name,id', (school,)).fetchall()
    frames = [{'id': r[0], 'name': r[1], 'description': r[2] or ''} for r in rows]
    if with_counts:
        counts = {}
        if has_frame_column(db):
            counts = dict(db.execute('''SELECT g.frame_id,COUNT(*) FROM tekne_chat_group g
                JOIN tekne_school_identity s ON s.id=g.school_id
                WHERE s.school_name=? AND g.frame_id IS NOT NULL GROUP BY g.frame_id''', (school,)).fetchall())
        for frame in frames:
            frame['count'] = counts.get(frame['id'], 0)
    return frames


def frame_in_school(db, school, frame_id):
    """frame_id がその学校の枠なら枠の情報を返す。None は「枠なし」。"""
    if frame_id is None:
        return None
    try:
        frame_id = int(frame_id)
    except (TypeError, ValueError):
        raise HTTPException(422, '枠を選び直してください。')
    row = db.execute('SELECT id,name,description FROM workspace_frame WHERE id=? AND school=?', (frame_id, school)).fetchone()
    if row is None:
        raise HTTPException(422, '選んだ枠が見つかりません。画面を再読み込みしてください。')
    return {'id': row[0], 'name': row[1], 'description': row[2] or ''}


def parse_frame_id(value):
    """フォームやJSONの値を枠ID（int）か None（枠なし）にする。"""
    if value in (None, '', FILTER_NONE):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, '枠を選び直してください。')


def create_frame(db, school, name, description, actor, now=None):
    clean, text = clean_frame_name(name), clean_frame_description(description)
    if clean is None:
        raise HTTPException(422, f'枠の名前は1〜{NAME_MAX}文字で入力してください。')
    if text is None:
        raise HTTPException(422, f'説明は{DESCRIPTION_MAX}文字以内で入力してください。')
    if db.execute('SELECT 1 FROM workspace_frame WHERE school=? AND name=?', (school, clean)).fetchone():
        raise HTTPException(409, '同じ名前の枠がすでにあります。')
    now = now or _now()
    cursor = db.execute('INSERT INTO workspace_frame(school,name,description,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                        (school, clean, text, actor, now, now))
    return {'id': cursor.lastrowid, 'name': clean, 'description': text}


def update_frame(db, school, frame_id, name, description, now=None):
    frame_in_school(db, school, frame_id)
    clean, text = clean_frame_name(name), clean_frame_description(description)
    if clean is None:
        raise HTTPException(422, f'枠の名前は1〜{NAME_MAX}文字で入力してください。')
    if text is None:
        raise HTTPException(422, f'説明は{DESCRIPTION_MAX}文字以内で入力してください。')
    if db.execute('SELECT 1 FROM workspace_frame WHERE school=? AND name=? AND id<>?', (school, clean, int(frame_id))).fetchone():
        raise HTTPException(409, '同じ名前の枠がすでにあります。')
    db.execute('UPDATE workspace_frame SET name=?,description=?,updated_at=? WHERE id=? AND school=?',
               (clean, text, now or _now(), int(frame_id), school))
    return {'id': int(frame_id), 'name': clean, 'description': text}


def frame_has_days(db, school, frame_id):
    """枠に自動保存日・提出日が1つでもあるか（過去の日付も含む）。"""
    for table in ('group_report_snapshot_day', 'group_report_submission_day'):
        if _table_exists(db, table) and 'frame_id' in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
            if db.execute(f'SELECT 1 FROM {table} WHERE school=? AND frame_id=? LIMIT 1', (school, int(frame_id))).fetchone():
                return True
    return False


def delete_frame(db, school, frame_id):
    """枠を削除する。所属していたワークスペースは「枠なし」に戻る。自動保存日・提出日がある枠は削除できない。"""
    frame_in_school(db, school, frame_id)
    if frame_has_days(db, school, frame_id):
        raise HTTPException(409, '自動保存日・提出日が設定されている枠は削除できません。')
    moved = 0
    if has_frame_column(db):
        moved = db.execute('UPDATE tekne_chat_group SET frame_id=NULL WHERE frame_id=?', (int(frame_id),)).rowcount
    db.execute('DELETE FROM workspace_frame WHERE id=? AND school=?', (int(frame_id), school))
    return {'deleted': int(frame_id), 'moved': moved}


def set_workspace_frame(db, school, group_id, frame_id):
    """ワークスペースの枠を変える（権限の確認は呼び出し側で行う）。"""
    frame = frame_in_school(db, school, frame_id)
    exists = db.execute('''SELECT 1 FROM tekne_chat_group g JOIN tekne_school_identity s ON s.id=g.school_id
        WHERE g.id=? AND s.school_name=?''', (group_id, school)).fetchone()
    if not exists:
        raise HTTPException(404, 'ワークスペースが見つかりません。')
    db.execute('UPDATE tekne_chat_group SET frame_id=? WHERE id=?', (frame['id'] if frame else None, group_id))
    return {'id': group_id, 'frame_id': frame['id'] if frame else None, 'frame_name': frame['name'] if frame else None}


def member_names(db, group_id):
    """ワークスペースの今のメンバーの名前（名前がなければログインID）。参加した順。"""
    has_student = _table_exists(db, 'student')
    name = "COALESCE(NULLIF(st.name,''),i.login_id)" if has_student else 'i.login_id'
    join = 'LEFT JOIN student st ON st.id=i.login_id' if has_student else ''
    rows = db.execute(f'''SELECT {name} FROM tekne_chat_member m
        JOIN tekne_student_identity i ON i.id=m.student_id {join}
        WHERE m.group_id=? AND m.left_at IS NULL AND i.deleted_at IS NULL AND i.login_id IS NOT NULL
        ORDER BY m.joined_at,i.login_id''', (group_id,)).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------- 枠ごとの自動保存日・提出日
# 日付の表では枠を frame_id（整数）で表し、0 を「枠なし」とする。

NO_FRAME_KEY = 0


def frame_key_filter(key):
    """日付の表の枠（0＝枠なし）を、school_groups などの絞り込みの値にする。"""
    return FILTER_NONE if not key else str(key)


def parse_frame_key(db, school, value):
    """画面から来た枠（'none'・''・None＝枠なし、または枠ID）を、日付の表の枠（整数）にする。"""
    frame_id = parse_frame_id(value)
    if frame_id is None:
        return NO_FRAME_KEY
    return frame_in_school(db, school, frame_id)['id']


def group_frame_key(db, group_id):
    """ワークスペースが今所属している枠（0＝枠なし）。"""
    if not has_frame_column(db):
        return NO_FRAME_KEY
    row = db.execute('SELECT frame_id FROM tekne_chat_group WHERE id=?', (group_id,)).fetchone()
    return int(row[0]) if row and row[0] else NO_FRAME_KEY


def frame_label(db, school, key):
    if not key:
        return '枠なし'
    row = db.execute('SELECT name FROM workspace_frame WHERE id=? AND school=?', (int(key), school)).fetchone()         if _table_exists(db, 'workspace_frame') else None
    return row[0] if row else '（削除された枠）'


# ---------------------------------------------------------------- 先生の画面の絞り込み

def frame_filter(db, school, value):
    """クエリ文字列 ?frame= の値を解釈する。不正な値は「すべて」として扱う。"""
    frames = school_frames(db, school)
    selected = FILTER_ALL
    if value == FILTER_NONE:
        selected = FILTER_NONE
    elif value not in (None, '', FILTER_ALL):
        try:
            frame_id = int(value)
        except (TypeError, ValueError):
            frame_id = None
        if any(f['id'] == frame_id for f in frames):
            selected = str(frame_id)
    options = [{'value': FILTER_ALL, 'label': 'すべての枠'}] + \
              [{'value': str(f['id']), 'label': f['name']} for f in frames] + \
              [{'value': FILTER_NONE, 'label': '枠なし'}]
    label = next(o['label'] for o in options if o['value'] == selected)
    return {'value': selected, 'options': options, 'label': label,
            'query': '' if selected == FILTER_ALL else f'?frame={selected}'}


def matches_frame(frame_id, selected):
    if selected == FILTER_ALL:
        return True
    if selected == FILTER_NONE:
        return frame_id is None
    return frame_id is not None and str(frame_id) == str(selected)


# ---------------------------------------------------------------- ルーター（先生用）

def build_workspace_frame_router(database_path, templates):
    from studies import connect_studies  # studies からもこのモジュールを読むため、ここで読み込む
    router = APIRouter()

    def super_teacher(request):
        if request.session.get('teacher_login') is not True:
            raise HTTPException(403, '先生としてログインしてください。')
        teacher_id = request.session.get('teacher_id')
        with closing(connect_studies(database_path)) as db:
            row = db.execute('SELECT school,role FROM teacher WHERE id=?', (teacher_id,)).fetchone()
        if row is None or row[1] != 'super_teacher':
            raise HTTPException(403, 'super_teacherのみ利用できます。')
        return teacher_id, row[0]

    def check_csrf(request, body):
        expected = request.session.get(FRAME_CSRF_KEY, '')
        token = body.get('csrf_token') if isinstance(body, dict) else None
        if not expected or not isinstance(token, str) or not secrets.compare_digest(expected, token):
            raise HTTPException(403, '画面を再読み込みしてください。')

    async def json_body(request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, '入力内容を確認してください。')
        if not isinstance(body, dict):
            raise HTTPException(422, '入力内容を確認してください。')
        check_csrf(request, body)
        return body

    def render(request, name, teacher_id, school, **extra):
        return templates.TemplateResponse(request=request, name=name, context={
            'request': request, 'school': school, 'teacher_id': teacher_id, 'teacher_role': 'super_teacher',
            'csrf_token': request.session.setdefault(FRAME_CSRF_KEY, secrets.token_urlsafe(32)),
            'name_max': NAME_MAX, 'description_max': DESCRIPTION_MAX, **extra})

    @router.get('/teacher/workspace-frames')
    async def frames_page(request: Request):
        """ワークスペースの所属（枠の付け替え）。枠の追加・一覧へのボタンもここに置く。"""
        teacher_id, school = super_teacher(request)
        from report_reviews import group_display_name, school_groups
        with closing(connect_studies(database_path)) as db:
            frames = school_frames(db, school)
            groups = school_groups(db, school)
            for group in groups:
                group['display_name'] = group_display_name(group)
                # グループはメンバーの名前を名前の右に薄く出す（個人スペースは表示名に本人の名前が入っている）
                group['members'] = member_names(db, group['id']) if group['kind'] != 'personal' else []
        return render(request, 'teacher/workspace_frames.html', teacher_id, school, frames=frames, groups=groups)

    @router.get('/teacher/workspace-frames/new')
    async def frame_new_page(request: Request):
        teacher_id, school = super_teacher(request)
        return render(request, 'teacher/workspace_frame_new.html', teacher_id, school)

    @router.get('/teacher/workspace-frames/list')
    async def frame_list_page(request: Request):
        teacher_id, school = super_teacher(request)
        with closing(connect_studies(database_path)) as db:
            frames = school_frames(db, school, with_counts=True)
        return render(request, 'teacher/workspace_frame_list.html', teacher_id, school, frames=frames)

    @router.post('/teacher/api/workspace-frames')
    async def frame_create(request: Request):
        teacher_id, school = super_teacher(request)
        body = await json_body(request)
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return create_frame(db, school, body.get('name'), body.get('description'), teacher_id)

    @router.post('/teacher/api/workspace-frames/{frame_id}')
    async def frame_update(request: Request, frame_id: int):
        _, school = super_teacher(request)
        body = await json_body(request)
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return update_frame(db, school, frame_id, body.get('name'), body.get('description'))

    @router.post('/teacher/api/workspace-frames/{frame_id}/delete')
    async def frame_delete(request: Request, frame_id: int):
        _, school = super_teacher(request)
        await json_body(request)
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return delete_frame(db, school, frame_id)

    @router.post('/teacher/api/workspaces/{group_id}/frame')
    async def workspace_frame(request: Request, group_id: str):
        _, school = super_teacher(request)
        body = await json_body(request)
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return set_workspace_frame(db, school, group_id, parse_frame_id(body.get('frame_id')))

    return router
