"""School-scoped TEKNE chat, group files, and administrator tools."""

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import unicodedata
from io import BytesIO
from contextlib import closing
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse


MAX_MESSAGE_LENGTH = 5000
MAX_PAGE_SIZE = 100
FILE_CHUNK_SIZE = 1024 * 1024
IDENTITY_MIGRATION = "tekne_chat_identity_v1"
INTERNAL_KEY_PATTERN = re.compile(r"^[a-f0-9]{32}$")
CLIENT_EVENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
SESSION_TTL = dt.timedelta(days=1)


class TekneChatMigrationError(RuntimeError):
    """Raised before chat schema changes when legacy identities are ambiguous."""


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _connect(database_path):
    db = sqlite3.connect(database_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _create_identity_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS tekne_school_identity (
        id TEXT PRIMARY KEY NOT NULL,
        school_name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS tekne_student_identity (
        id TEXT PRIMARY KEY NOT NULL,
        login_id TEXT UNIQUE,
        school_id TEXT NOT NULL REFERENCES tekne_school_identity(id),
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        UNIQUE(id, school_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS tekne_student_session (
        token_hash TEXT PRIMARY KEY NOT NULL,
        student_id TEXT NOT NULL REFERENCES tekne_student_identity(id),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT
    )""")


def _school_key(db, school_name, *, create=False):
    row = db.execute("SELECT id FROM tekne_school_identity WHERE school_name = ?", (school_name,)).fetchone()
    if row:
        return row[0]
    if not create:
        return None
    school_id = uuid4().hex
    db.execute("INSERT INTO tekne_school_identity(id, school_name, created_at) VALUES(?,?,?)",
               (school_id, school_name, _now()))
    return school_id


def link_student_identity(db, login_id, school_name):
    """Assign an immutable chat identity to a newly registered student."""
    _create_identity_tables(db)
    school_id = _school_key(db, school_name, create=True)
    row = db.execute("SELECT id, school_id FROM tekne_student_identity WHERE login_id = ?", (login_id,)).fetchone()
    if row:
        if row[1] != school_id:
            raise TekneChatMigrationError("既存の生徒IDと学校IDの対応が一致しません。")
        return row[0]
    student_id = uuid4().hex
    db.execute("INSERT INTO tekne_student_identity(id, login_id, school_id, created_at) VALUES(?,?,?,?)",
               (student_id, login_id, school_id, _now()))
    return student_id


def initialize_tekne_chat(db):
    """Validate legacy mappings, then atomically add chat-only schema and identities."""
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("BEGIN IMMEDIATE")
    try:
        required = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('student','registered_school')"
        )}
        if required != {"student", "registered_school"}:
            raise TekneChatMigrationError("生徒または学校コード管理テーブルが見つかりません。")

        duplicate = db.execute(
            "SELECT id FROM student GROUP BY id HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate:
            raise TekneChatMigrationError("重複した生徒IDがあるため、内部ID移行を停止しました。")
        invalid_id = db.execute("SELECT 1 FROM student WHERE id IS NULL OR typeof(id)!='text' OR length(trim(id))=0 LIMIT 1").fetchone()
        if invalid_id:
            raise TekneChatMigrationError("空または不正な生徒IDがあるため、内部ID移行を停止しました。")

        student_columns = {row[1] for row in db.execute("PRAGMA table_info(student)")}
        if not {"id", "school"}.issubset(student_columns):
            raise TekneChatMigrationError("生徒テーブルの必須カラムが見つかりません。")
        school_names = [row[0] for row in db.execute("SELECT DISTINCT school FROM student")]
        if any(not isinstance(name, str) or not name.strip() for name in school_names):
            raise TekneChatMigrationError("学校名が空の生徒がいるため、内部ID移行を停止しました。")
        registered_names = {row[0] for row in db.execute("SELECT name FROM registered_school")}
        unknown_schools = [name for name in school_names if name not in registered_names]
        if unknown_schools:
            raise TekneChatMigrationError("学校コード管理表と一致しない学校名があるため、内部ID移行を停止しました。")

        _create_identity_tables(db)
        db.execute("""CREATE TABLE IF NOT EXISTS tekne_chat_group (
            id TEXT PRIMARY KEY NOT NULL,
            school_id TEXT NOT NULL REFERENCES tekne_school_identity(id),
            name TEXT NOT NULL,
            icon_key TEXT,
            created_by TEXT NOT NULL REFERENCES tekne_student_identity(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(id, school_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS tekne_chat_member (
            group_id TEXT NOT NULL,
            school_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            left_at TEXT,
            PRIMARY KEY(group_id, student_id),
            FOREIGN KEY(group_id, school_id) REFERENCES tekne_chat_group(id, school_id),
            FOREIGN KEY(student_id, school_id) REFERENCES tekne_student_identity(id, school_id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tekne_chat_member_student ON tekne_chat_member(student_id, left_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS tekne_chat_folder (
            id TEXT PRIMARY KEY NOT NULL,
            group_id TEXT NOT NULL,
            school_id TEXT NOT NULL,
            parent_id TEXT,
            name TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES tekne_student_identity(id),
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            UNIQUE(id, group_id),
            FOREIGN KEY(group_id, school_id) REFERENCES tekne_chat_group(id, school_id),
            FOREIGN KEY(parent_id, group_id) REFERENCES tekne_chat_folder(id, group_id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tekne_chat_folder_group_parent ON tekne_chat_folder(group_id, parent_id, deleted_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS tekne_chat_file (
            id TEXT PRIMARY KEY NOT NULL,
            group_id TEXT NOT NULL,
            school_id TEXT NOT NULL,
            folder_id TEXT,
            display_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            object_key TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            content_type TEXT NOT NULL,
            uploaded_by TEXT NOT NULL REFERENCES tekne_student_identity(id),
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            FOREIGN KEY(group_id, school_id) REFERENCES tekne_chat_group(id, school_id),
            FOREIGN KEY(folder_id, group_id) REFERENCES tekne_chat_folder(id, group_id)
        )""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_tekne_chat_file_active_name
            ON tekne_chat_file(group_id, COALESCE(folder_id, ''), normalized_name)
            WHERE deleted_at IS NULL""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tekne_chat_file_group_folder ON tekne_chat_file(group_id, folder_id, deleted_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS tekne_chat_message (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            school_id TEXT NOT NULL,
            sender_student_id TEXT REFERENCES tekne_student_identity(id),
            message_type TEXT NOT NULL CHECK(message_type IN ('text','file_upload')),
            body TEXT,
            file_id TEXT REFERENCES tekne_chat_file(id),
            client_event_id TEXT,
            file_name_snapshot TEXT,
            folder_path_snapshot TEXT,
            created_at TEXT NOT NULL,
            edited_at TEXT,
            deleted_at TEXT,
            deleted_by_type TEXT,
            deleted_by_id TEXT,
            FOREIGN KEY(group_id, school_id) REFERENCES tekne_chat_group(id, school_id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tekne_chat_message_group_id ON tekne_chat_message(group_id, id DESC)")
        message_columns = {row[1] for row in db.execute("PRAGMA table_info(tekne_chat_message)")}
        if "client_event_id" not in message_columns:
            db.execute("ALTER TABLE tekne_chat_message ADD COLUMN client_event_id TEXT")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_tekne_chat_message_client_event
            ON tekne_chat_message(group_id, sender_student_id, client_event_id)
            WHERE client_event_id IS NOT NULL""")
        db.execute("""CREATE TABLE IF NOT EXISTS tekne_chat_admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS tekne_chat_migration (
            name TEXT PRIMARY KEY NOT NULL,
            applied_at TEXT NOT NULL
        )""")

        now = _now()
        for school_name in sorted(registered_names):
            _school_key(db, school_name, create=True)
        for login_id, school_name in db.execute("SELECT id, school FROM student ORDER BY id"):
            school_id = _school_key(db, school_name)
            identity = db.execute("SELECT id, school_id FROM tekne_student_identity WHERE login_id = ?", (login_id,)).fetchone()
            if identity:
                if identity[1] != school_id:
                    raise TekneChatMigrationError("既存の内部IDと学校の対応が一致しないため、移行を停止しました。")
            else:
                db.execute("INSERT INTO tekne_student_identity(id, login_id, school_id, created_at) VALUES(?,?,?,?)",
                           (uuid4().hex, login_id, school_id, now))
        # A previously linked identity must still match exactly one current student.
        orphan = db.execute("""SELECT 1 FROM tekne_student_identity i
            WHERE i.login_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM student s WHERE s.id=i.login_id)
            LIMIT 1""").fetchone()
        if orphan:
            raise TekneChatMigrationError("内部IDに対応しない生徒レコードがあるため、移行を停止しました。")
        db.execute("INSERT OR IGNORE INTO tekne_chat_migration(name, applied_at) VALUES(?,?)",
                   (IDENTITY_MIGRATION, now))
        db.commit()
    except Exception:
        db.rollback()
        raise


def create_chat_session(database_path, login_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = dt.datetime.now(dt.timezone.utc)
    with closing(_connect(database_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        student = db.execute("""SELECT i.id FROM tekne_student_identity i
            JOIN student s ON s.id=i.login_id WHERE i.login_id=? AND i.deleted_at IS NULL""", (login_id,)).fetchone()
        if not student:
            raise TekneChatMigrationError("生徒のチャット内部IDがありません。")
        db.execute("INSERT INTO tekne_student_session(token_hash, student_id, created_at, expires_at) VALUES(?,?,?,?)",
                   (token_hash, student[0], now.isoformat(timespec="seconds"),
                    (now + SESSION_TTL).isoformat(timespec="seconds")))
    return token


def revoke_chat_session(database_path, token):
    if not isinstance(token, str) or not token:
        return
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with closing(_connect(database_path)) as db, db:
        db.execute("UPDATE tekne_student_session SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                   (_now(), digest))


def revoke_student_identity(db, login_id):
    identity = db.execute("SELECT id FROM tekne_student_identity WHERE login_id=? AND deleted_at IS NULL", (login_id,)).fetchone()
    if not identity:
        raise TekneChatMigrationError("削除対象の生徒のチャット内部IDがありません。")
    now = _now()
    db.execute("UPDATE tekne_student_identity SET login_id=NULL, deleted_at=? WHERE id=?", (now, identity[0]))
    db.execute("UPDATE tekne_chat_member SET left_at=? WHERE student_id=? AND left_at IS NULL", (now, identity[0]))
    db.execute("UPDATE tekne_student_session SET revoked_at=? WHERE student_id=? AND revoked_at IS NULL", (now, identity[0]))


def rename_student_identity(db, old_login_id, new_login_id):
    changed = db.execute("UPDATE tekne_student_identity SET login_id=? WHERE login_id=? AND deleted_at IS NULL",
                         (new_login_id, old_login_id)).rowcount
    if changed != 1:
        raise TekneChatMigrationError("生徒IDに対応するチャット内部IDがありません。")


def _clean_name(value, *, max_length=255):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFC", value.replace("\\", "/").rsplit("/", 1)[-1])
    value = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127).strip()
    if not value or value in {".", ".."} or len(value) > max_length:
        return None
    return value


def _normalized(value):
    return unicodedata.normalize("NFC", value).casefold()


def _valid_id(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _storage_file(storage_root, object_key):
    if not INTERNAL_KEY_PATTERN.fullmatch(object_key or ""):
        raise ValueError("Invalid chat storage key")
    root = (Path(storage_root) / "objects").resolve()
    target = (root / object_key).resolve()
    if target.parent != root:
        raise ValueError("Invalid chat storage path")
    return target


def _icon_file(storage_root, object_key):
    if not INTERNAL_KEY_PATTERN.fullmatch(object_key or ""):
        raise ValueError("Invalid chat icon key")
    root = (Path(storage_root) / "icons").resolve()
    target = (root / (object_key + ".webp")).resolve()
    if target.parent != root:
        raise ValueError("Invalid chat icon path")
    return target


def _write_upload(upload, storage_root):
    root = Path(storage_root).resolve()
    objects = root / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    object_key = uuid4().hex
    target = _storage_file(root, object_key)
    temporary = target.with_name("." + object_key + ".tmp")
    size = 0
    try:
        with temporary.open("xb") as output:
            while True:
                chunk = upload.file.read(FILE_CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        return object_key, size, target
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def _response_message(db, message):
    sender = None
    if message["sender_student_id"]:
        sender_row = db.execute("SELECT login_id, deleted_at FROM tekne_student_identity WHERE id=?",
                                (message["sender_student_id"],)).fetchone()
        sender = sender_row[0] if sender_row and not sender_row[1] else "削除済みユーザー"
    deleted = bool(message["deleted_at"])
    return {
        "id": message["id"], "group_id": message["group_id"], "type": message["message_type"],
        "sender": sender or ("TEKNE管理者" if message["message_type"] == "text" else "システム"),
        "body": "メッセージが削除されました" if deleted else (message["body"] or ""),
        "file_id": message["file_id"], "file_name": message["file_name_snapshot"],
        "folder_path": message["folder_path_snapshot"], "created_at": message["created_at"],
        "edited_at": message["edited_at"], "deleted": deleted,
        "file_url": f"/tekne-chat?group_id={quote(message['group_id'])}&file_id={quote(message['file_id'])}" if message["file_id"] else None,
    }


def _folder_path(db, group_id, folder_id):
    if folder_id is None:
        return "ルート"
    path = []
    current = folder_id
    seen = set()
    while current:
        if current in seen:
            raise HTTPException(409, "フォルダ階層が不正です。")
        seen.add(current)
        row = db.execute("SELECT parent_id, name FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL",
                         (current, group_id)).fetchone()
        if not row:
            raise HTTPException(404, "フォルダが見つかりません。")
        path.append(row[1])
        current = row[0]
    return "/".join(reversed(path))


def _unique_file_name(db, group_id, folder_id, requested, exclude_id=None):
    base = requested
    suffix = Path(requested).suffix
    stem = requested[:-len(suffix)] if suffix else requested
    candidate = base
    index = 1
    while True:
        query = "SELECT 1 FROM tekne_chat_file WHERE group_id=? AND normalized_name=? AND deleted_at IS NULL AND "
        params = [group_id, _normalized(candidate)]
        if folder_id is None:
            query += "folder_id IS NULL"
        else:
            query += "folder_id=?"
            params.append(folder_id)
        if exclude_id:
            query += " AND id<>?"
            params.append(exclude_id)
        if not db.execute(query + " LIMIT 1", params).fetchone():
            return candidate
        candidate = f"{stem} ({index}){suffix}"
        index += 1


class GroupConnectionManager:
    def __init__(self, service=None):
        self._connections = {}
        self._lock = asyncio.Lock()
        self._service = service

    async def add(self, group_id, websocket, session):
        async with self._lock:
            self._connections.setdefault(group_id, {})[websocket] = dict(session)

    async def remove(self, group_id, websocket):
        async with self._lock:
            connections = self._connections.get(group_id)
            if connections:
                connections.pop(websocket, None)
                if not connections:
                    self._connections.pop(group_id, None)

    async def broadcast(self, group_id, payload):
        async with self._lock:
            connections = tuple(self._connections.get(group_id, {}).items())
        for connection, session in connections:
            try:
                if self._service is not None:
                    await asyncio.to_thread(self._service.check_socket_membership, session, group_id)
                await connection.send_json(payload)
            except HTTPException:
                try:
                    await connection.close(code=4403)
                except Exception:
                    pass
                await self.remove(group_id, connection)
            except Exception:
                await self.remove(group_id, connection)

    async def close_session(self, token):
        if not isinstance(token, str) or not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with self._lock:
            targets = [(group_id, websocket) for group_id, items in self._connections.items()
                       for websocket, session in items.items()
                       if hashlib.sha256(str(session.get("tekne_chat_session", "")).encode("utf-8")).hexdigest() == token_hash]
        for group_id, websocket in targets:
            try:
                await websocket.close(code=4401)
            except Exception:
                pass
            await self.remove(group_id, websocket)

    async def close_login_id(self, login_id):
        async with self._lock:
            targets = [(group_id, websocket) for group_id, items in self._connections.items()
                       for websocket, session in items.items() if session.get("user_id") == login_id]
        for group_id, websocket in targets:
            try:
                await websocket.close(code=4403)
            except Exception:
                pass
            await self.remove(group_id, websocket)


class TekneChatService:
    def __init__(self, database_path, storage_root):
        self.database_path = Path(database_path)
        self.storage_root = Path(storage_root)
        self.connections = GroupConnectionManager(self)

    def identity_for_session(self, session):
        if session.get("user_login") is not True or not session.get("user_id"):
            raise HTTPException(401, "生徒としてログインしてください。")
        try:
            login_time = dt.datetime.strptime(session.get("user_time", ""), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            raise HTTPException(401, "ログインし直してください。")
        age = dt.datetime.now() - login_time
        if age < dt.timedelta(0) or age >= SESSION_TTL:
            raise HTTPException(401, "ログインの有効期限が切れました。ログインし直してください。")
        token = session.get("tekne_chat_session")
        if not isinstance(token, str) or not token:
            raise HTTPException(401, "チャットを利用するにはログインし直してください。")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with closing(_connect(self.database_path)) as db:
            row = db.execute("""SELECT i.id, i.school_id, si.school_name
                FROM tekne_student_identity i
                JOIN student s ON s.id=i.login_id
                JOIN tekne_school_identity si ON si.id=i.school_id
                JOIN tekne_student_session cs ON cs.student_id=i.id
                WHERE i.login_id=? AND i.deleted_at IS NULL AND s.school=si.school_name
              AND cs.token_hash=? AND cs.revoked_at IS NULL AND cs.expires_at>?""",
                (session["user_id"], token_hash, _now())).fetchone()
        if not row:
            raise HTTPException(401, "生徒アカウントまたはセッションが無効です。")
        return dict(row)

    def _identity(self, db, session):
        login_id = session.get("user_id")
        token = session.get("tekne_chat_session")
        try:
            login_time = dt.datetime.strptime(session.get("user_time", ""), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            raise HTTPException(401, "ログインし直してください。")
        age = dt.datetime.now() - login_time
        if session.get("user_login") is not True or not login_id or age < dt.timedelta(0) or age >= SESSION_TTL:
            raise HTTPException(401, "ログインの有効期限が切れました。")
        if not isinstance(token, str) or not token:
            raise HTTPException(401, "チャットを利用するにはログインし直してください。")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        identity = db.execute("""SELECT i.id, i.school_id, si.school_name
            FROM tekne_student_identity i JOIN student s ON s.id=i.login_id
            JOIN tekne_school_identity si ON si.id=i.school_id
            JOIN tekne_student_session cs ON cs.student_id=i.id
            WHERE i.login_id=? AND i.deleted_at IS NULL AND s.school=si.school_name
              AND cs.token_hash=? AND cs.revoked_at IS NULL AND cs.expires_at>?""",
            (login_id, digest, _now())).fetchone()
        if not identity:
            raise HTTPException(401, "生徒アカウントまたはセッションが無効です。")
        return identity

    def _member(self, db, session, group_id):
        identity = self._identity(db, session)
        row = db.execute("""SELECT g.id, g.school_id, g.name, g.icon_key, m.student_id
            FROM tekne_chat_group g JOIN tekne_chat_member m ON m.group_id=g.id AND m.school_id=g.school_id
            WHERE g.id=? AND g.school_id=? AND m.student_id=? AND m.left_at IS NULL""",
            (group_id, identity[1], identity[0])).fetchone()
        if not row:
            raise HTTPException(403, "このグループを利用する権限がありません。")
        return identity, row

    def check_socket_membership(self, session, group_id):
        with closing(_connect(self.database_path)) as db:
            identity, _ = self._member(db, session, group_id)
            return identity[0]

    def file_group(self, file_id):
        with closing(_connect(self.database_path)) as db:
            row = db.execute("SELECT group_id FROM tekne_chat_file WHERE id=?", (file_id,)).fetchone()
            if not row:
                raise HTTPException(404, "ファイルが見つかりません。")
            return row[0]

    def groups(self, session):
        with closing(_connect(self.database_path)) as db:
            identity = self._identity(db, session)
            rows = db.execute("""SELECT g.id,g.name,g.icon_key,g.updated_at,
                (SELECT COUNT(*) FROM tekne_chat_member m WHERE m.group_id=g.id AND m.left_at IS NULL) member_count,
                (SELECT body FROM tekne_chat_message x WHERE x.group_id=g.id ORDER BY x.id DESC LIMIT 1) last_message
                FROM tekne_chat_group g JOIN tekne_chat_member m ON m.group_id=g.id
                WHERE g.school_id=? AND m.student_id=? AND m.left_at IS NULL ORDER BY g.updated_at DESC,g.id""",
                (identity["school_id"], identity["id"])).fetchall()
            return [dict(row) for row in rows]

    def submission_group_snapshot(self, db, session, group_id):
        """Validate current membership and return submitter identities using the caller's DB transaction."""
        identity, group = self._member(db, session, group_id)
        members = db.execute("""SELECT i.id, i.login_id
            FROM tekne_chat_member m
            JOIN tekne_student_identity i ON i.id=m.student_id AND i.school_id=m.school_id
            JOIN student s ON s.id=i.login_id AND s.school=(
                SELECT school_name FROM tekne_school_identity WHERE id=i.school_id)
            WHERE m.group_id=? AND m.school_id=? AND m.left_at IS NULL
              AND i.deleted_at IS NULL AND i.login_id IS NOT NULL
            ORDER BY m.joined_at, i.login_id""", (group_id, identity[1])).fetchall()
        if not members or identity[0] not in {row[0] for row in members}:
            raise HTTPException(422, "選択したグループの在籍状況を確認できません。画面を再読み込みしてください。")
        return {"id": group[0], "name": group[2], "members": members}

    def create_group(self, session, name):
        clean = _clean_name(name, max_length=100)
        if not clean:
            raise HTTPException(422, "グループ名を1〜100文字で入力してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity = self._identity(db, session)
            group_id, now = uuid4().hex, _now()
            db.execute("INSERT INTO tekne_chat_group(id,school_id,name,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (group_id, identity[1], clean, identity[0], now, now))
            db.execute("INSERT INTO tekne_chat_member(group_id,school_id,student_id,joined_at) VALUES(?,?,?,?)",
                       (group_id, identity[1], identity[0], now))
            return {"id": group_id, "name": clean, "icon_key": None, "member_count": 1}

    def rename_group(self, session, group_id, name):
        clean = _clean_name(name, max_length=100)
        if not clean:
            raise HTTPException(422, "グループ名を1〜100文字で入力してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity, _ = self._member(db, session, group_id)
            db.execute("UPDATE tekne_chat_group SET name=?,updated_at=? WHERE id=? AND school_id=?",
                       (clean, _now(), group_id, identity[1]))
            return {"id": group_id, "name": clean}

    def invite(self, session, group_id, target_login_id):
        if not _valid_id(target_login_id):
            raise HTTPException(422, "招待する生徒IDを確認してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity, group = self._member(db, session, group_id)
            target = db.execute("""SELECT i.id FROM tekne_student_identity i JOIN student s ON s.id=i.login_id
                WHERE i.login_id=? AND i.school_id=? AND i.deleted_at IS NULL AND s.school=?""",
                (target_login_id, identity[1], identity[2])).fetchone()
            if not target:
                raise HTTPException(404, "同じ学校の生徒が見つかりません。")
            now = _now()
            db.execute("""INSERT INTO tekne_chat_member(group_id,school_id,student_id,joined_at,left_at)
                VALUES(?,?,?,?,NULL) ON CONFLICT(group_id,student_id) DO UPDATE SET joined_at=excluded.joined_at,left_at=NULL""",
                (group_id, identity[1], target[0], now))
            return {"group_id": group_id, "student_id": target[0], "login_id": target_login_id, "joined_at": now}

    def search_students(self, session, query, limit=20):
        term = (query or "").strip()
        if len(term) < 2:
            return []
        with closing(_connect(self.database_path)) as db:
            identity = self._identity(db, session)
            rows = db.execute("""SELECT i.id,i.login_id FROM tekne_student_identity i JOIN student s ON s.id=i.login_id
                WHERE i.school_id=? AND i.deleted_at IS NULL AND s.school=? AND i.login_id LIKE ? ESCAPE '\\'
                AND i.id<>? ORDER BY i.login_id LIMIT ?""",
                (identity["school_id"], identity["school_name"], _like(term)+"%", identity["id"], min(max(limit,1),50))).fetchall()
            return [{"student_id": row[0], "login_id": row[1]} for row in rows]

    def members(self, session, group_id):
        with closing(_connect(self.database_path)) as db:
            self._member(db, session, group_id)
            rows = db.execute("""SELECT m.student_id,i.login_id,i.deleted_at,m.joined_at
                FROM tekne_chat_member m JOIN tekne_student_identity i ON i.id=m.student_id
                WHERE m.group_id=? AND m.left_at IS NULL ORDER BY i.login_id,m.joined_at""", (group_id,)).fetchall()
            return [{"student_id": row[0], "login_id": row[1] if not row[2] else "削除済みユーザー", "joined_at": row[3]}
                    for row in rows]

    def leave(self, session, group_id):
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity, _ = self._member(db, session, group_id)
            db.execute("UPDATE tekne_chat_member SET left_at=? WHERE group_id=? AND student_id=? AND left_at IS NULL",
                       (_now(), group_id, identity[0]))
            return {"left": True}

    def message_history(self, session, group_id, before_id=None, limit=50, after_id=None):
        limit = min(max(int(limit), 1), MAX_PAGE_SIZE)
        with closing(_connect(self.database_path)) as db:
            self._member(db, session, group_id)
            if after_id is not None:
                rows = db.execute("SELECT * FROM tekne_chat_message WHERE group_id=? AND id>? ORDER BY id ASC LIMIT ?",
                                  (group_id, after_id, limit + 1)).fetchall()
                has_more = len(rows) > limit
                selected = rows[:limit]
                return {"messages": [_response_message(db, row) for row in selected],
                        "next_after_id": selected[-1]["id"] if has_more and selected else None}
            if before_id is None:
                rows = db.execute("SELECT * FROM tekne_chat_message WHERE group_id=? ORDER BY id DESC LIMIT ?",
                                  (group_id, limit + 1)).fetchall()
            else:
                rows = db.execute("SELECT * FROM tekne_chat_message WHERE group_id=? AND id<? ORDER BY id DESC LIMIT ?",
                                  (group_id, before_id, limit + 1)).fetchall()
            has_more = len(rows) > limit
            selected = list(reversed(rows[:limit]))
            return {"messages": [_response_message(db, row) for row in selected],
                    "next_before_id": selected[0]["id"] if has_more and selected else None}

    def get_file(self, session, file_id):
        with closing(_connect(self.database_path)) as db:
            record = self.file_record(db, file_id)
            self._member(db, session, record["group_id"])
            return {"id": record["id"], "group_id": record["group_id"], "folder_id": record["folder_id"],
                    "display_name": record["display_name"], "size_bytes": record["size_bytes"],
                    "content_type": record["content_type"], "folder_path": _folder_path(db, record["group_id"], record["folder_id"])}

    def group_icon(self, session, group_id):
        with closing(_connect(self.database_path)) as db:
            self._member(db, session, group_id)
            row = db.execute("SELECT icon_key FROM tekne_chat_group WHERE id=?", (group_id,)).fetchone()
            return row[0] if row else None

    def set_group_icon(self, session, group_id, icon_key):
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._member(db, session, group_id)
            row = db.execute("SELECT icon_key FROM tekne_chat_group WHERE id=?", (group_id,)).fetchone()
            if not row:
                raise HTTPException(404, "グループが見つかりません。")
            db.execute("UPDATE tekne_chat_group SET icon_key=?,updated_at=? WHERE id=?", (icon_key, _now(), group_id))
            return row[0]

    def rename_folder(self, session, folder_id, name):
        clean = _clean_name(name)
        if not clean:
            raise HTTPException(422, "フォルダ名を確認してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tekne_chat_folder WHERE id=? AND deleted_at IS NULL", (folder_id,)).fetchone()
            if not row:
                raise HTTPException(404, "フォルダが見つかりません。")
            self._member(db, session, row["group_id"])
            db.execute("UPDATE tekne_chat_folder SET name=? WHERE id=?", (clean, folder_id))
            return {"id": folder_id, "name": clean, "group_id": row["group_id"]}

    def create_message(self, session, group_id, body, client_event_id=None):
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_MESSAGE_LENGTH:
            raise HTTPException(422, "メッセージは1〜5,000文字で入力してください。")
        if client_event_id is not None and (not isinstance(client_event_id, str) or not CLIENT_EVENT_PATTERN.fullmatch(client_event_id)):
            raise HTTPException(422, "送信IDが正しくありません。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity, _ = self._member(db, session, group_id)
            if client_event_id:
                prior = db.execute("""SELECT * FROM tekne_chat_message WHERE group_id=? AND sender_student_id=? AND client_event_id=?""",
                                   (group_id, identity[0], client_event_id)).fetchone()
                if prior:
                    return _response_message(db, prior)
            message_id = db.execute("""INSERT INTO tekne_chat_message
                (group_id,school_id,sender_student_id,message_type,body,client_event_id,created_at)
                VALUES(?,?,?,'text',?,?,?)""", (group_id, identity[1], identity[0], body, client_event_id, _now())).lastrowid
            db.execute("UPDATE tekne_chat_group SET updated_at=? WHERE id=?", (_now(), group_id))
            row = db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message_id,)).fetchone()
            return _response_message(db, row)

    def edit_message(self, session, message_id, body):
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_MESSAGE_LENGTH:
            raise HTTPException(422, "メッセージは1〜5,000文字で入力してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity = self._identity(db, session)
            row = db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message_id,)).fetchone()
            if not row or row["sender_student_id"] != identity[0] or row["deleted_at"] or row["message_type"] != "text":
                raise HTTPException(403, "編集できるメッセージがありません。")
            self._member(db, session, row["group_id"])
            db.execute("UPDATE tekne_chat_message SET body=?,edited_at=? WHERE id=?",
                       (body, _now(), message_id))
            updated = db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message_id,)).fetchone()
            return _response_message(db, updated)

    def delete_message(self, session, message_id, *, admin_id=None):
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if admin_id is None:
                identity = self._identity(db, session)
                row = db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message_id,)).fetchone()
                if not row or row["sender_student_id"] != identity[0] or row["deleted_at"]:
                    raise HTTPException(403, "削除できるメッセージがありません。")
                self._member(db, session, row["group_id"])
                deleted_by_type, deleted_by_id = "student", identity[0]
            else:
                row = db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message_id,)).fetchone()
                if not row:
                    raise HTTPException(404, "メッセージが見つかりません。")
                deleted_by_type, deleted_by_id = "admin", admin_id
                self._audit(db, admin_id, "message.delete", "message", str(message_id), {})
            db.execute("UPDATE tekne_chat_message SET deleted_at=?,deleted_by_type=?,deleted_by_id=? WHERE id=?",
                       (_now(), deleted_by_type, deleted_by_id, message_id))
            updated = db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message_id,)).fetchone()
            return row["group_id"], _response_message(db, updated)

    def create_folder(self, session, group_id, name, parent_id=None):
        clean = _clean_name(name)
        if not clean:
            raise HTTPException(422, "フォルダ名を確認してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            identity, _ = self._member(db, session, group_id)
            if parent_id and not db.execute("SELECT 1 FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL",
                                            (parent_id, group_id)).fetchone():
                raise HTTPException(404, "親フォルダが見つかりません。")
            folder_id = uuid4().hex
            db.execute("""INSERT INTO tekne_chat_folder(id,group_id,school_id,parent_id,name,created_by,created_at)
                VALUES(?,?,?,?,?,?,?)""", (folder_id, group_id, identity[1], parent_id, clean, identity[0], _now()))
            return {"id": folder_id, "group_id": group_id, "parent_id": parent_id, "name": clean}

    def list_files(self, session, group_id, folder_id=None, query=""):
        with closing(_connect(self.database_path)) as db:
            self._member(db, session, group_id)
            current_parent = None
            if folder_id:
                current = db.execute("SELECT parent_id FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL",
                                     (folder_id, group_id)).fetchone()
                if not current:
                    raise HTTPException(404, "フォルダが見つかりません。")
                current_parent = current[0]
            if query:
                pattern = "%" + _like(query.strip()) + "%"
                files = db.execute("""SELECT f.id,f.folder_id,f.display_name,f.size_bytes,f.content_type,f.uploaded_by,
                    i.login_id AS uploaded_by_login_id,f.created_at
                    FROM tekne_chat_file f LEFT JOIN tekne_student_identity i ON i.id=f.uploaded_by
                    WHERE f.group_id=? AND f.deleted_at IS NULL AND f.display_name LIKE ? ESCAPE '\\'
                    ORDER BY f.display_name,f.id""", (group_id, pattern)).fetchall()
                folders = db.execute("""SELECT id,parent_id,name,created_at FROM tekne_chat_folder
                    WHERE group_id=? AND deleted_at IS NULL AND name LIKE ? ESCAPE '\\' ORDER BY name,id""",
                    (group_id, pattern)).fetchall()
            else:
                if folder_id and not db.execute("SELECT 1 FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL",
                                                (folder_id, group_id)).fetchone():
                    raise HTTPException(404, "フォルダが見つかりません。")
                files = db.execute("""SELECT f.id,f.folder_id,f.display_name,f.size_bytes,f.content_type,f.uploaded_by,
                    i.login_id AS uploaded_by_login_id,f.created_at
                    FROM tekne_chat_file f LEFT JOIN tekne_student_identity i ON i.id=f.uploaded_by
                    WHERE f.group_id=? AND f.folder_id IS ? AND f.deleted_at IS NULL ORDER BY f.display_name,f.id""",
                    (group_id, folder_id)).fetchall()
                folders = db.execute("""SELECT id,parent_id,name,created_at FROM tekne_chat_folder
                    WHERE group_id=? AND parent_id IS ? AND deleted_at IS NULL ORDER BY name,id""",
                    (group_id, folder_id)).fetchall()
            return {"files": [dict(row) for row in files], "folders": [dict(row) for row in folders],
                    "current_parent_id": current_parent,
                    "folder_path": _folder_path(db, group_id, folder_id) if folder_id else "ルート"}

    def all_folders(self, session, group_id):
        with closing(_connect(self.database_path)) as db:
            self._member(db, session, group_id)
            rows = db.execute("SELECT id,parent_id,name FROM tekne_chat_folder WHERE group_id=? AND deleted_at IS NULL ORDER BY name,id",
                              (group_id,)).fetchall()
            return [{"id": row[0], "parent_id": row[1], "name": row[2], "path": _folder_path(db, group_id, row[0])} for row in rows]

    def upload_file(self, session, group_id, folder_id, upload):
        display_name = _clean_name(upload.filename)
        if not display_name:
            raise HTTPException(422, "ファイル名を確認してください。")
        object_key, size, path = _write_upload(upload, self.storage_root)
        try:
            with closing(_connect(self.database_path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                identity, _ = self._member(db, session, group_id)
                if folder_id and not db.execute("SELECT 1 FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL",
                                                (folder_id, group_id)).fetchone():
                    raise HTTPException(404, "保存先フォルダが見つかりません。")
                display_name = _unique_file_name(db, group_id, folder_id, display_name)
                file_id = uuid4().hex
                content_type = (upload.content_type or "application/octet-stream")[:255]
                db.execute("""INSERT INTO tekne_chat_file
                    (id,group_id,school_id,folder_id,display_name,normalized_name,object_key,size_bytes,content_type,uploaded_by,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (file_id, group_id, identity[1], folder_id, display_name, _normalized(display_name), object_key,
                     size, content_type, identity[0], _now()))
                folder_path = _folder_path(db, group_id, folder_id)
                message_id = db.execute("""INSERT INTO tekne_chat_message
                    (group_id,school_id,sender_student_id,message_type,file_id,file_name_snapshot,folder_path_snapshot,created_at)
                    VALUES(?,?,?,'file_upload',?,?,?,?)""",
                    (group_id, identity[1], identity[0], file_id, display_name, folder_path, _now())).lastrowid
                db.execute("UPDATE tekne_chat_group SET updated_at=? WHERE id=?", (_now(), group_id))
                message = _response_message(db, db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message_id,)).fetchone())
                message["download_url"] = f"/tekne-chat?group_id={quote(group_id)}&file_id={quote(file_id)}"
                message["folder_url"] = f"/tekne-chat?group_id={quote(group_id)}&folder_id={quote(folder_id or '')}"
                return message
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def file_record(self, db, file_id):
        row = db.execute("SELECT * FROM tekne_chat_file WHERE id=? AND deleted_at IS NULL", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "ファイルが見つからないか、削除されています。")
        return row

    def delete_file(self, session, file_id, *, admin_id=None):
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self.file_record(db, file_id)
            if admin_id is None:
                identity = self._identity(db, session)
                self._member(db, session, row["group_id"])
            else:
                self._audit(db, admin_id, "file.delete", "file", file_id, {})
            db.execute("UPDATE tekne_chat_file SET deleted_at=? WHERE id=?", (_now(), file_id))
            return row["group_id"], row["object_key"]

    def rename_file(self, session, file_id, name):
        clean = _clean_name(name)
        if not clean:
            raise HTTPException(422, "ファイル名を確認してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self.file_record(db, file_id)
            self._member(db, session, row["group_id"])
            clean = _unique_file_name(db, row["group_id"], row["folder_id"], clean, exclude_id=file_id)
            db.execute("UPDATE tekne_chat_file SET display_name=?,normalized_name=? WHERE id=?",
                       (clean, _normalized(clean), file_id))
            return {"id": file_id, "display_name": clean}

    def move_file(self, session, file_id, folder_id):
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self.file_record(db, file_id)
            self._member(db, session, row["group_id"])
            if folder_id and not db.execute("SELECT 1 FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL",
                                            (folder_id, row["group_id"])).fetchone():
                raise HTTPException(404, "移動先フォルダが見つかりません。")
            name = _unique_file_name(db, row["group_id"], folder_id, row["display_name"], exclude_id=file_id)
            db.execute("UPDATE tekne_chat_file SET folder_id=?,display_name=?,normalized_name=? WHERE id=?",
                       (folder_id, name, _normalized(name), file_id))
            return {"id": file_id, "folder_id": folder_id, "display_name": name}

    def delete_folder(self, session, folder_id, *, admin_id=None):
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tekne_chat_folder WHERE id=? AND deleted_at IS NULL", (folder_id,)).fetchone()
            if not row:
                raise HTTPException(404, "フォルダが見つかりません。")
            if admin_id is None:
                self._member(db, session, row["group_id"])
            else:
                self._audit(db, admin_id, "folder.delete", "folder", folder_id, {})
            file_rows = db.execute("""WITH RECURSIVE tree(id) AS (
                SELECT id FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL
                UNION SELECT f.id FROM tekne_chat_folder f JOIN tree t ON f.parent_id=t.id
                WHERE f.group_id=? AND f.deleted_at IS NULL)
                SELECT f.id,f.object_key FROM tekne_chat_file f
                WHERE f.folder_id IN (SELECT id FROM tree) AND f.deleted_at IS NULL""",
                (folder_id, row["group_id"], row["group_id"])).fetchall()
            now = _now()
            db.execute("""WITH RECURSIVE tree(id) AS (
                SELECT id FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL
                UNION SELECT f.id FROM tekne_chat_folder f JOIN tree t ON f.parent_id=t.id
                WHERE f.group_id=? AND f.deleted_at IS NULL)
                UPDATE tekne_chat_file SET deleted_at=? WHERE folder_id IN (SELECT id FROM tree)""",
                (folder_id, row["group_id"], row["group_id"], now))
            db.execute("""WITH RECURSIVE tree(id) AS (
                SELECT id FROM tekne_chat_folder WHERE id=? AND group_id=? AND deleted_at IS NULL
                UNION SELECT f.id FROM tekne_chat_folder f JOIN tree t ON f.parent_id=t.id
                WHERE f.group_id=? AND f.deleted_at IS NULL)
                UPDATE tekne_chat_folder SET deleted_at=? WHERE id IN (SELECT id FROM tree)""",
                (folder_id, row["group_id"], row["group_id"], now))
            return row["group_id"], [dict(item) for item in file_rows]

    def _audit(self, db, admin_id, action, entity_type, entity_id, details):
        db.execute("INSERT INTO tekne_chat_admin_audit(admin_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                   (admin_id, action, entity_type, entity_id, json.dumps(details, ensure_ascii=False), _now()))

    def admin_groups(self, query="", offset=0, limit=100):
        with closing(_connect(self.database_path)) as db:
            params = []
            sql = "SELECT g.id,g.name,s.school_name,g.created_at,g.updated_at FROM tekne_chat_group g JOIN tekne_school_identity s ON s.id=g.school_id"
            count_sql = "SELECT COUNT(*) FROM tekne_chat_group g JOIN tekne_school_identity s ON s.id=g.school_id"
            if query:
                sql += " WHERE g.name LIKE ? ESCAPE '\\' OR s.school_name LIKE ? ESCAPE '\\'"
                count_sql += " WHERE g.name LIKE ? ESCAPE '\\' OR s.school_name LIKE ? ESCAPE '\\'"
                term = "%" + _like(query.strip()) + "%"
                params.extend((term, term))
            total = db.execute(count_sql, params).fetchone()[0]
            sql += " ORDER BY g.updated_at DESC,g.id LIMIT ? OFFSET ?"
            groups = db.execute(sql, [*params, min(max(int(limit), 1), 100), max(int(offset), 0)]).fetchall()
            items = [{**dict(row), "member_count": db.execute("SELECT COUNT(*) FROM tekne_chat_member WHERE group_id=? AND left_at IS NULL", (row[0],)).fetchone()[0],
                     "message_count": db.execute("SELECT COUNT(*) FROM tekne_chat_message WHERE group_id=?", (row[0],)).fetchone()[0],
                     "file_count": db.execute("SELECT COUNT(*) FROM tekne_chat_file WHERE group_id=? AND deleted_at IS NULL", (row[0],)).fetchone()[0]}
                    for row in groups]
            return {"groups": items, "total": total}

    def admin_group_detail(self, group_id, message_before_id=None, file_before_id=None, limit=50):
        with closing(_connect(self.database_path)) as db:
            group = db.execute("SELECT g.*,s.school_name FROM tekne_chat_group g JOIN tekne_school_identity s ON s.id=g.school_id WHERE g.id=?",
                                (group_id,)).fetchone()
            if not group:
                raise HTTPException(404, "グループが見つかりません。")
            members = db.execute("""SELECT m.student_id,i.login_id,i.deleted_at,m.joined_at,m.left_at
                FROM tekne_chat_member m JOIN tekne_student_identity i ON i.id=m.student_id
                WHERE m.group_id=? ORDER BY m.joined_at""", (group_id,)).fetchall()
            page_size = min(max(int(limit), 1), 100)
            message_sql = "SELECT * FROM tekne_chat_message WHERE group_id=?"
            message_args = [group_id]
            if message_before_id is not None:
                message_sql += " AND id<?"; message_args.append(message_before_id)
            message_rows = db.execute(message_sql + " ORDER BY id DESC LIMIT ?", [*message_args, page_size + 1]).fetchall()
            next_message_before = message_rows[page_size - 1]["id"] if len(message_rows) > page_size else None
            messages = message_rows[:page_size]
            file_sql = "SELECT rowid AS _rowid_, * FROM tekne_chat_file WHERE group_id=? AND deleted_at IS NULL"
            file_args = [group_id]
            if file_before_id is not None:
                file_sql += " AND rowid<?"; file_args.append(file_before_id)
            file_rows = db.execute(file_sql + " ORDER BY rowid DESC LIMIT ?", [*file_args, page_size + 1]).fetchall()
            next_file_before = file_rows[page_size - 1]["_rowid_"] if len(file_rows) > page_size else None
            files = file_rows[:page_size]
            folders = db.execute("SELECT * FROM tekne_chat_folder WHERE group_id=? AND deleted_at IS NULL ORDER BY created_at DESC", (group_id,)).fetchall()
            return {"group": dict(group),
                    "members": [{"student_id": r[0], "login_id": r[1] if not r[2] else "削除済みユーザー", "joined_at": r[3], "left_at": r[4]} for r in members],
                    "messages": [_response_message(db, row) for row in reversed(messages)],
                    "files": [dict(row) for row in files], "folders": [dict(row) for row in folders],
                    "next_message_before_id": next_message_before, "next_file_before_id": next_file_before}

    def admin_rename_group(self, admin_id, group_id, name):
        clean = _clean_name(name, max_length=100)
        if not clean:
            raise HTTPException(422, "グループ名を確認してください。")
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM tekne_chat_group WHERE id=?", (group_id,)).fetchone():
                raise HTTPException(404, "グループが見つかりません。")
            db.execute("UPDATE tekne_chat_group SET name=?,updated_at=? WHERE id=?", (clean, _now(), group_id))
            self._audit(db, admin_id, "group.rename", "group", group_id, {"name": clean})

    def admin_remove_member(self, admin_id, group_id, student_id):
        with closing(_connect(self.database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE tekne_chat_member SET left_at=? WHERE group_id=? AND student_id=? AND left_at IS NULL",
                                 (_now(), group_id, student_id)).rowcount
            if not changed:
                raise HTTPException(404, "参加中のメンバーが見つかりません。")
            self._audit(db, admin_id, "member.remove", "member", student_id, {"group_id": group_id})


def _like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _admin_account(db, request):
    from admin_management import active_admin
    account = active_admin(db, request)
    if account is None:
        raise HTTPException(403, "管理者としてログインしてください。")
    return account


def _same_origin(websocket):
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not origin or not host:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    scheme = websocket.url.scheme
    expected_scheme = "https" if scheme == "wss" else "http"
    return parsed.scheme == expected_scheme and parsed.netloc.lower() == host.lower()


def build_tekne_chat_router(database_path, templates, storage_root):
    router = APIRouter()
    service = TekneChatService(database_path, storage_root)

    def csrf_token(request):
        token = request.session.get("tekne_chat_csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            request.session["tekne_chat_csrf"] = token
        return token

    def require_csrf(request, token):
        expected = request.session.get("tekne_chat_csrf", "")
        if not expected or not token or not secrets.compare_digest(expected, token):
            raise HTTPException(403, "操作を確認できませんでした。ページを再読み込みしてください。")

    async def require_admin(request):
        with closing(_connect(database_path)) as db:
            return _admin_account(db, request)

    @router.get("/tekne-chat")
    async def chat_page(request: Request, group_id: str = "", file_id: str = "", folder_id: str = ""):
        service.identity_for_session(request.session)
        return templates.TemplateResponse(request=request, name="tekne_chat/index.html",
            context={"request": request, "csrf_token": csrf_token(request), "initial_group_id": group_id,
                     "initial_file_id": file_id, "initial_folder_id": folder_id,
                     "user_id": request.session.get("user_id")})

    @router.get("/tekne-chat/api/groups")
    async def group_list(request: Request):
        return {"groups": await asyncio.to_thread(service.groups, request.session)}

    @router.post("/tekne-chat/api/groups")
    async def group_create(request: Request, name: str = Form(...), csrf: str = Form(...)):
        require_csrf(request, csrf)
        return await asyncio.to_thread(service.create_group, request.session, name)

    @router.patch("/tekne-chat/api/groups/{group_id}")
    async def group_rename(request: Request, group_id: str, name: str = Form(...), csrf: str = Form(...)):
        require_csrf(request, csrf)
        result = await asyncio.to_thread(service.rename_group, request.session, group_id, name)
        await service.connections.broadcast(group_id, {"type": "group.updated", **result})
        return result

    @router.get("/tekne-chat/api/students")
    async def student_search(request: Request, q: str = "", limit: int = 20):
        return {"students": await asyncio.to_thread(service.search_students, request.session, q, limit)}

    @router.get("/tekne-chat/api/groups/{group_id}/members")
    async def member_list(request: Request, group_id: str):
        return {"members": await asyncio.to_thread(service.members, request.session, group_id)}

    @router.post("/tekne-chat/api/groups/{group_id}/members")
    async def member_invite(request: Request, group_id: str, student_id: str = Form(...), csrf: str = Form(...)):
        require_csrf(request, csrf)
        result = await asyncio.to_thread(service.invite, request.session, group_id, student_id)
        await service.connections.broadcast(group_id, {"type": "members.updated"})
        return result

    @router.post("/tekne-chat/api/groups/{group_id}/messages")
    async def message_create(request: Request, group_id: str, body: str = Form(...), csrf: str = Form(...), client_event_id: str = Form("")):
        require_csrf(request, csrf)
        message = await asyncio.to_thread(service.create_message, request.session, group_id, body, client_event_id or None)
        await service.connections.broadcast(group_id, {"type": "message.created", "message": message})
        return {"message": message}

    @router.delete("/tekne-chat/api/groups/{group_id}/members/me")
    async def member_leave(request: Request, group_id: str, csrf: str = Form(...)):
        require_csrf(request, csrf)
        result = await asyncio.to_thread(service.leave, request.session, group_id)
        await service.connections.broadcast(group_id, {"type": "member.left", "student_id": request.session.get("user_id")})
        return result

    @router.get("/tekne-chat/api/groups/{group_id}/messages")
    async def message_list(request: Request, group_id: str, before_id: int | None = None, after_id: int | None = None, limit: int = 50):
        return await asyncio.to_thread(service.message_history, request.session, group_id, before_id, limit, after_id)

    @router.get("/tekne-chat/api/files/{file_id}")
    async def file_detail(request: Request, file_id: str):
        return await asyncio.to_thread(service.get_file, request.session, file_id)

    @router.get("/tekne-chat/api/groups/{group_id}/icon")
    async def group_icon(request: Request, group_id: str):
        key = await asyncio.to_thread(service.group_icon, request.session, group_id)
        if not key:
            raise HTTPException(404, "グループアイコンがありません。")
        target = _icon_file(storage_root, key)
        if not target.is_file():
            raise HTTPException(404, "グループアイコンがありません。")
        return FileResponse(target, media_type="image/webp", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/tekne-chat/api/groups/{group_id}/icon")
    async def group_icon_update(request: Request, group_id: str, csrf: str = Form(...), file: UploadFile | None = File(None), remove_icon: bool = Form(False)):
        require_csrf(request, csrf)
        await asyncio.to_thread(service.check_socket_membership, request.session, group_id)
        if file is None and not remove_icon:
            return {"updated": True}
        old_key = None
        new_key = None
        if file is not None:
            raw = await file.read(2 * 1024 * 1024 + 1)
            await file.close()
            if len(raw) > 2 * 1024 * 1024:
                raise HTTPException(413, "アイコンは2MB以下にしてください。")
            try:
                from PIL import Image, UnidentifiedImageError
                with Image.open(BytesIO(raw)) as source:
                    if source.width > 4096 or source.height > 4096:
                        raise HTTPException(422, "画像サイズが大きすぎます。")
                    source.load()
                    image = source.convert("RGB")
                    icons = Path(storage_root).resolve() / "icons"
                    icons.mkdir(parents=True, exist_ok=True)
                    new_key = uuid4().hex
                    image.save(_icon_file(storage_root, new_key), format="WEBP", quality=85)
            except HTTPException:
                if new_key:
                    _icon_file(storage_root, new_key).unlink(missing_ok=True)
                raise
            except Exception:
                if new_key:
                    _icon_file(storage_root, new_key).unlink(missing_ok=True)
                raise HTTPException(422, "画像ファイルを読み取れませんでした。")
        try:
            old_key = await asyncio.to_thread(service.set_group_icon, request.session, group_id, new_key)
        except Exception:
            if new_key:
                _icon_file(storage_root, new_key).unlink(missing_ok=True)
            raise
        if old_key:
            try:
                _icon_file(storage_root, old_key).unlink(missing_ok=True)
            except OSError:
                logging.exception("Failed to remove replaced chat icon")
        await service.connections.broadcast(group_id, {"type": "group.updated", "id": group_id})
        return {"updated": True}

    @router.patch("/tekne-chat/api/messages/{message_id}")
    async def message_edit(request: Request, message_id: int, body: str = Form(...), csrf: str = Form(...)):
        require_csrf(request, csrf)
        message = await asyncio.to_thread(service.edit_message, request.session, message_id, body)
        await service.connections.broadcast(message["group_id"], {"type": "message.updated", "message": message})
        return {"message": message}

    @router.delete("/tekne-chat/api/messages/{message_id}")
    async def message_delete(request: Request, message_id: int, csrf: str = Form(...)):
        require_csrf(request, csrf)
        group_id, message = await asyncio.to_thread(service.delete_message, request.session, message_id)
        await service.connections.broadcast(group_id, {"type": "message.updated", "message": message})
        return {"message": message}

    @router.get("/tekne-chat/api/groups/{group_id}/folders")
    async def folder_list(request: Request, group_id: str, folder_id: str | None = None, q: str = ""):
        return await asyncio.to_thread(service.list_files, request.session, group_id, folder_id, q)

    @router.get("/tekne-chat/api/groups/{group_id}/folder-tree")
    async def folder_tree(request: Request, group_id: str):
        return {"folders": await asyncio.to_thread(service.all_folders, request.session, group_id)}

    @router.post("/tekne-chat/api/groups/{group_id}/folders")
    async def folder_create(request: Request, group_id: str, name: str = Form(...), parent_id: str = Form(""), csrf: str = Form(...)):
        require_csrf(request, csrf)
        result = await asyncio.to_thread(service.create_folder, request.session, group_id, name, parent_id or None)
        await service.connections.broadcast(group_id, {"type": "files.updated"})
        return result

    @router.patch("/tekne-chat/api/folders/{folder_id}")
    async def folder_rename(request: Request, folder_id: str, name: str = Form(...), csrf: str = Form(...)):
        require_csrf(request, csrf)
        result = await asyncio.to_thread(service.rename_folder, request.session, folder_id, name)
        await service.connections.broadcast(result["group_id"], {"type": "files.updated"})
        return result

    @router.delete("/tekne-chat/api/folders/{folder_id}")
    async def folder_delete(request: Request, folder_id: str, csrf: str = Form(...)):
        require_csrf(request, csrf)
        group_id, deleted_files = await asyncio.to_thread(service.delete_folder, request.session, folder_id)
        for file_record in deleted_files:
            try:
                _storage_file(storage_root, file_record["object_key"]).unlink(missing_ok=True)
            except OSError:
                logging.exception("Failed to remove deleted chat file %s", file_record["id"])
        await service.connections.broadcast(group_id, {"type": "files.updated"})
        return {"deleted": True}

    @router.post("/tekne-chat/api/groups/{group_id}/files")
    async def file_upload(request: Request, group_id: str, file: UploadFile = File(...), folder_id: str = Form(""), csrf: str = Form(...)):
        require_csrf(request, csrf)
        try:
            message = await asyncio.to_thread(service.upload_file, request.session, group_id, folder_id or None, file)
            await service.connections.broadcast(group_id, {"type": "message.created", "message": message})
            return {"message": message}
        finally:
            await file.close()

    async def file_download_response(file_id, record, download_name=None):
        try:
            target = _storage_file(storage_root, record["object_key"])
        except ValueError:
            raise HTTPException(404, "ファイルが見つかりません。")
        if not target.is_file():
            raise HTTPException(404, "ファイルが見つかりません。")
        name = _clean_name(download_name or record["display_name"]) or "download"
        return FileResponse(target, media_type="application/octet-stream", filename=name,
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

    @router.get("/tekne-chat/api/files/{file_id}/download")
    async def file_download(request: Request, file_id: str):
        with closing(_connect(database_path)) as db:
            identity = service._identity(db, request.session)
            record = service.file_record(db, file_id)
            service._member(db, request.session, record["group_id"])
            if record["school_id"] != identity["school_id"]:
                raise HTTPException(404, "ファイルが見つかりません。")
            return await file_download_response(file_id, record)

    @router.delete("/tekne-chat/api/files/{file_id}")
    async def file_delete(request: Request, file_id: str, csrf: str = Form(...)):
        require_csrf(request, csrf)
        group_id, object_key = await asyncio.to_thread(service.delete_file, request.session, file_id)
        try:
            _storage_file(storage_root, object_key).unlink(missing_ok=True)
        except OSError:
            logging.exception("Failed to remove deleted chat file %s", file_id)
        await service.connections.broadcast(group_id, {"type": "files.updated"})
        return {"deleted": True}

    @router.patch("/tekne-chat/api/files/{file_id}")
    async def file_rename(request: Request, file_id: str, name: str = Form(...), csrf: str = Form(...)):
        require_csrf(request, csrf)
        result = await asyncio.to_thread(service.rename_file, request.session, file_id, name)
        await service.connections.broadcast((await asyncio.to_thread(service.file_group, file_id)), {"type": "files.updated"})
        return result

    @router.post("/tekne-chat/api/files/{file_id}/move")
    async def file_move(request: Request, file_id: str, folder_id: str = Form(""), csrf: str = Form(...)):
        require_csrf(request, csrf)
        result = await asyncio.to_thread(service.move_file, request.session, file_id, folder_id or None)
        await service.connections.broadcast((await asyncio.to_thread(service.file_group, file_id)), {"type": "files.updated"})
        return result

    @router.websocket("/ws/tekne-chat/{group_id}")
    async def chat_socket(websocket: WebSocket, group_id: str):
        if not _same_origin(websocket):
            await websocket.close(code=4403)
            return
        session = websocket.scope.get("session", {})
        try:
            await asyncio.to_thread(service.check_socket_membership, session, group_id)
        except HTTPException:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        await service.connections.add(group_id, websocket, session)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(websocket.receive_json(), timeout=30)
                except asyncio.TimeoutError:
                    try:
                        await asyncio.to_thread(service.check_socket_membership, session, group_id)
                    except HTTPException:
                        await websocket.close(code=4403)
                        return
                    await websocket.send_json({"type": "ping"})
                    continue
                if not isinstance(event, dict) or event.get("type") != "message.create":
                    await websocket.send_json({"type": "error", "message": "未対応の操作です。"})
                    continue
                try:
                    message = await asyncio.to_thread(service.create_message, session, group_id, event.get("body"), event.get("client_event_id"))
                except HTTPException as exc:
                    if exc.status_code == 401 or exc.status_code == 403:
                        await websocket.close(code=4403)
                        return
                    await websocket.send_json({"type": "error", "message": exc.detail})
                    continue
                await service.connections.broadcast(group_id, {"type": "message.created", "message": message})
        except WebSocketDisconnect:
            pass
        except Exception:
            logging.exception("TEKNE chat WebSocket failed")
            try:
                await websocket.close(code=1011)
            except Exception:
                pass
        finally:
            await service.connections.remove(group_id, websocket)

    async def admin_page(request):
        admin = await require_admin(request)
        return admin

    @router.get("/admin/tekne-chat")
    async def admin_chat_page(request: Request, q: str = "", offset: int = 0):
        admin = await admin_page(request)
        result = await asyncio.to_thread(service.admin_groups, q, offset, 100)
        return templates.TemplateResponse(request=request, name="admin/tekne_chat.html",
            context={"request": request, "admin_id": admin[0], "groups": result["groups"], "total_groups": result["total"], "offset": max(offset, 0), "query": q,
                     "csrf_token": csrf_token(request)})

    @router.get("/admin/tekne-chat/api/groups/{group_id}")
    async def admin_group_detail(request: Request, group_id: str, message_before_id: int | None = None,
                                 file_before_id: int | None = None, limit: int = 50):
        await admin_page(request)
        return await asyncio.to_thread(service.admin_group_detail, group_id, message_before_id, file_before_id, limit)

    @router.patch("/admin/tekne-chat/api/groups/{group_id}")
    async def admin_group_rename(request: Request, group_id: str, name: str = Form(...), csrf: str = Form(...)):
        require_csrf(request, csrf)
        admin = await admin_page(request)
        await asyncio.to_thread(service.admin_rename_group, admin[0], group_id, name)
        await service.connections.broadcast(group_id, {"type": "group.updated", "id": group_id})
        return {"updated": True}

    @router.post("/admin/tekne-chat/api/groups/{group_id}/members/{student_id}/remove")
    async def admin_member_remove(request: Request, group_id: str, student_id: str, csrf: str = Form(...)):
        require_csrf(request, csrf)
        admin = await admin_page(request)
        await asyncio.to_thread(service.admin_remove_member, admin[0], group_id, student_id)
        await service.connections.broadcast(group_id, {"type": "members.updated"})
        return {"removed": True}

    @router.delete("/admin/tekne-chat/api/messages/{message_id}")
    async def admin_message_delete(request: Request, message_id: int, csrf: str = Form(...)):
        require_csrf(request, csrf)
        admin = await admin_page(request)
        group_id, message = await asyncio.to_thread(service.delete_message, {}, message_id, admin_id=admin[0])
        await service.connections.broadcast(group_id, {"type": "message.updated", "message": message})
        return {"message": message}

    @router.delete("/admin/tekne-chat/api/files/{file_id}")
    async def admin_file_delete(request: Request, file_id: str, csrf: str = Form(...)):
        require_csrf(request, csrf)
        admin = await admin_page(request)
        group_id, object_key = await asyncio.to_thread(service.delete_file, {}, file_id, admin_id=admin[0])
        try:
            _storage_file(storage_root, object_key).unlink(missing_ok=True)
        except OSError:
            logging.exception("Failed to remove deleted chat file %s", file_id)
        await service.connections.broadcast(group_id, {"type": "files.updated"})
        return {"deleted": True}

    @router.delete("/admin/tekne-chat/api/folders/{folder_id}")
    async def admin_folder_delete(request: Request, folder_id: str, csrf: str = Form(...)):
        require_csrf(request, csrf)
        admin = await admin_page(request)
        group_id, deleted_files = await asyncio.to_thread(service.delete_folder, {}, folder_id, admin_id=admin[0])
        for record in deleted_files:
            try:
                _storage_file(storage_root, record["object_key"]).unlink(missing_ok=True)
            except OSError:
                logging.exception("Failed to remove deleted chat file %s", record["id"])
        await service.connections.broadcast(group_id, {"type": "files.updated"})
        return {"deleted": True}

    @router.get("/admin/tekne-chat/api/files/{file_id}/download")
    async def admin_file_download(request: Request, file_id: str):
        await admin_page(request)
        with closing(_connect(database_path)) as db:
            record = service.file_record(db, file_id)
            return await file_download_response(file_id, record)

    return router, service


