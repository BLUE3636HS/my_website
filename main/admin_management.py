"""SQLite administrator roles, additive migration, and super-admin routes."""

import datetime
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

import bcrypt
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

JST = datetime.timezone(datetime.timedelta(hours=9))


def migrate_admin_database(database_path):
    """Keep an SQLite snapshot before the first additive migration."""
    with closing(sqlite3.connect(database_path, timeout=10)) as db:
        has_marker_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration'").fetchone()
        has_profiles = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mentor_profile'").fetchone()
        marker = 'admin_profile_v1' if has_profiles else 'admin_roles_v1'
        applied = has_marker_table and db.execute("SELECT 1 FROM schema_migration WHERE name=?", (marker,)).fetchone()
        if not applied:
            backup = Path(str(database_path) + f'.{marker}.' + datetime.datetime.now(JST).strftime('%Y%m%d%H%M%S%f') + '.bak')
            with closing(sqlite3.connect(backup)) as snapshot:
                db.backup(snapshot)
        with db:
            initialize_admin_roles(db)


def initialize_admin_roles(db):
    # Caller owns the transaction. The marker prevents future startup promotions.
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    db.execute("CREATE TABLE IF NOT EXISTS admin (id TEXT PRIMARY KEY NOT NULL, pwd TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS schema_migration (name TEXT PRIMARY KEY NOT NULL, applied_at TEXT NOT NULL)")
    columns = {row[1] for row in db.execute("PRAGMA table_info(admin)")}
    for name, definition in {
        "name": "TEXT NOT NULL DEFAULT ''",
        "role": "TEXT NOT NULL DEFAULT 'admin' CHECK(role IN ('admin','super_admin'))",
        "deleted_at": "TEXT",
        "session_version": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in columns:
            db.execute(f"ALTER TABLE admin ADD COLUMN {name} {definition}")
    if not db.execute("SELECT 1 FROM schema_migration WHERE name='admin_roles_v1'").fetchone():
        db.execute("UPDATE admin SET role='super_admin' WHERE id='26A001'")
        db.execute("INSERT INTO schema_migration VALUES ('admin_roles_v1', ?)",
                   (datetime.datetime.now(JST).isoformat(),))
    has_profiles = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mentor_profile'").fetchone()
    if has_profiles and not db.execute("SELECT 1 FROM schema_migration WHERE name='admin_profile_v1'").fetchone():
        now = datetime.datetime.now(JST).isoformat()
        db.execute("""INSERT INTO mentor_profile(admin_id,display_name,created_at,updated_at)
            SELECT id,name,?,? FROM admin WHERE name<>'' AND NOT EXISTS
            (SELECT 1 FROM mentor_profile WHERE admin_id=admin.id)""", (now, now))
        # Existing public profile names take precedence; photos and biography stay intact.
        db.execute("""UPDATE admin SET name=(SELECT display_name FROM mentor_profile WHERE admin_id=admin.id)
            WHERE EXISTS(SELECT 1 FROM mentor_profile WHERE admin_id=admin.id AND display_name<>'')""")
        db.execute("""UPDATE mentor_profile SET display_name=(SELECT name FROM admin WHERE id=mentor_profile.admin_id)
            WHERE display_name='' AND EXISTS(SELECT 1 FROM admin WHERE id=mentor_profile.admin_id)""")
        db.execute("INSERT INTO schema_migration VALUES ('admin_profile_v1', ?)", (datetime.datetime.now(JST).isoformat(),))


def session_secret(database_path):
    configured = os.environ.get("SESSION_SECRET")
    if configured:
        if len(configured) < 32:
            raise RuntimeError("SESSION_SECRET は32文字以上にしてください。")
        return configured
    path = Path(database_path).parent / '.session_secret'
    try:
        with path.open('xb') as output:
            output.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("セッション署名キーが不正です。")
    return key.hex()


def active_admin(db, request):
    if request.session.get('admin_login') is not True:
        return None
    row = db.execute("SELECT id, name, role, session_version FROM admin WHERE id=? AND deleted_at IS NULL",
                     (request.session.get('admin_id'),)).fetchone()
    return row if row and row[3] == request.session.get('admin_session_version', 0) else None


def require_super_admin(db, request):
    admin = active_admin(db, request)
    if admin is None or admin[2] != 'super_admin':
        raise HTTPException(403, "最上位管理者の権限が必要です。")
    return admin


def validate_admin(admin_id, name, password):
    if not admin_id or len(admin_id) > 128 or any(ord(c) < 32 for c in admin_id):
        raise ValueError("管理者IDは制御文字を含まない1〜128文字で入力してください。")
    if len(name) > 128 or any(ord(c) < 32 for c in name):
        raise ValueError("管理者名は制御文字を含まない1〜128文字で入力してください。")
    if len(password) < 12 or len(password.encode('utf-8')) > 72:
        raise ValueError("パスワードは12文字以上、UTF-8で72バイト以内で入力してください。")


def build_admin_router(database_path, templates):
    router = APIRouter(prefix='/admin/administrators')

    def render(request, page, error=None, status_code=200, values=None):
        with closing(sqlite3.connect(database_path)) as db:
            require_super_admin(db, request)
            rows = db.execute("SELECT id,name,role,deleted_at FROM admin ORDER BY id").fetchall() if page == 'list' else []
        token = request.session.setdefault('admin_management_csrf', secrets.token_urlsafe(32))
        return templates.TemplateResponse(request=request, name='admin/administrators.html',
            context={'request': request, 'admin_id': request.session.get('admin_id'),
                     'page': page, 'administrators': rows, 'csrf_token': token,
                     'error': error, 'values': values or {},
                     'notice': request.session.pop('admin_management_notice', None)}, status_code=status_code)

    def csrf(request, token):
        expected = request.session.get('admin_management_csrf')
        if not expected or not secrets.compare_digest(expected.encode(), token.encode()):
            raise HTTPException(403, 'CSRFトークンが不正です。')

    @router.get('')
    async def administrator_list(request: Request):
        return render(request, 'list')

    @router.get('/new')
    async def administrator_new(request: Request):
        return render(request, 'new')

    @router.post('/new')
    async def administrator_create(request: Request, admin_id: str = Form(...),
                                   password: str = Form(...), csrf_token: str = Form(...)):
        admin_id, name = admin_id.strip(), ''
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            require_super_admin(db, request)
            csrf(request, csrf_token)
            try:
                validate_admin(admin_id, name, password)
                password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                db.execute("INSERT INTO admin(id,name,pwd,role) VALUES (?,?,?,'admin')",
                           (admin_id, name, password_hash))
                db.commit()
            except (ValueError, sqlite3.IntegrityError) as exc:
                db.rollback()
                message = 'その管理者IDはすでに使用されています（削除済みも含む）。' if isinstance(exc, sqlite3.IntegrityError) else str(exc)
                return render(request, 'new', message, 400, {'admin_id': admin_id, 'name': name})
        request.session['admin_management_notice'] = '一般管理者を追加しました。'
        request.session['admin_management_csrf'] = secrets.token_urlsafe(32)
        return RedirectResponse('/admin/administrators', status_code=303)

    @router.post('/delete')
    async def administrator_delete(request: Request, admin_id: str = Form(...), csrf_token: str = Form(...)):
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            require_super_admin(db, request)
            csrf(request, csrf_token)
            target = db.execute('SELECT role,deleted_at FROM admin WHERE id=?', (admin_id,)).fetchone()
            if target is None:
                raise HTTPException(404, '管理者が見つかりません。')
            if target[0] == 'super_admin':
                raise HTTPException(403, '最上位管理者は削除できません。')
            if target[1] is not None:
                raise HTTPException(409, 'すでに削除された管理者です。')
            now = datetime.datetime.now(JST)
            pending = db.execute("""SELECT 1 FROM mentor_reservation WHERE mentor_admin_id=? AND status='active'
                AND (day>? OR (day=? AND end_time>?)) LIMIT 1""",
                (admin_id, now.date().isoformat(), now.date().isoformat(), now.strftime('%H:%M'))).fetchone()
            if pending:
                db.rollback()
                return render(request, 'list', '今後の有効なメンター予約があります。予約に対応してから削除してください。', 409)
            db.execute('UPDATE admin SET deleted_at=? WHERE id=? AND role=\'admin\'', (now.isoformat(), admin_id))
            db.execute('UPDATE mentor_profile SET is_published=0, updated_at=? WHERE admin_id=?', (now.isoformat(), admin_id))
            db.commit()
        request.session['admin_management_notice'] = '管理者を削除しました。履歴とIDは保持されています。'
        request.session['admin_management_csrf'] = secrets.token_urlsafe(32)
        return RedirectResponse('/admin/administrators', status_code=303)

    return router


def build_password_router(database_path):
    router = APIRouter()

    @router.post('/admin/profile/password')
    async def change_password(request: Request, current_password: str = Form(...),
                              new_password: str = Form(...), confirm_password: str = Form(...),
                              csrf_token: str = Form(...)):
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            account = active_admin(db, request)
            if account is None:
                raise HTTPException(403, '管理者としてログインしてください。')
            token = request.session.get('admin_password_csrf', '')
            if not token or not secrets.compare_digest(token.encode(), csrf_token.encode()):
                raise HTTPException(403, 'CSRFトークンが不正です。')
            try:
                validate_admin(account[0], '', new_password)
                if new_password != confirm_password:
                    raise ValueError('新しいパスワードと確認用パスワードが一致しません。')
                old_hash = db.execute('SELECT pwd FROM admin WHERE id=?', (account[0],)).fetchone()[0]
                try:
                    correct = bcrypt.checkpw(current_password.encode(), old_hash.encode())
                except ValueError:
                    correct = False
                if not correct:
                    raise ValueError('現在のパスワードが正しくありません。')
                hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                db.execute('UPDATE admin SET pwd=?,session_version=session_version+1 WHERE id=?', (hashed, account[0]))
                db.commit()
            except ValueError as exc:
                db.rollback()
                request.session['mentor_profile_notice'] = {'type': 'error', 'message': str(exc)}
                return RedirectResponse('/admin/profile', 303)
        request.session.clear()
        request.session['admin_password_notice'] = 'パスワードを変更しました。新しいパスワードでログインしてください。'
        return RedirectResponse('/admin/login', 303)

    return router
