"""School-scoped custom student fields and validated values."""
import datetime
import json
import re
import sqlite3


FIELD_TYPES = {
    "text": "1行テキスト", "textarea": "複数行テキスト", "number": "数値",
    "email": "メールアドレス", "date": "日付", "select": "単一選択",
    "multiselect": "複数選択", "checkbox": "チェックボックス",
}
MAX_LABEL_LENGTH = 80
MAX_OPTION_LENGTH = 100
MAX_OPTIONS = 100
MAX_VALUE_LENGTH = 4000


def initialize_custom_fields(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS student_custom_field (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school TEXT NOT NULL,
            label TEXT NOT NULL,
            field_type TEXT NOT NULL,
            required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
            options_json TEXT NOT NULL DEFAULT '[]',
            deleted_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_student_custom_field_school
            ON student_custom_field(school, deleted_at, id);
        CREATE TABLE IF NOT EXISTS student_custom_field_value (
            student_id TEXT NOT NULL,
            field_id INTEGER NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(student_id, field_id),
            FOREIGN KEY(field_id) REFERENCES student_custom_field(id)
        );
        CREATE INDEX IF NOT EXISTS idx_student_custom_field_value_field
            ON student_custom_field_value(field_id);
    """)
    # Discard responses for removed fields and any values whose field definition
    # no longer exists. This also cleans up data left by older soft deletes.
    db.execute("""DELETE FROM student_custom_field_value
        WHERE field_id NOT IN (
            SELECT id FROM student_custom_field WHERE deleted_at IS NULL
        )""")
    db.commit()


def validate_definition(label, field_type, required, options_text):
    label = (label or "").strip()
    if not label or len(label) > MAX_LABEL_LENGTH:
        raise ValueError("項目名は1〜80文字で入力してください。")
    if field_type not in FIELD_TYPES:
        raise ValueError("項目の形式が不正です。")
    if required not in ("0", "1", 0, 1, False, True):
        raise ValueError("必須設定が不正です。")
    options = []
    if field_type in ("select", "multiselect"):
        options = [line.strip() for line in (options_text or "").splitlines() if line.strip()]
        if not options or len(options) > MAX_OPTIONS or any(len(x) > MAX_OPTION_LENGTH for x in options) or len(set(options)) != len(options):
            raise ValueError("選択肢は重複のない1〜100個（各100文字以内）で入力してください。")
    elif (options_text or "").strip():
        raise ValueError("この形式では選択肢を設定できません。")
    return label, field_type, int(bool(int(required))), options


def validate_value(field, raw):
    value = raw
    if field["field_type"] == "checkbox":
        value = raw in ("1", "on", "true", True)
    elif field["field_type"] == "multiselect":
        value = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        if any(item not in field["options"] for item in value) or len(set(value)) != len(value):
            raise ValueError("選択肢にない値が含まれています。")
    elif raw is None:
        value = ""
    elif not isinstance(raw, str):
        raise ValueError("入力値が不正です。")
    if isinstance(value, str):
        value = value.strip()
        if len(value) > MAX_VALUE_LENGTH:
            raise ValueError("入力は4000文字以内にしてください。")
    if value in ("", []) or value is False:
        if field["required"]:
            raise ValueError("必須項目を入力してください。")
        return None
    kind = field["field_type"]
    if kind == "number":
        try:
            number = float(value)
            if not __import__("math").isfinite(number): raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("数値を入力してください。")
        return value
    if kind == "email" and (len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)):
        raise ValueError("有効なメールアドレスを入力してください。")
    if kind == "date":
        try: datetime.date.fromisoformat(value)
        except ValueError: raise ValueError("日付を正しく入力してください。")
    if kind == "select" and value not in field["options"]:
        raise ValueError("選択肢にない値です。")
    return value


def decode_value(value_json):
    try: return json.loads(value_json)
    except (TypeError, json.JSONDecodeError): return None
