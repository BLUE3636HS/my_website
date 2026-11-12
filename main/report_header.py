"""成果レポートPDFの先頭に印字する情報（学校ごとに先生が選ぶ）。"""
import datetime
import json
import sqlite3

JST = datetime.timezone(datetime.timedelta(hours=9))
MAX_HEADER_ITEMS = 20

# キー → 表示名。member_* と custom:<id> はメンバーごとの情報
BASE_HEADER_OPTIONS = [
    ('saved_at', '保存日時'),
    ('school_name', '学校名'),
    ('group_name', 'グループ名'),
    ('version', 'バージョン'),
    ('save_kind', '保存の種類'),
    ('saved_by', '保存者'),
    ('member_id', 'メンバーのユーザーID'),
    ('member_name', 'メンバーの名前'),
    ('member_furigana', 'メンバーのふりがな'),
]
# 設定していない学校は、これまでの「保存情報」と同じ内容を印字する
DEFAULT_HEADER_ITEMS = ['school_name', 'group_name', 'version', 'saved_at', 'saved_by']
MEMBER_KEYS = {'member_id', 'member_name', 'member_furigana'}
# 先頭の情報全体の文字サイズ・配置・太字（全行で共通）
DEFAULT_HEADER_STYLE = {'font_size': 11, 'alignment': 'left', 'bold': False}


def normalize_header_style(value):
    """保存済みの値を読み取る。不正・欠けている値は既定値にする。"""
    style = dict(DEFAULT_HEADER_STYLE)
    if isinstance(value, dict):
        size = value.get('font_size')
        if isinstance(size, int) and not isinstance(size, bool) and 6 <= size <= 36:
            style['font_size'] = size
        if value.get('alignment') in ('left', 'center', 'right'):
            style['alignment'] = value['alignment']
        if isinstance(value.get('bold'), bool):
            style['bold'] = value['bold']
    return style


def is_member_key(key):
    return key in MEMBER_KEYS or key.startswith('custom:')


def custom_fields(db, school):
    try:
        return [(row[0], row[1]) for row in db.execute(
            'SELECT id,label FROM student_custom_field WHERE school=? AND deleted_at IS NULL ORDER BY id', (school,))]
    except sqlite3.OperationalError:  # 生徒の追加項目の機能が未初期化のDB
        return []


def header_options(db, school):
    options = [{'key': key, 'label': label} for key, label in BASE_HEADER_OPTIONS]
    options += [{'key': f'custom:{field_id}', 'label': f'メンバーの{label}'} for field_id, label in custom_fields(db, school)]
    return options


def validate_header_items(db, school, items):
    allowed = {item['key'] for item in header_options(db, school)}
    if len(items) > MAX_HEADER_ITEMS:
        raise ValueError(f'印字する情報は{MAX_HEADER_ITEMS}個までです。')
    if len(set(items)) != len(items):
        raise ValueError('印字する情報が重複しています。')
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise ValueError('選べない情報が含まれています。画面を再読み込みしてください。')
    return list(items)


def school_header_items(db, school):
    row = db.execute('SELECT items_json FROM school_report_header WHERE school=?', (school,)).fetchone()
    if row is None:
        return list(DEFAULT_HEADER_ITEMS)
    try:
        items = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return list(DEFAULT_HEADER_ITEMS)
    return [item for item in items if isinstance(item, str)]


def school_header_style(db, school):
    row = db.execute('SELECT style_json FROM school_report_header WHERE school=?', (school,)).fetchone()
    try:
        return normalize_header_style(json.loads(row[0]) if row and row[0] else None)
    except (TypeError, json.JSONDecodeError):
        return dict(DEFAULT_HEADER_STYLE)


def save_school_header_items(db, school, items, actor, style=None):
    style = normalize_header_style(style)
    db.execute('''INSERT INTO school_report_header(school,items_json,updated_at,updated_by,style_json) VALUES(?,?,?,?,?)
        ON CONFLICT(school) DO UPDATE SET items_json=excluded.items_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by,style_json=excluded.style_json''',
               (school, json.dumps(items), datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), actor, json.dumps(style)))


def _display_value(value_json):
    try:
        value = json.loads(value_json)
    except (TypeError, json.JSONDecodeError):
        return ''
    if value is True:
        return 'はい'
    if value in (None, False):
        return ''
    if isinstance(value, list):
        return '・'.join(str(item) for item in value)
    return str(value)


def group_context_snapshot(db, group_id, school_name):
    """保存した時点のグループ名とメンバー情報（ID・名前・ふりがな・学校の追加項目）。"""
    fields = custom_fields(db, school_name)
    members = []
    try:
        group = db.execute('SELECT name FROM tekne_chat_group WHERE id=?', (group_id,)).fetchone()
        rows = db.execute('''SELECT i.login_id, s.name, s.furigana FROM tekne_chat_member m
        JOIN tekne_student_identity i ON i.id=m.student_id
        LEFT JOIN student s ON s.id=i.login_id
        WHERE m.group_id=? AND m.left_at IS NULL AND i.login_id IS NOT NULL AND i.deleted_at IS NULL
        ORDER BY m.joined_at, i.login_id''', (group_id,)).fetchall()
    except sqlite3.OperationalError:  # チャット機能が未初期化のDB
        group, rows = None, []
    for login_id, name, furigana in rows:
        values = {}
        if fields:
            values = {str(field_id): _display_value(value_json) for field_id, value_json in db.execute(
                'SELECT field_id,value_json FROM student_custom_field_value WHERE student_id=?', (login_id,))}
        members.append({'id': login_id, 'name': name or '', 'furigana': furigana or '',
                        'custom': {str(field_id): values.get(str(field_id), '') for field_id, _ in fields}})
    return {'group_name': group[0] if group else '', 'members': members,
            'custom_labels': {str(field_id): label for field_id, label in fields}}


def format_saved_at(value):
    try:
        moment = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or ''
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(JST).strftime('%Y/%m/%d %H:%M')


def header_text(items, version, context):
    """選ばれた情報を「名前：値」の行にする。メンバーの情報は1人1行にまとめる。"""
    labels = dict(BASE_HEADER_OPTIONS)
    custom_labels = context.get('custom_labels', {})
    kind = '定期保存' if version.get('kind') == 'scheduled' else '生徒保存'
    if version.get('kind') == 'scheduled' and version.get('scheduled_date'):
        kind += f"（{version['scheduled_date'].replace('-', '/')}）"
    values = {
        'saved_at': format_saved_at(version.get('saved_at')),
        'school_name': version.get('school_name', ''),
        'group_name': context.get('group_name') or version.get('group_id', ''),
        'version': str(version.get('version_number', '')),
        'save_kind': kind,
        'saved_by': version.get('saved_by', ''),
    }
    lines, member_items, member_at = [], [], None
    for item in items:
        if is_member_key(item):
            if member_at is None:
                member_at = len(lines)
                lines.append(None)
            member_items.append(item)
        elif item in values:
            lines.append(f'{labels[item]}：{values[item]}')
    if member_at is not None:
        member_lines = ['メンバー：']
        for member in context.get('members', []):
            parts = []
            for item in member_items:
                if item == 'member_id': parts.append(member.get('id', ''))
                elif item == 'member_name': parts.append(member.get('name', ''))
                elif item == 'member_furigana': parts.append(member.get('furigana', ''))
                else:
                    field_id = item.split(':', 1)[1]
                    if field_id in custom_labels:
                        value = member.get('custom', {}).get(field_id, '')
                        parts.append(f'{custom_labels[field_id]} {value}' if value else '')
            parts = [part for part in parts if part]
            member_lines.append('・' + ('／'.join(parts) if parts else '（未登録）'))
        if len(member_lines) == 1:
            member_lines.append('・（メンバーなし）')
        lines[member_at] = '\n'.join(member_lines)
    return '\n'.join(lines)
