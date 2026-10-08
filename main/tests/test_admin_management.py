import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import bcrypt
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

import main
import create_admin
from admin_management import initialize_admin_roles, migrate_admin_database, build_admin_router, build_password_router, session_secret
from mentor_reservations import initialize_mentor_tables, build_router as build_mentor_router


class AdminManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.db'
        self.password = 'secure-password-123'
        self.hash = bcrypt.hashpw(self.password.encode(), bcrypt.gensalt(rounds=4)).decode()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE admin(id TEXT PRIMARY KEY NOT NULL,pwd TEXT NOT NULL)')
            db.executemany('INSERT INTO admin VALUES (?,?)', [('26A001', self.hash), ('ordinary', self.hash)])
            initialize_admin_roles(db)
            initialize_mentor_tables(db)
            db.commit()
        self.app = FastAPI()
        templates = Jinja2Templates(directory=str(Path(main.__file__).parent / 'templates'),
            context_processors=[main.admin_template_context])
        self.app.include_router(build_admin_router(self.path, templates))
        self.app.include_router(build_password_router(self.path))
        self.app.include_router(build_mentor_router(self.path, templates, Path(self.temp.name) / 'images'))
        self.app.add_api_route('/admin/login', main.AdminLogin, methods=['POST'])
        self.app.add_api_route('/admin/login', main.AdminLoginPage, methods=['GET'])
        self.app.add_api_route('/admin/mypage', lambda: Response('existing admin feature'), methods=['GET'])
        self.app.add_middleware(main.LoginCheckMiddleware)
        self.key = 'test-key-with-at-least-32-characters'
        self.app.add_middleware(SessionMiddleware, secret_key=self.key)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.addCleanup(self.db.close)
        self.addCleanup(patch.stopall)
        patch.object(main, 'DATABASE_PATH', self.path).start()
        patch.object(main, 'cursor', self.db.cursor()).start()

    def http(self, path, cookie='', method='GET', data=None):
        body = urlencode(data or {}).encode()
        messages = []
        sent = False
        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            await asyncio.sleep(100)
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'method': method, 'path': path, 'root_path': '',
                 'scheme': 'http', 'query_string': b'', 'server': ('test', 80), 'client': ('test', 1),
                 'headers': [(b'cookie', cookie.encode()), (b'content-type', b'application/x-www-form-urlencoded')]}
        asyncio.run(self.app(scope, receive, send))
        start = next(m for m in messages if m['type'] == 'http.response.start')
        headers = dict(start['headers'])
        updated = headers.get(b'set-cookie', b'').decode().split(';')[0] or cookie
        content = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body').decode()
        return start['status'], content, updated

    def login(self, admin_id):
        status, _, cookie = self.http('/admin/login', method='POST', data={'id': admin_id, 'pwd': self.password})
        self.assertEqual(status, 303)
        return cookie

    def token(self, cookie):
        status, html, cookie = self.http('/admin/administrators/new', cookie)
        self.assertEqual(status, 200)
        import re
        return re.search('name="csrf_token" value="([^"]+)"', html).group(1), cookie

    def test_migration_preserves_hashes_and_never_promotes_others(self):
        initialize_admin_roles(self.db)
        self.assertEqual(self.db.execute('SELECT id,pwd,role FROM admin ORDER BY id').fetchall(),
                         [('26A001', self.hash, 'super_admin'), ('ordinary', self.hash, 'admin')])
        self.db.execute("UPDATE admin SET role='admin' WHERE id='26A001'")
        initialize_admin_roles(self.db)
        self.assertEqual(self.db.execute("SELECT role FROM admin WHERE id='26A001'").fetchone()[0], 'admin')

    def test_permissions_and_existing_features(self):
        ordinary = self.login('ordinary')
        self.assertEqual(self.http('/admin/mypage', ordinary)[0], 200)
        for url in ('/admin/administrators', '/admin/administrators/new'):
            self.assertEqual(self.http(url, ordinary)[0], 403)
            self.assertEqual(self.http(url)[0], 303)
        for url in ('/admin/administrators/new', '/admin/administrators/delete'):
            self.assertEqual(self.http(url, ordinary, 'POST', {'admin_id': 'victim', 'name': 'Name',
                'password': self.password, 'csrf_token': 'x', 'role': 'super_admin'})[0], 403)
        super_cookie = self.login('26A001')
        status, html, _ = self.http('/admin/administrators', super_cookie)
        self.assertEqual(status, 200)
        self.assertIn('管理者管理', html)
        self.assertIn('26A001', html)
        self.assertNotIn(self.hash, html)
        self.assertEqual(self.http('/admin/administrators/permissions', super_cookie)[0], 404)
        self.assertNotIn('権限管理', html)
        # Reuse the same base template to verify ordinary sidebar rendering.
        status, html, _ = self.http('/admin/login', method='POST', data={'id': 'ordinary', 'pwd': 'wrong'})
        self.assertEqual(status, 401)
        request = main.Request({'type': 'http', 'path': '/admin/mypage', 'headers': [], 'session': {}})
        request.state.admin_role = 'admin'
        templates = Jinja2Templates(directory=str(Path(main.__file__).parent / 'templates'), context_processors=[main.admin_template_context])
        html = templates.TemplateResponse(request=request, name='admin/base.html').body.decode()
        self.assertNotIn('管理者管理', html)
        self.assertIn('研究成果管理', html)

    def test_create_validation_csrf_duplicate_and_forced_role(self):
        cookie = self.login('26A001')
        token, cookie = self.token(cookie)
        data = {'admin_id': 'new', 'name': '<script>Name</script>', 'password': self.password, 'csrf_token': token, 'role': 'super_admin'}
        self.assertEqual(self.http('/admin/administrators/new', cookie, 'POST', dict(data, csrf_token='bad'))[0], 403)
        self.assertEqual(self.http('/admin/administrators/new', cookie, 'POST', dict(data, password='short'))[0], 400)
        self.assertEqual(self.http('/admin/administrators/new', cookie, 'POST', dict(data, password='あ'*25))[0], 400)
        status, _, cookie = self.http('/admin/administrators/new', cookie, 'POST', data)
        self.assertEqual(status, 303)
        row = self.db.execute("SELECT pwd,role FROM admin WHERE id='new'").fetchone()
        self.assertTrue(bcrypt.checkpw(self.password.encode(), row[0].encode()))
        self.assertEqual(row[1], 'admin')
        token, cookie = self.token(cookie)
        self.assertEqual(self.http('/admin/administrators/new', cookie, 'POST', dict(data, csrf_token=token))[0], 400)
        html = self.http('/admin/administrators', cookie)[1]
        self.assertEqual(self.db.execute("SELECT name FROM admin WHERE id='new'").fetchone()[0], '')
        self.assertNotIn('<script>Name</script>', html)
        self.assertNotIn(row[0], html)

    def test_delete_protection_history_and_session_revocation(self):
        super_cookie = self.login('26A001')
        ordinary_cookie = self.login('ordinary')
        token, super_cookie = self.token(super_cookie)
        data = {'admin_id': 'ordinary', 'csrf_token': token}
        self.assertEqual(self.http('/admin/administrators/delete', super_cookie, 'POST', dict(data, admin_id='26A001'))[0], 403)
        self.assertEqual(self.http('/admin/administrators/delete', super_cookie, 'POST', dict(data, csrf_token='bad'))[0], 403)
        self.db.execute("INSERT INTO mentor_profile(admin_id,display_name,is_published,created_at,updated_at) VALUES ('ordinary','Keep',1,'now','now')")
        self.db.execute("""INSERT INTO mentor_reservation(student_id,mentor_admin_id,day,start_time,end_time,meeting_type,consultation,created_at)
            VALUES('s','ordinary','2099-01-01','13:00','13:30','offline','Keep history','now')""")
        self.db.commit()
        self.assertEqual(self.http('/admin/administrators/delete', super_cookie, 'POST', data)[0], 409)
        self.assertEqual(self.http('/admin/mypage', ordinary_cookie)[0], 200)
        self.db.execute("UPDATE mentor_reservation SET day='2000-01-01'")
        self.db.commit()
        self.assertEqual(self.http('/admin/administrators/delete', super_cookie, 'POST', data)[0], 303)
        self.assertEqual(self.db.execute('SELECT consultation FROM mentor_reservation').fetchone()[0], 'Keep history')
        self.assertEqual(self.db.execute('SELECT display_name,is_published FROM mentor_profile').fetchone(), ('Keep', 0))
        self.assertEqual(self.http('/admin/mypage', ordinary_cookie)[0], 303)
        self.assertEqual(self.http('/admin/login', method='POST', data={'id': 'ordinary', 'pwd': self.password})[0], 401)
        self.assertIsNotNone(self.db.execute("SELECT deleted_at FROM admin WHERE id='ordinary'").fetchone()[0])
        self.assertEqual(self.http('/admin/administrators/new', super_cookie, 'POST', dict(data, name='Reuse', password=self.password))[0], 400)
        # Even an in-flight profile save cannot make a deleted account bookable.
        self.db.execute("UPDATE mentor_profile SET is_published=1 WHERE admin_id='ordinary'")
        self.db.commit()
        router = build_mentor_router(self.path, main.templates, Path(self.temp.name))
        endpoint = next(r.endpoint for r in router.routes if r.path == '/mentor-reservation/{admin_id}/availability')
        request = main.Request({'type': 'http', 'path': '/', 'headers': [], 'session': {'user_login': True, 'user_id': 's'}})
        with self.assertRaises(main.HTTPException) as rejected:
            asyncio.run(endpoint(request, 'ordinary', '2099-01-01', 'offline'))
        self.assertEqual(rejected.exception.status_code, 404)

    def test_db_role_changes_apply_to_existing_sessions(self):
        cookie = self.login('26A001')
        self.db.execute("UPDATE admin SET role='admin' WHERE id='26A001'")
        self.db.commit()
        self.assertEqual(self.http('/admin/administrators', cookie)[0], 403)
        self.assertEqual(self.http('/admin/mypage', cookie)[0], 200)

    def test_cli_creates_super_and_does_not_overwrite(self):
        with patch.object(create_admin, 'DATABASE_PATH', self.path), patch('builtins.input', return_value='cli'), patch('getpass.getpass', return_value=self.password):
            create_admin.main()
            create_admin.main()
        self.assertEqual(self.db.execute("SELECT role,name FROM admin WHERE id='cli'").fetchone(), ('super_admin', ''))
        self.assertEqual(self.db.execute("SELECT pwd FROM admin WHERE id='26A001'").fetchone()[0], self.hash)

    def test_session_key_persists_and_fixed_key_is_removed(self):
        with patch.dict('os.environ', {}, clear=True):
            key = session_secret(self.path)
            self.assertEqual(key, session_secret(self.path))
            self.assertNotEqual(key, 'TEKNE')

    def test_backup_and_repeat_migration_preserve_unknown_accounts(self):
        path = Path(self.temp.name) / 'legacy.db'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE admin(id TEXT PRIMARY KEY,pwd TEXT NOT NULL)')
            db.executemany('INSERT INTO admin VALUES (?,?)', [('26A001', self.hash), ('unknown', self.hash)])
        migrate_admin_database(path)
        backups = list(path.parent.glob('legacy.db.admin_roles_v1.*.bak'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('SELECT * FROM admin ORDER BY id').fetchall(), [('26A001', self.hash), ('unknown', self.hash)])
            self.assertEqual(len(db.execute('PRAGMA table_info(admin)').fetchall()), 2)
        migrate_admin_database(path)
        self.assertEqual(list(path.parent.glob('legacy.db.admin_roles_v1.*.bak')), backups)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT id,pwd,role FROM admin ORDER BY id').fetchall(),
                             [('26A001', self.hash, 'super_admin'), ('unknown', self.hash, 'admin')])

    def test_old_fixed_key_cannot_forge_super_admin_session(self):
        import base64
        import json
        from itsdangerous import TimestampSigner
        payload = {'admin_login': True, 'admin_id': '26A001',
                   'admin_time': main.datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        fake = TimestampSigner('TEKNE').sign(base64.b64encode(json.dumps(payload).encode())).decode()
        self.assertEqual(self.http('/admin/administrators', 'session=' + fake)[0], 303)

    def profile_tokens(self, cookie):
        import re
        status, html, cookie = self.http('/admin/profile', cookie)
        self.assertEqual(status, 200)
        profile = re.search('name="csrf" value="([^"]+)"', html).group(1)
        password = re.search('name="csrf_token" value="([^"]+)"', html).group(1)
        return profile, password, cookie

    def test_own_profile_updates_shared_name_and_public_mentor_info(self):
        cookie = self.login('ordinary')
        token, _, cookie = self.profile_tokens(cookie)
        data = {'display_name': '<script>Owner</script>', 'description': 'Shared biography',
                'is_published': '1', 'csrf': token, 'admin_id': '26A001'}
        self.assertEqual(self.http('/admin/profile', cookie, 'POST', dict(data, csrf='bad'))[0], 303)
        self.assertEqual(self.db.execute("SELECT name FROM admin WHERE id='ordinary'").fetchone()[0], '')
        status, _, cookie = self.http('/admin/profile', cookie, 'POST', data)
        self.assertEqual(status, 303)
        self.assertEqual(self.db.execute("SELECT name FROM admin WHERE id='ordinary'").fetchone()[0], data['display_name'])
        self.assertEqual(self.db.execute("SELECT name FROM admin WHERE id='26A001'").fetchone()[0], '')
        self.assertEqual(self.db.execute('SELECT admin_id,display_name,description,is_published FROM mentor_profile').fetchone(),
                         ('ordinary', data['display_name'], 'Shared biography', 1))
        request = main.Request({'type': 'http', 'path': '/mentor-reservation', 'headers': [], 'session': {'user_id': 'student'}})
        router = build_mentor_router(self.path, main.templates, Path(self.temp.name))
        endpoint = next(r.endpoint for r in router.routes if r.path == '/mentor-reservation')
        html = asyncio.run(endpoint(request)).body.decode()
        self.assertIn('&lt;script&gt;Owner&lt;/script&gt;', html)
        self.assertIn('Shared biography', html)
        token, _, cookie = self.profile_tokens(cookie)
        self.http('/admin/profile', cookie, 'POST', dict(data, is_published='0', csrf=token))
        self.assertNotIn('Shared biography', asyncio.run(endpoint(request)).body.decode())
        self.assertEqual(self.http('/admin/profile/26A001', cookie)[0], 404)

    def test_password_change_validates_current_confirmation_length_and_csrf(self):
        cookie = self.login('ordinary')
        _, token, cookie = self.profile_tokens(cookie)
        data = {'current_password': self.password, 'new_password': 'replacement-password-123',
                'confirm_password': 'replacement-password-123', 'csrf_token': token, 'admin_id': '26A001'}
        self.assertEqual(self.http('/admin/profile/password', cookie, 'POST', dict(data, csrf_token='bad'))[0], 403)
        for invalid in (dict(data, current_password='wrong'), dict(data, confirm_password='mismatch'),
                        dict(data, new_password='short', confirm_password='short'),
                        dict(data, new_password='あ'*25, confirm_password='あ'*25)):
            self.assertEqual(self.http('/admin/profile/password', cookie, 'POST', invalid)[0], 303)
            self.assertEqual(self.db.execute("SELECT pwd,session_version FROM admin WHERE id='ordinary'").fetchone(), (self.hash, 0))
        old_cookie = self.login('ordinary')
        status, _, cookie = self.http('/admin/profile/password', cookie, 'POST', data)
        self.assertEqual(status, 303)
        hashed, version = self.db.execute("SELECT pwd,session_version FROM admin WHERE id='ordinary'").fetchone()
        self.assertTrue(bcrypt.checkpw(data['new_password'].encode(), hashed.encode()))
        self.assertEqual(version, 1)
        self.assertEqual(self.db.execute("SELECT pwd FROM admin WHERE id='26A001'").fetchone()[0], self.hash)
        self.assertEqual(self.http('/admin/mypage', old_cookie)[0], 303)
        self.assertEqual(self.http('/admin/profile', old_cookie)[0], 303)
        self.assertEqual(self.http('/admin/login', method='POST', data={'id': 'ordinary', 'pwd': self.password})[0], 401)
        status, _, new_cookie = self.http('/admin/login', method='POST', data={'id': 'ordinary', 'pwd': data['new_password']})
        self.assertEqual(status, 303)
        self.assertEqual(self.http('/admin/profile', new_cookie)[0], 200)

    def test_profile_migration_preserves_image_description_and_visibility(self):
        self.db.execute("UPDATE admin SET name='Old separate name' WHERE id='ordinary'")
        self.db.execute("INSERT INTO mentor_profile(admin_id,display_name,description,is_published,profile_image,created_at,updated_at) VALUES('ordinary','Existing mentor','Keep biography',1,'keep.webp','old','old')")
        self.db.commit()
        initialize_admin_roles(self.db)
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT name,pwd FROM admin WHERE id='ordinary'").fetchone(), ('Existing mentor', self.hash))
        self.assertEqual(self.db.execute('SELECT display_name,description,is_published,profile_image FROM mentor_profile').fetchone(), ('Existing mentor', 'Keep biography', 1, 'keep.webp'))
        initialize_admin_roles(self.db)
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM mentor_profile').fetchone()[0], 1)

    def test_add_form_only_requests_id_and_password(self):
        _, html, _ = self.http('/admin/administrators/new', self.login('26A001'))
        self.assertNotIn('name="name"', html)
        self.assertIn('name="admin_id"', html)
        self.assertIn('name="password"', html)


if __name__ == '__main__':
    unittest.main()
