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
        "must_change_password": "INTEGER NOT NULL DEFAULT 0",
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


def validate_admin_id(admin_id):
    if not admin_id or len(admin_id) > 128 or any(ord(c) < 32 for c in admin_id):
        raise ValueError("管理者IDは制御文字を含まない1〜128文字で入力してください。")


def validate_admin(admin_id, name, password):
    validate_admin_id(admin_id)
    if len(name) > 128 or any(ord(c) < 32 for c in name):
        raise ValueError("管理者名は制御文字を含まない1〜128文字で入力してください。")
    if len(password) < 12 or len(password.encode('utf-8')) > 72:
        raise ValueError("パスワードは12文字以上、UTF-8で72バイト以内で入力してください。")


def build_admin_router(database_path, templates, mentor_profile_uploads_dir=None):
    router = APIRouter(prefix='/admin/administrators')
    image_dir = Path(mentor_profile_uploads_dir).resolve() if mentor_profile_uploads_dir else None

    def render(request, page, error=None, status_code=200, values=None):
        with closing(sqlite3.connect(database_path)) as db:
            require_super_admin(db, request)
            rows = db.execute("SELECT id,name,role,deleted_at FROM admin ORDER BY id").fetchall() if page == 'list' else []
        token = request.session.setdefault('admin_management_csrf', secrets.token_urlsafe(32))
        return templates.TemplateResponse(request=request, name='admin/administrators.html',
            context={'request': request, 'admin_id': request.session.get('admin_id'),
                     'page': page, 'administrators': rows, 'csrf_token': token,
                     'error': error, 'values': values or {},
                     'notice': request.session.pop('admin_management_notice', None)}, status_code=status_code,
            headers={'Cache-Control': 'no-store'} if page == 'created' else None)

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
                                   csrf_token: str = Form(...)):
        admin_id, name = admin_id.strip(), ''
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            require_super_admin(db, request)
            csrf(request, csrf_token)
            try:
                validate_admin_id(admin_id)
                password = secrets.token_urlsafe(18)
                password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                db.execute("INSERT INTO admin(id,name,pwd,role,must_change_password) VALUES (?,?,?,'admin',1)",
                           (admin_id, name, password_hash))
                db.commit()
            except (ValueError, sqlite3.IntegrityError) as exc:
                db.rollback()
                message = 'その管理者IDはすでに使用されています。' if isinstance(exc, sqlite3.IntegrityError) else str(exc)
                return render(request, 'new', message, 400, {'admin_id': admin_id})
        request.session['admin_management_csrf'] = secrets.token_urlsafe(32)
        return render(request, 'created', values={'admin_id': admin_id, 'password': password})

    @router.post('/suspend')
    async def administrator_suspend(request: Request, admin_id: str = Form(...), csrf_token: str = Form(...)):
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            require_super_admin(db, request)
            csrf(request, csrf_token)
            target = db.execute('SELECT role,deleted_at FROM admin WHERE id=?', (admin_id,)).fetchone()
            if target is None:
                raise HTTPException(404, '管理者が見つかりません。')
            if target[0] == 'super_admin':
                raise HTTPException(403, '最上位管理者は停止できません。')
            if target[1] is not None:
                raise HTTPException(409, 'すでに停止中の管理者です。')
            now = datetime.datetime.now(JST)
            db.execute('UPDATE admin SET deleted_at=?,session_version=session_version+1 WHERE id=? AND role=\'admin\'', (now.isoformat(), admin_id))
            db.commit()
        request.session['admin_management_notice'] = '管理者を停止しました。データは保持されています。'
        request.session['admin_management_csrf'] = secrets.token_urlsafe(32)
        return RedirectResponse('/admin/administrators', status_code=303)

    @router.post('/resume')
    async def administrator_resume(request: Request, admin_id: str = Form(...), csrf_token: str = Form(...)):
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            require_super_admin(db, request)
            csrf(request, csrf_token)
            target = db.execute('SELECT role,deleted_at FROM admin WHERE id=?', (admin_id,)).fetchone()
            if target is None:
                raise HTTPException(404, '管理者が見つかりません。')
            if target[0] == 'super_admin':
                raise HTTPException(403, '最上位管理者の状態は変更できません。')
            if target[1] is None:
                raise HTTPException(409, '管理者はすでに有効です。')
            db.execute('UPDATE admin SET deleted_at=NULL,session_version=session_version+1 WHERE id=? AND role=\'admin\'', (admin_id,))
            db.commit()
        request.session['admin_management_notice'] = '管理者を再開しました。'
        request.session['admin_management_csrf'] = secrets.token_urlsafe(32)
        return RedirectResponse('/admin/administrators', status_code=303)

    @router.post('/delete')
    async def administrator_delete(request: Request, admin_id: str = Form(...), csrf_token: str = Form(...),
                                   reauth_admin_id: str = Form(...), reauth_admin_password: str = Form(...)):
        image_name = None
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            actor = require_super_admin(db, request)
            csrf(request, csrf_token)
            target = db.execute('SELECT role,deleted_at FROM admin WHERE id=?', (admin_id,)).fetchone()
            if target is None:
                raise HTTPException(404, '管理者が見つかりません。')
            if target[0] == 'super_admin':
                raise HTTPException(403, '最上位管理者は削除できません。')
            if target[1] is None:
                raise HTTPException(409, '有効な管理者は削除できません。先に停止してください。')
            actor_row = db.execute('SELECT pwd FROM admin WHERE id=? AND role=\'super_admin\' AND deleted_at IS NULL',
                                   (actor[0],)).fetchone()
            try:
                credentials_valid = (reauth_admin_id == actor[0] and actor_row is not None
                                     and bcrypt.checkpw(reauth_admin_password.encode(), actor_row[0].encode()))
            except (ValueError, TypeError):
                credentials_valid = False
            if not credentials_valid:
                db.rollback()
                return render(request, 'list', 'ログインIDまたはパスワードが正しくありません。', 403)
            now = datetime.datetime.now(JST)
            if db.execute("""SELECT 1 FROM mentor_reservation WHERE mentor_admin_id=? AND status='active'
                AND (day>? OR (day=? AND end_time>?)) LIMIT 1""",
                (admin_id, now.date().isoformat(), now.date().isoformat(), now.strftime('%H:%M'))).fetchone():
                db.rollback()
                return render(request, 'list', '今後の有効なメンター予約があります。予約に対応してから削除してください。', 409)

            profile = db.execute('SELECT profile_image FROM mentor_profile WHERE admin_id=?', (admin_id,)).fetchone() if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mentor_profile'").fetchone() else None
            image_name = profile[0] if profile else None
            reservation_ids = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mentor_reservation'").fetchone():
                reservation_ids = [row[0] for row in db.execute(
                    'SELECT id FROM mentor_reservation WHERE mentor_admin_id=?', (admin_id,))]
            if reservation_ids and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification'").fetchone():
                placeholders = ','.join('?' for _ in reservation_ids)
                db.execute(f"DELETE FROM notification WHERE related_reservation_type='mentor' AND related_reservation_id IN ({placeholders})", reservation_ids)

            # Workshop schedule slots are shared data. Keep them and transfer their
            # owner marker to another active admin or the system marker.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reservation_available_slot'").fetchone():
                replacement = db.execute("SELECT id FROM admin WHERE id<>? AND role='super_admin' AND deleted_at IS NULL ORDER BY id LIMIT 1", (admin_id,)).fetchone()
                db.execute('UPDATE reservation_available_slot SET admin_id=? WHERE admin_id=?', (replacement[0] if replacement else 'system', admin_id))

            for table, column in (
                ('mentor_reservation', 'mentor_admin_id'),
                ('mentor_available_slot', 'admin_id'),
                ('mentor_profile', 'admin_id'),
                ('admin_notification', 'recipient_admin_id'),
                ('tekne_chat_admin_audit', 'admin_id'),
            ):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                    if column in columns:
                        db.execute(f'DELETE FROM {table} WHERE {column}=?', (admin_id,))

            # Remove notifications sent in explicit admin broadcasts with their batch.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_batch'").fetchone():
                columns = {row[1] for row in db.execute('PRAGMA table_info(notification_batch)')}
                if {'sender_type', 'sender_id'} <= columns:
                    batch_ids = [row[0] for row in db.execute(
                        "SELECT id FROM notification_batch WHERE sender_type='admin' AND sender_id=?", (admin_id,))]
                    if batch_ids and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification'").fetchone():
                        placeholders = ','.join('?' for _ in batch_ids)
                        db.execute(f'DELETE FROM notification WHERE batch_id IN ({placeholders})', batch_ids)
                    db.execute("DELETE FROM notification_batch WHERE sender_type='admin' AND sender_id=?", (admin_id,))
            db.execute('DELETE FROM admin WHERE id=? AND role=\'admin\' AND deleted_at IS NOT NULL', (admin_id,))
            db.commit()

        if image_dir and image_name:
            candidate = (image_dir / image_name).resolve()
            if Path(image_name).name == image_name and candidate.parent == image_dir:
                candidate.unlink(missing_ok=True)
        request.session['admin_management_notice'] = '管理者と関連データを完全に削除しました。'
        request.session['admin_management_csrf'] = secrets.token_urlsafe(32)
        return RedirectResponse('/admin/administrators', status_code=303)

    return router


def build_password_router(database_path):
    router = APIRouter()
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / 'templates'))

    @router.get('/admin/first-password')
    async def first_password_page(request: Request):
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            account = active_admin(db, request)
            if account is None:
                raise HTTPException(403, '管理者としてログインしてください。')
            required = db.execute('SELECT must_change_password FROM admin WHERE id=?', (account[0],)).fetchone()[0]
        if not required:
            return RedirectResponse('/admin/mypage', 303)
        token = request.session.setdefault('admin_first_password_csrf', secrets.token_urlsafe(32))
        return templates.TemplateResponse(request=request, name='admin/first_password.html',
            context={'request': request, 'csrf_token': token})

    @router.post('/admin/first-password')
    async def first_password_change(request: Request, new_password: str = Form(...),
                                   confirm_password: str = Form(...), csrf_token: str = Form(...)):
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            account = active_admin(db, request)
            if account is None:
                raise HTTPException(403, '管理者としてログインしてください。')
            token = request.session.get('admin_first_password_csrf', '')
            if not token or not secrets.compare_digest(token.encode(), csrf_token.encode()):
                raise HTTPException(403, 'CSRFトークンが不正です。')
            required = db.execute('SELECT must_change_password FROM admin WHERE id=?', (account[0],)).fetchone()[0]
            if not required:
                return RedirectResponse('/admin/mypage', 303)
            try:
                validate_admin(account[0], '', new_password)
                if new_password != confirm_password:
                    raise ValueError('新しいパスワードと確認用パスワードが一致しません。')
                hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                db.execute('UPDATE admin SET pwd=?,must_change_password=0,session_version=session_version+1 WHERE id=?',
                           (hashed, account[0]))
                db.commit()
            except ValueError as exc:
                db.rollback()
                token = request.session.setdefault('admin_first_password_csrf', secrets.token_urlsafe(32))
                return templates.TemplateResponse(request=request, name='admin/first_password.html',
                    context={'request': request, 'csrf_token': token, 'error': str(exc)}, status_code=400)
        request.session.clear()
        request.session['admin_password_notice'] = 'パスワードを設定しました。新しいパスワードでログインしてください。'
        return RedirectResponse('/admin/login', 303)

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
