"""Local SQLite helpers for main's school-issued registration credentials."""

import datetime
import hmac
import re
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

import bcrypt


SCHOOL_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SCHOOL_ID_LENGTH = 6
SCHOOL_ID_PATTERN = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
SCHOOL_ID_PEPPER_PATH = Path(__file__).resolve().parent / "database" / ".school_id_pepper"
RATE_LIMIT_WINDOW_SECONDS = 60 * 60
RATE_LIMIT_MAX_FAILURES = 10
RATE_LIMIT_TOTAL_WINDOW_SECONDS = 15 * 60
RATE_LIMIT_MAX_REQUESTS = 60
ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def initialize_school_registration(db):
    """Create only additive tables; never alter or clear existing account data."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS registered_school (
            school_id_lookup_hash TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL UNIQUE,
            school_id_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS school_registration_attempt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_ip TEXT NOT NULL,
            attempted_at INTEGER NOT NULL,
            credential_valid INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_school_registration_attempt_ip_time
        ON school_registration_attempt(source_ip, attempted_at);
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(registered_school)")}
    if "school_id_lookup_hash" not in columns:
        # Upgrade the earlier selector-based table without dropping existing schools.
        db.execute("ALTER TABLE registered_school ADD COLUMN school_id_lookup_hash TEXT")
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_registered_school_lookup_hash
        ON registered_school(school_id_lookup_hash)
    """)
    attempt_columns = {
        row[1] for row in db.execute("PRAGMA table_info(school_registration_attempt)")
    }
    if "credential_valid" not in attempt_columns:
        db.execute(
            "ALTER TABLE school_registration_attempt "
            "ADD COLUMN credential_valid INTEGER NOT NULL DEFAULT 0"
        )


def _load_lookup_pepper():
    path = SCHOOL_ID_PEPPER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as key_file:
            key_file.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    pepper = path.read_bytes()
    if len(pepper) != 32:
        raise RuntimeError("学校ID照合キーが不正です。バックアップから復元してください。")
    return pepper


def _school_id_lookup_hash(school_id_bytes, pepper):
    return hmac.new(pepper, school_id_bytes, "sha256").hexdigest()


def _school_id_bytes(value):
    if not isinstance(value, str) or not SCHOOL_ID_PATTERN.fullmatch(value):
        return None
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        return None


def issue_school_id(db, name, *, reissue=False, now=None):
    """Issue a one-time credential; persist a bcrypt verifier and lookup digest."""
    school_name = name.strip() if isinstance(name, str) else ""
    if not school_name or len(school_name) > 200:
        raise ValueError("学校名は1〜200文字で入力してください。")
    legacy_selector_column = "selector" in {
        column[1] for column in db.execute("PRAGMA table_info(registered_school)")
    }
    if legacy_selector_column:
        row = db.execute(
            "SELECT school_id_lookup_hash, selector FROM registered_school WHERE name = ?",
            (school_name,),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT school_id_lookup_hash FROM registered_school WHERE name = ?",
            (school_name,),
        ).fetchone()
    if row and not reissue:
        raise ValueError("同じ学校名が登録済みです。再発行を選択してください。")
    if not row and reissue:
        raise ValueError("再発行する学校が見つかりません。")

    pepper = _load_lookup_pepper()
    for _ in range(100):
        issued = "".join(secrets.choice(SCHOOL_ID_ALPHABET) for _ in range(SCHOOL_ID_LENGTH))
        issued_bytes = issued.encode("ascii")
        lookup_hash = _school_id_lookup_hash(issued_bytes, pepper)
        if db.execute(
            "SELECT 1 FROM registered_school WHERE school_id_lookup_hash = ?",
            (lookup_hash,),
        ).fetchone() is None:
            break
    else:
        raise RuntimeError("学校IDを一意に生成できませんでした。")
    secret_hash = bcrypt.hashpw(issued_bytes + pepper, bcrypt.gensalt()).decode("ascii")
    timestamp = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    if row:
        if legacy_selector_column:
            # Keep the obsolete NOT NULL column satisfied while revoking its old token.
            db.execute(
                "UPDATE registered_school SET selector = ?, school_id_lookup_hash = ?, school_id_hash = ?, updated_at = ? WHERE name = ?",
                (secrets.token_urlsafe(9), lookup_hash, secret_hash, timestamp, school_name),
            )
        else:
            db.execute(
                "UPDATE registered_school SET school_id_lookup_hash = ?, school_id_hash = ?, updated_at = ? WHERE name = ?",
                (lookup_hash, secret_hash, timestamp, school_name),
            )
    else:
        if legacy_selector_column:
            db.execute(
                "INSERT INTO registered_school (selector, name, school_id_hash, created_at, updated_at, school_id_lookup_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (secrets.token_urlsafe(9), school_name, secret_hash, timestamp, timestamp, lookup_hash),
            )
        else:
            db.execute(
                "INSERT INTO registered_school (school_id_lookup_hash, name, school_id_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (lookup_hash, school_name, secret_hash, timestamp, timestamp),
            )
    return issued


def verify_school_id(db, value):
    """Use a digest only as an index, then bcrypt-check the full credential."""
    school_id_bytes = _school_id_bytes(value)
    if school_id_bytes is None:
        return None
    pepper = _load_lookup_pepper()
    lookup_hash = _school_id_lookup_hash(school_id_bytes, pepper)
    row = db.execute(
        "SELECT name, school_id_hash FROM registered_school WHERE school_id_lookup_hash = ?",
        (lookup_hash,),
    ).fetchone()
    if row is None:
        return None
    try:
        valid = bcrypt.checkpw(school_id_bytes + pepper, row[1].encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return None
    return row[0] if valid else None


def validate_student_credentials(user_id, password):
    return bool(
        isinstance(user_id, str)
        and ID_PATTERN.fullmatch(user_id)
        and isinstance(password, str)
        and password.isascii()
        and 8 <= len(password) <= 72
        and any(char.isalpha() for char in password)
        and any(char.isdigit() for char in password)
    )


def register_school_student(database_path, user_id, password, school_id, source_ip, *, now=None):
    """Rate-limit and create one student in a serialized local SQLite transaction."""
    if not validate_student_credentials(user_id, password):
        return "invalid"
    attempt_time = int((now or datetime.datetime.now(datetime.timezone.utc)).timestamp())
    try:
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            with db:
                initialize_school_registration(db)
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "DELETE FROM school_registration_attempt WHERE attempted_at < ?",
                    (attempt_time - RATE_LIMIT_WINDOW_SECONDS,),
                )
                failures = db.execute(
                    "SELECT COUNT(*) FROM school_registration_attempt "
                    "WHERE source_ip = ? AND credential_valid = 0 AND attempted_at >= ?",
                    (source_ip, attempt_time - RATE_LIMIT_WINDOW_SECONDS),
                ).fetchone()[0]
                requests = db.execute(
                    "SELECT COUNT(*) FROM school_registration_attempt "
                    "WHERE source_ip = ? AND attempted_at >= ?",
                    (source_ip, attempt_time - RATE_LIMIT_TOTAL_WINDOW_SECONDS),
                ).fetchone()[0]
                if failures >= RATE_LIMIT_MAX_FAILURES or requests >= RATE_LIMIT_MAX_REQUESTS:
                    return "rate_limited"
                school_name = verify_school_id(db, school_id)
                db.execute(
                    "INSERT INTO school_registration_attempt "
                    "(source_ip, attempted_at, credential_valid) VALUES (?, ?, ?)",
                    (source_ip, attempt_time, int(school_name is not None)),
                )
                if school_name is None:
                    return "invalid"
                if db.execute("SELECT 1 FROM student WHERE id = ? LIMIT 1", (user_id,)).fetchone():
                    return "duplicate"
                password_hash = bcrypt.hashpw(password.encode("ascii"), bcrypt.gensalt()).decode("ascii")
                db.execute(
                    "INSERT INTO student (id, pwd, school) VALUES (?, ?, ?)",
                    (user_id, password_hash, school_name),
                )
                return "created"
    except sqlite3.IntegrityError:
        return "duplicate"
    except sqlite3.Error:
        return "unavailable"
