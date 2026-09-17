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
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)))""")
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
        }.items():
            if name not in field_columns:
                db.execute(f'ALTER TABLE study_template_field ADD COLUMN {name} {definition}')
        columns = {row[1] for row in db.execute("PRAGMA table_info(study)")}
        additions = {
            "registration_type": "TEXT NOT NULL DEFAULT 'pdf' CHECK(registration_type IN ('pdf', 'template'))",
            "template_id": "INTEGER REFERENCES study_template(id) ON DELETE RESTRICT",
            "submission_key": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE study ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS study_submission_key ON study(submission_key)")
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


def get_templates(db, include_inactive=False):
    return [{"id": row[0], "name": row[1], "active": bool(row[2]), "fields": [
        {"id": field[0], "label": field[1], "required": bool(field[2]),
         "heading_font_size": field[3], "body_font_size": field[4],
         "hide_heading": bool(field[5]), "max_length": field[6],
         "heading_alignment": field[7], "body_alignment": field[8],
         "heading_bold": bool(field[9]), "body_bold": bool(field[10]),
         "field_type": field[11], "image_size": field[12], "image_alignment": field[13]}
        for field in db.execute("""SELECT id, label, required, heading_font_size, body_font_size, hide_heading, max_length, heading_alignment, body_alignment, heading_bold, body_bold, field_type, image_size, image_alignment FROM study_template_field
            WHERE template_id = ? ORDER BY position, id""", (row[0],))
    ]} for row in db.execute("SELECT id, name, active FROM study_template WHERE active = 1 OR ? ORDER BY id", (include_inactive,))]


def study_pdf_path(uploads, filename):
    """Only a PDF directly inside uploads can belong to a research submission."""
    root = Path(uploads).resolve()
    target = (root / filename).resolve()
    if target.parent != root or target.suffix.lower() != ".pdf":
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
