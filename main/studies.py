"""Research schema and DB-defined templates; no unrelated tables are modified."""
import sqlite3
from pathlib import Path


def connect_studies(path):
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize_studies(db):
    # Retain the original seven columns so existing tuple-based views stay stable.
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS study_template (
            id INTEGER PRIMARY KEY, seed_key TEXT UNIQUE, name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)), owner_school TEXT)""")
        template_columns = {row[1] for row in db.execute('PRAGMA table_info(study_template)')}
        if 'owner_school' not in template_columns:
            db.execute('ALTER TABLE study_template ADD COLUMN owner_school TEXT')
        db.execute("""CREATE TABLE IF NOT EXISTS study_template_field (
            id INTEGER PRIMARY KEY, template_id INTEGER NOT NULL
                REFERENCES study_template(id) ON DELETE CASCADE,
            label TEXT NOT NULL, position INTEGER NOT NULL,
            required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0, 1)),
            UNIQUE(template_id, id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS study (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            introduce TEXT NOT NULL, filename TEXT NOT NULL, pdfpath TEXT NOT NULL,
            userid TEXT NOT NULL, time TEXT NOT NULL)""")
        field_columns = {row[1] for row in db.execute('PRAGMA table_info(study_template_field)')}
        if 'stable_key' not in field_columns:
            db.execute('ALTER TABLE study_template_field ADD COLUMN stable_key TEXT')
            db.execute('UPDATE study_template_field SET stable_key=lower(hex(randomblob(16))) WHERE stable_key IS NULL')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_study_template_field_stable_key ON study_template_field(template_id,stable_key)')
        for name, definition in {
            'heading_font_size': 'INTEGER NOT NULL DEFAULT 11 CHECK(heading_font_size BETWEEN 6 AND 36)',
            'body_font_size': 'INTEGER NOT NULL DEFAULT 10 CHECK(body_font_size BETWEEN 6 AND 36)',
            'hide_heading': 'INTEGER NOT NULL DEFAULT 0 CHECK(hide_heading IN (0,1))',
            'max_length': 'INTEGER NOT NULL DEFAULT 0 CHECK(max_length >= 0)',
            'heading_alignment': "TEXT NOT NULL DEFAULT 'left' CHECK(heading_alignment IN ('left','center','right'))",
            'body_alignment': "TEXT NOT NULL DEFAULT 'left' CHECK(body_alignment IN ('left','center','right'))",
            'heading_bold': 'INTEGER NOT NULL DEFAULT 0 CHECK(heading_bold IN (0,1))',
            'body_bold': 'INTEGER NOT NULL DEFAULT 0 CHECK(body_bold IN (0,1))',
            'field_type': "TEXT NOT NULL DEFAULT 'text' CHECK(field_type IN ('text','image'))",
            'image_size': "TEXT NOT NULL DEFAULT 'small' CHECK(image_size IN ('small','large'))",
            'image_alignment': "TEXT NOT NULL DEFAULT 'center' CHECK(image_alignment IN ('left','center','right'))",
            # 1 のとき文字の下に画像も入れられる（項目の内容「文字と画像」）
            'with_images': 'INTEGER NOT NULL DEFAULT 0 CHECK(with_images IN (0,1))',
        }.items():
            if name not in field_columns:
                db.execute(f'ALTER TABLE study_template_field ADD COLUMN {name} {definition}')
        db.execute('UPDATE study_template_field SET stable_key=lower(hex(randomblob(16))) WHERE stable_key IS NULL OR stable_key=\'\'')
        # Template items are optional; normalize legacy definitions at startup.
        db.execute('UPDATE study_template_field SET required=0 WHERE required<>0')
        columns = {row[1] for row in db.execute("PRAGMA table_info(study)")}
        additions = {
            "registration_type": "TEXT NOT NULL DEFAULT 'pdf' CHECK(registration_type IN ('pdf', 'template'))",
            "template_id": "INTEGER REFERENCES study_template(id) ON DELETE RESTRICT",
            "submission_key": "TEXT",
            "submission_kind": "TEXT NOT NULL DEFAULT 'individual' CHECK(submission_kind IN ('individual', 'group'))",
            "chat_group_id": "TEXT",
            "chat_group_name": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE study ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS study_submission_key ON study(submission_key)")
        db.execute("""CREATE TABLE IF NOT EXISTS study_submission_member (
            study_id INTEGER NOT NULL REFERENCES study(id) ON DELETE CASCADE,
            tekne_student_id TEXT NOT NULL,
            student_id_snapshot TEXT NOT NULL,
            position INTEGER NOT NULL CHECK(position >= 0),
            PRIMARY KEY(study_id, tekne_student_id),
            UNIQUE(study_id, position))""")
        db.execute("""CREATE TABLE IF NOT EXISTS study_group_access (
            study_id INTEGER NOT NULL REFERENCES study(id) ON DELETE CASCADE,
            tekne_student_id TEXT NOT NULL,
            PRIMARY KEY(study_id, tekne_student_id))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_study_group_access_student ON study_group_access(tekne_student_id,study_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS school_report_template (
            school TEXT PRIMARY KEY NOT NULL, template_id INTEGER NOT NULL REFERENCES study_template(id),
            updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS group_report_draft (
            group_id TEXT PRIMARY KEY NOT NULL, school_id TEXT NOT NULL, school_name TEXT NOT NULL,
            template_id INTEGER NOT NULL, template_snapshot TEXT NOT NULL, report_data TEXT NOT NULL,
            updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS group_report_version (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL, school_id TEXT NOT NULL,
            school_name TEXT NOT NULL, version_number INTEGER NOT NULL, template_snapshot TEXT NOT NULL,
            report_snapshot TEXT NOT NULL, saved_by TEXT NOT NULL, saved_at TEXT NOT NULL,
            UNIQUE(group_id,version_number))""")
        # kind: 'manual' は生徒の保存（最新件数だけ残す）、'scheduled' は定期自動保存（永久保存）
        version_columns = {row[1] for row in db.execute("PRAGMA table_info(group_report_version)")}
        if "kind" not in version_columns:
            db.execute("ALTER TABLE group_report_version ADD COLUMN kind TEXT NOT NULL DEFAULT 'manual'")
        if "scheduled_date" not in version_columns:
            db.execute("ALTER TABLE group_report_version ADD COLUMN scheduled_date TEXT")
        # 保存した時点のグループ名・メンバー情報（PDFの先頭に印字する情報）。古い履歴は NULL
        if "context_snapshot" not in version_columns:
            db.execute("ALTER TABLE group_report_version ADD COLUMN context_snapshot TEXT")
        # 学校ごとの「PDFの先頭に印字する情報」の設定（JSONのキー配列）
        db.execute("""CREATE TABLE IF NOT EXISTS school_report_header (
            school TEXT PRIMARY KEY NOT NULL, items_json TEXT NOT NULL,
            updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)""")
        if "style_json" not in {row[1] for row in db.execute("PRAGMA table_info(school_report_header)")}:
            db.execute("ALTER TABLE school_report_header ADD COLUMN style_json TEXT")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS group_report_version_scheduled
            ON group_report_version(group_id,scheduled_date) WHERE kind='scheduled'""")
        db.execute("""CREATE TABLE IF NOT EXISTS group_report_snapshot_day (
            school TEXT NOT NULL, day TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            processed_at TEXT, PRIMARY KEY(school,day))""")
        prune_manual_report_versions(db)
        initialize_report_review_tables(db)
        db.execute("""CREATE TABLE IF NOT EXISTS group_report_lock (
            group_id TEXT NOT NULL, field_key TEXT NOT NULL, student_id TEXT NOT NULL,
            student_name TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(group_id,field_key))""")
        db.execute("""CREATE TABLE IF NOT EXISTS group_report_asset (
            content_hash TEXT PRIMARY KEY NOT NULL, stored_name TEXT NOT NULL UNIQUE,
            content_type TEXT NOT NULL, file_size INTEGER NOT NULL, created_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS group_report_asset_access (
            group_id TEXT NOT NULL, content_hash TEXT NOT NULL REFERENCES group_report_asset(content_hash) ON DELETE CASCADE,
            PRIMARY KEY(group_id,content_hash))""")
        db.execute("""CREATE TABLE IF NOT EXISTS study_field_value (
            study_id INTEGER NOT NULL REFERENCES study(id) ON DELETE CASCADE,
            field_id INTEGER NOT NULL REFERENCES study_template_field(id) ON DELETE RESTRICT,
            value TEXT NOT NULL DEFAULT '', PRIMARY KEY(study_id, field_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS study_field_image (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_id INTEGER NOT NULL REFERENCES study(id) ON DELETE CASCADE,
            field_id INTEGER NOT NULL REFERENCES study_template_field(id) ON DELETE RESTRICT,
            position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 3),
            stored_name TEXT NOT NULL UNIQUE,
            original_name TEXT NOT NULL,
            caption TEXT NOT NULL CHECK(length(caption) BETWEEN 1 AND 50),
            image_format TEXT NOT NULL CHECK(image_format IN ('JPEG','PNG')),
            width INTEGER NOT NULL CHECK(width > 0),
            height INTEGER NOT NULL CHECK(height > 0),
            UNIQUE(study_id, field_id, position))""")
        # Only seed fields when the template is first created, never overwrite edits.
        inserted = db.execute("""INSERT OR IGNORE INTO study_template(seed_key, name)
            VALUES ('basic-research-v1', '研究レポート（基本）')""")
        if inserted.rowcount:
            labels = ["テーマ", "背景", "目的", "実験方法、実験材料道具", "実験結果",
                      "考察", "結論", "参考文献", "謝辞"]
            db.executemany("""INSERT INTO study_template_field(template_id, label, position, required)
                VALUES (?, ?, ?, 0)""", [(inserted.lastrowid, label, i) for i, label in enumerate(labels)])
        db.execute("UPDATE study_template_field SET stable_key=lower(hex(randomblob(16))) WHERE stable_key IS NULL OR stable_key=''")
        migrate_theme_fields(db)
        initialize_report_archive_tables(db)


def initialize_report_archive_tables(db):
    """成果レポートのアーカイブ（学校ごとの永久保存）。元のグループ・枠・提出の表とは結びつけず、保存した時点の値をコピーして持つ。"""
    db.execute("""CREATE TABLE IF NOT EXISTS report_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT, school TEXT NOT NULL,
        frame_id INTEGER NOT NULL DEFAULT 0, frame_name TEXT NOT NULL DEFAULT '', frame_description TEXT NOT NULL DEFAULT '',
        round_day TEXT NOT NULL, round_title TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 0,
        submission_id INTEGER NOT NULL UNIQUE,
        group_id TEXT NOT NULL, group_name TEXT NOT NULL, group_kind TEXT NOT NULL DEFAULT 'group',
        theme TEXT NOT NULL DEFAULT '', members_json TEXT NOT NULL DEFAULT '[]', custom_labels_json TEXT NOT NULL DEFAULT '{}',
        template_snapshot TEXT NOT NULL, report_snapshot TEXT NOT NULL, version_json TEXT NOT NULL DEFAULT '{}',
        submitted_at TEXT, submit_method TEXT, submitted_by TEXT,
        review_overall TEXT NOT NULL DEFAULT '', review_fields_json TEXT NOT NULL DEFAULT '{}',
        review_result TEXT, returned_at TEXT, returned_by TEXT,
        archived_at TEXT NOT NULL, archived_by TEXT NOT NULL, search_text TEXT NOT NULL DEFAULT '')""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_report_archive_school_day ON report_archive(school,round_day DESC,id DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_report_archive_school_archived ON report_archive(school,archived_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS report_archive_asset (
        archive_id INTEGER NOT NULL REFERENCES report_archive(id) ON DELETE CASCADE, content_hash TEXT NOT NULL,
        PRIMARY KEY(archive_id,content_hash))""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_report_archive_asset_hash ON report_archive_asset(content_hash)")
    db.execute("""CREATE TABLE IF NOT EXISTS report_archive_deletion_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, school TEXT NOT NULL, archive_id INTEGER NOT NULL,
        group_name TEXT NOT NULL, round_title TEXT NOT NULL, round_day TEXT NOT NULL,
        deleted_at TEXT NOT NULL, deleted_by TEXT NOT NULL, deleted_role TEXT NOT NULL)""")


MANUAL_REPORT_VERSION_LIMIT = 10


def initialize_report_review_tables(db):
    """成果レポートの提出日・提出・先生のレビューのテーブル（処理は report_reviews.py）。"""
    from workspace_frames import ensure_frame_schema
    ensure_frame_schema(db)
    # 提出日：定期自動保存日のうち、提出日として指定された日（名前付き）
    db.execute("""CREATE TABLE IF NOT EXISTS group_report_submission_day (
        school TEXT NOT NULL, day TEXT NOT NULL, title TEXT NOT NULL,
        created_by TEXT NOT NULL, created_at TEXT NOT NULL, finalized_at TEXT,
        PRIMARY KEY(school,day))""")
    # 提出：attempt=0 が本提出、1以上が再提出。version_id が NULL なら未提出（再提出待ちなど）
    db.execute("""CREATE TABLE IF NOT EXISTS group_report_submission (
        id INTEGER PRIMARY KEY AUTOINCREMENT, school TEXT NOT NULL, group_id TEXT NOT NULL,
        round_day TEXT NOT NULL, attempt INTEGER NOT NULL CHECK(attempt >= 0), due_day TEXT NOT NULL,
        version_id INTEGER, method TEXT CHECK(method IN ('manual','auto')), submitted_by TEXT, submitted_at TEXT,
        finalized_at TEXT, created_at TEXT NOT NULL, UNIQUE(group_id,round_day,attempt))""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_group_report_submission_round ON group_report_submission(school,round_day)")
    # レビュー：提出版ごとに1つ。result が NULL の間は先生だけが見られる下書き
    db.execute("""CREATE TABLE IF NOT EXISTS group_report_review (
        submission_id INTEGER PRIMARY KEY NOT NULL REFERENCES group_report_submission(id) ON DELETE CASCADE,
        overall_comment TEXT NOT NULL DEFAULT '', field_comments TEXT NOT NULL DEFAULT '{}',
        result TEXT CHECK(result IN ('returned','resubmit')), returned_at TEXT, returned_by TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS group_report_review_editor (
        submission_id INTEGER NOT NULL REFERENCES group_report_submission(id) ON DELETE CASCADE,
        teacher_id TEXT NOT NULL, first_edited_at TEXT NOT NULL, last_edited_at TEXT NOT NULL,
        PRIMARY KEY(submission_id,teacher_id))""")
    # レビュー中のロック：「レビューを終了」か「返却」で外れる。どの先生でも強制的に外せる
    db.execute("""CREATE TABLE IF NOT EXISTS group_report_review_lock (
        submission_id INTEGER PRIMARY KEY NOT NULL REFERENCES group_report_submission(id) ON DELETE CASCADE,
        teacher_id TEXT NOT NULL, locked_at TEXT NOT NULL)""")
    # 期限前リマインドの送信記録（同じ期限・同じ区分は1回だけ）
    db.execute("""CREATE TABLE IF NOT EXISTS group_report_reminder_sent (
        group_id TEXT NOT NULL, round_day TEXT NOT NULL, attempt INTEGER NOT NULL, offset_days INTEGER NOT NULL,
        sent_at TEXT NOT NULL, PRIMARY KEY(group_id,round_day,attempt,offset_days))""")
    # 生徒が返却されたレビューを見たかどうか（ワークスペースで「新しいレビュー」と知らせるため）
    db.execute("""CREATE TABLE IF NOT EXISTS group_report_review_seen (
        submission_id INTEGER NOT NULL REFERENCES group_report_submission(id) ON DELETE CASCADE,
        student_id TEXT NOT NULL, seen_at TEXT NOT NULL, PRIMARY KEY(submission_id,student_id))""")
    add_frame_to_report_days(db)
    # 再提出の行が「枠なし」で作られていた不具合の補正：再提出は、同じ提出日の本提出と同じ枠の提出回にする
    db.execute("""UPDATE group_report_submission SET round_frame=(
            SELECT p.round_frame FROM group_report_submission p
            WHERE p.group_id=group_report_submission.group_id AND p.round_day=group_report_submission.round_day AND p.attempt=0)
        WHERE attempt>0 AND EXISTS (
            SELECT 1 FROM group_report_submission p
            WHERE p.group_id=group_report_submission.group_id AND p.round_day=group_report_submission.round_day AND p.attempt=0
              AND p.round_frame<>group_report_submission.round_frame)""")
    # 提出は提出した時点で確定する（先生が返却するまで出し直せない）。以前の「期限前の提出」も確定済みにする
    db.execute("UPDATE group_report_submission SET finalized_at=COALESCE(submitted_at,created_at) WHERE version_id IS NOT NULL AND finalized_at IS NULL")


def _rebuild_with_frame(db, table, create_sql, columns):
    """日付の表に枠（frame_id、0＝枠なし）を加え、主キーを (school, frame_id, day) にする。"""
    if 'frame_id' in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
        return
    db.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    db.execute(create_sql)
    db.execute(f"INSERT INTO {table}({columns},frame_id) SELECT {columns},0 FROM {table}_old")
    db.execute(f"DROP TABLE {table}_old")


def add_frame_to_report_days(db):
    """自動保存日・提出日を枠ごとにする。今ある日付は「枠なし」（frame_id=0）の日付になる。"""
    _rebuild_with_frame(db, 'group_report_snapshot_day', """CREATE TABLE group_report_snapshot_day (
        school TEXT NOT NULL, day TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
        processed_at TEXT, frame_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(school,frame_id,day))""",
        'school,day,created_by,created_at,processed_at')
    _rebuild_with_frame(db, 'group_report_submission_day', """CREATE TABLE group_report_submission_day (
        school TEXT NOT NULL, day TEXT NOT NULL, title TEXT NOT NULL,
        created_by TEXT NOT NULL, created_at TEXT NOT NULL, finalized_at TEXT,
        frame_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(school,frame_id,day))""",
        'school,day,title,created_by,created_at,finalized_at')
    # 提出がどの枠の提出回のものか（提出後に枠を変えても、元の提出回に残る）
    if 'round_frame' not in {row[1] for row in db.execute("PRAGMA table_info(group_report_submission)")}:
        db.execute("ALTER TABLE group_report_submission ADD COLUMN round_frame INTEGER NOT NULL DEFAULT 0")


def prune_manual_report_versions(db, group_id=None, keep=MANUAL_REPORT_VERSION_LIMIT):
    """生徒の保存（kind='manual'）をグループごとに最新 keep 件だけ残し、それより古いものを削除する。"""
    where, params = ("AND group_id=?", (group_id,)) if group_id is not None else ("", ())
    return db.execute(f"""DELETE FROM group_report_version WHERE id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY group_id ORDER BY version_number DESC) AS rank
            FROM group_report_version WHERE kind='manual' {where})
        WHERE rank > ?)""", (*params, keep)).rowcount


# 項目の内容：text＝文字だけ、image＝画像だけ、both＝文字と画像（文字の下に画像）
FIELD_TYPES = ('text', 'image', 'both')


def field_has_text(field_type):
    return field_type in ('text', 'both')


def field_has_images(field_type):
    return field_type in ('image', 'both')


def stored_field_type(field_type):
    """画面・APIの項目の内容を、DBの (field_type, with_images) に変換する。"""
    return ('text', 1) if field_type == 'both' else (field_type, 0)


def get_templates(db, include_inactive=False):
    return [{"id": row[0], "name": row[1], "active": bool(row[2]), "fields": [
        {"id": field[0], "stable_key": field[14], "label": field[1], "required": False,
         "heading_font_size": field[3], "body_font_size": field[4],
         "hide_heading": bool(field[5]), "max_length": field[6],
         "heading_alignment": field[7], "body_alignment": field[8],
         "heading_bold": bool(field[9]), "body_bold": bool(field[10]),
         "field_type": "both" if field[11] == "text" and field[15] else field[11],
         "image_size": field[12], "image_alignment": field[13]}
        for field in db.execute("""SELECT id, label, required, heading_font_size, body_font_size, hide_heading, max_length, heading_alignment, body_alignment, heading_bold, body_bold, field_type, image_size, image_alignment, stable_key, with_images FROM study_template_field
            WHERE template_id = ? ORDER BY position, id""", (row[0],))
    ]} for row in db.execute("SELECT id, name, active FROM study_template WHERE active = 1 OR ? ORDER BY id", (include_inactive,))]


# 全レポートに必ず入る「テーマ」項目。固定の識別キーで先頭に置く（古いテンプレートの「テーマ」項目もテーマとして扱う）
THEME_KEY = "theme"
THEME_LABEL = "テーマ"


def default_theme_field():
    return {"stable_key": THEME_KEY, "label": THEME_LABEL, "required": False, "heading_font_size": 11,
            "body_font_size": 10, "hide_heading": False, "max_length": 0, "heading_alignment": "left",
            "body_alignment": "left", "heading_bold": False, "body_bold": False, "field_type": "text",
            "image_size": "small", "image_alignment": "center"}


def is_theme_field(field):
    """識別キーが theme の項目、または見出しが「テーマ」の文字項目。"""
    if field.get("stable_key") == THEME_KEY:
        return True
    return (field.get("label") or "").strip() == THEME_LABEL and field.get("field_type", "text") in ("text", "both")


def find_theme_field(fields):
    explicit = next((f for f in fields if f.get("stable_key") == THEME_KEY), None)
    return explicit or next((f for f in fields if is_theme_field(f)), None)


def ensure_theme_field(fields):
    """項目の一覧（dict）に、テーマ項目を先頭に1つだけ置いた一覧を返す。なければ追加し、あれば先頭へ移して文字だけにする。"""
    fields = list(fields)
    theme = find_theme_field(fields)
    if theme is None:
        return [default_theme_field(), *fields]
    rest = [f for f in fields if f is not theme]
    return [{**theme, "field_type": "text"}, *rest]


def theme_value(template, report):
    """保存されたテンプレートとレポート本文から、テーマの文字列を取り出す（なければ空）。"""
    field = find_theme_field(template.get("fields", []))
    if field is None or not isinstance(report, dict):
        return ""
    item = report.get(field.get("stable_key"))
    value = item.get("value") if isinstance(item, dict) else ""
    return (value or "").strip() if isinstance(value, str) else ""


def migrate_theme_fields(db):
    """既存のテンプレートと書きかけのレポートに、テーマ項目がなければ先頭に足す（何度呼んでもよい）。"""
    import json
    columns = "heading_font_size,body_font_size,hide_heading,max_length,heading_alignment,body_alignment,heading_bold,body_bold,field_type,image_size,image_alignment,stable_key,with_images"
    for (template_id,) in db.execute("SELECT id FROM study_template").fetchall():
        fields = [{"stable_key": r[0], "label": r[1], "field_type": r[2]} for r in db.execute(
            "SELECT stable_key,label,field_type FROM study_template_field WHERE template_id=? ORDER BY position,id", (template_id,))]
        if find_theme_field(fields) is not None:
            continue
        db.execute("UPDATE study_template_field SET position=position+1 WHERE template_id=?", (template_id,))
        db.execute(f"INSERT INTO study_template_field(template_id,label,position,required,{columns}) VALUES(?,?,0,0,11,10,0,0,'left','left',0,0,'text','small','center',?,0)",
                   (template_id, THEME_LABEL, THEME_KEY))
    for group_id, snapshot, data in db.execute("SELECT group_id,template_snapshot,report_data FROM group_report_draft").fetchall():
        try:
            template, values = json.loads(snapshot), json.loads(data)
        except (TypeError, ValueError):
            continue
        if not isinstance(template, dict) or not isinstance(values, dict) or find_theme_field(template.get("fields", [])) is not None:
            continue
        template["fields"] = [default_theme_field(), *template.get("fields", [])]
        values[THEME_KEY] = {"value": "", "images": []}
        db.execute("UPDATE group_report_draft SET template_snapshot=?,report_data=?,revision=revision+1 WHERE group_id=?",
                   (json.dumps(template, ensure_ascii=False), json.dumps(values, ensure_ascii=False), group_id))


def get_submission_collaboration(db, study_ids):
    ids = list(dict.fromkeys(int(study_id) for study_id in study_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(f"""SELECT id, submission_kind, chat_group_id, chat_group_name
        FROM study WHERE id IN ({placeholders}) AND submission_kind='group'""", ids).fetchall()
    result = {row[0]: {"kind": row[1], "group_id": row[2], "group_name": row[3], "submitters": []}
              for row in rows}
    if result:
        group_ids = list(result)
        member_placeholders = ",".join("?" for _ in group_ids)
        for study_id, student_id, student_name in db.execute(f"""SELECT member.study_id, member.student_id_snapshot, student.name
            FROM study_submission_member member
            LEFT JOIN student ON student.id = member.student_id_snapshot
            WHERE member.study_id IN ({member_placeholders})
            ORDER BY member.study_id, member.position""", group_ids):
            result[study_id]["submitters"].append(student_name or student_id)
    return result


def study_file_path(uploads, filename):
    """Only a file directly inside uploads can belong to a research submission."""
    root = Path(uploads).resolve()
    target = (root / filename).resolve()
    if target.parent != root:
        raise ValueError("Invalid research file path")
    return target


def study_pdf_path(uploads, filename):
    """Only a PDF directly inside uploads can belong to a research submission."""
    target = study_file_path(uploads, filename)
    if target.suffix.lower() != ".pdf":
        raise ValueError("Invalid research PDF path")
    return target


def validate_pdf(upload):
    # Do not trust MIME supplied by the client. Check extension, header and trailer.
    import re
    if not upload.filename or Path(upload.filename).suffix.lower() != ".pdf":
        raise ValueError("PDFファイルを選択してください。")
    stream = upload.file
    stream.seek(0)
    header = stream.read(16)
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(max(0, size - 4096))
    trailer = stream.read()
    stream.seek(0)
    if not re.match(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n)", header) or not re.search(
        rb"startxref\s+\d+\s+%%EOF\s*\Z", trailer
    ):
        raise ValueError("PDF形式を確認できません。正常なPDFファイルを選択してください。")
