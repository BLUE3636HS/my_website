import asyncio
import hashlib
import hmac
import io
import json
import secrets
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import FormData, UploadFile
from starlette.requests import Request

import main
from studies import connect_studies, get_templates, initialize_studies, study_pdf_path


def request(path, session):
    return Request({"type": "http", "method": "GET", "path": path, "root_path": "",
                    "scheme": "http", "server": ("test", 80), "client": ("test", 1),
                    "headers": [], "query_string": b"", "session": session})


def sample_pdf():
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>']
    content = b'%PDF-1.4\n'
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content += str(i).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(content)
    content += b'xref\n0 4\n0000000000 65535 f \n'
    for offset in offsets:
        content += f'{offset:010d} 00000 n \n'.encode()
    return content + f'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'test.db'
        self.uploads = self.root / 'uploads'
        self.uploads.mkdir()
        with closing(connect_studies(self.path)) as db:
            db.executescript("""CREATE TABLE student(id TEXT, pwd TEXT, school TEXT);
                CREATE TABLE teacher(id TEXT, pwd TEXT, school TEXT);
                INSERT INTO student VALUES ('a', 'x', 'school1'), ('b', 'x', 'school2');
                INSERT INTO teacher VALUES ('teacher1', 'x', 'school1');
                CREATE TABLE unrelated(value TEXT);
                CREATE TABLE notification(recipient_user_id TEXT, is_read INTEGER);
                INSERT INTO unrelated VALUES ('preserve');""")
            initialize_studies(db)
            self.template = get_templates(db)[0]
        for name, value in [('DATABASE_PATH', self.path), ('UPLOADS_DIR', self.uploads)]:
            p = patch.object(main, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.session = {'user_login': True, 'user_id': 'a', 'study_csrf_token': 'csrf'}

    def rows(self, sql, args=()):
        with closing(connect_studies(self.path)) as db:
            return db.execute(sql, args).fetchall()

    def token(self):
        nonce = secrets.token_hex(32)
        return nonce + '.' + hmac.new(b'csrf', nonce.encode(), hashlib.sha256).hexdigest()

    def submit(self, mode='template', token=None, session=None, **values):
        data = {'registration_type': mode, 'template_id': str(self.template['id']),
                'csrf_token': 'csrf', 'submission_token': token or self.token(), **values}
        req = request('/addform', self.session if session is None else session)
        req._form = FormData(data)
        return asyncio.run(main.Add(req))

    def test_empty_template_and_independent_name_and_values(self):
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.rows('SELECT name, introduce, registration_type, template_id FROM study'),
                         [('', '', 'template', self.template['id'])])
        self.assertEqual(self.rows('SELECT value FROM study_field_value'), [('',)] * 9)
        values = {f"field_{field['id']}": f"長文\n{field['label']} <script>" for field in self.template['fields']}
        self.submit(name='独立した研究名', introduce='紹介文', **values)
        self.assertEqual(dict(self.rows('SELECT field_id, value FROM study_field_value WHERE study_id=2')),
                         {field['id']: values[f"field_{field['id']}"] for field in self.template['fields']})
        self.assertEqual(self.rows('SELECT name FROM study WHERE id=2'), [('独立した研究名',)])

    def test_seed_is_idempotent_and_does_not_touch_other_tables(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("UPDATE study_template SET name='edited'")
            db.commit()
            initialize_studies(db)
            initialize_studies(db)
        self.assertEqual(self.rows('SELECT name FROM study_template'), [('edited',)])
        self.assertEqual(len(self.rows('SELECT * FROM study_template_field')), 9)
        self.assertEqual(self.rows('SELECT * FROM unrelated'), [('preserve',)])

    def test_legacy_schema_migration_preserves_rows(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE study(id INTEGER PRIMARY KEY, name TEXT, introduce TEXT, filename TEXT, pdfpath TEXT, userid TEXT, time TEXT)')
            db.execute("INSERT INTO study VALUES (3, 'old', '', 'old.pdf', '3.pdf', 'a', 'date')")
            db.commit()
            initialize_studies(db)
            self.assertEqual(db.execute('SELECT id, name, registration_type FROM study').fetchall(), [(3, 'old', 'pdf')])

    def test_retries_are_idempotent(self):
        token = self.token()
        first = self.submit(token=token)
        second = self.submit(token=token)
        self.assertEqual(json.loads(first.body), json.loads(second.body))
        self.assertEqual(len(self.rows('SELECT id FROM study')), 1)

    def test_concurrent_requests_only_create_one_submission(self):
        token = self.token()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: json.loads(self.submit(token=token).body), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.rows('SELECT id FROM study')), 1)

    def test_rejects_auth_csrf_unknown_templates_and_fields(self):
        for options, status in [({'session': {}}, 401), ({'csrf_token': 'wrong'}, 403),
                                ({'submission_token': 'wrong'}, 403), ({'csrf_token': '不正'}, 403), ({'mode': 'other'}, 422),
                                ({'template_id': '999'}, 422), ({'field_999': 'x'}, 422)]:
            with self.subTest(options=options), self.assertRaises(HTTPException) as error:
                self.submit(**options)
            self.assertEqual(error.exception.status_code, status)
        self.assertEqual(self.rows('SELECT id FROM study'), [])

    def test_dynamic_second_template_and_required_field(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("INSERT INTO study_template(id,name) VALUES (2,'別テンプレート')")
            db.execute("INSERT INTO study_template_field(id,template_id,label,position,required) VALUES (20,2,'必須項目',0,1)")
            db.commit()
        with self.assertRaises(HTTPException):
            self.submit(template_id='2', field_20='  ')
        with self.assertRaises(HTTPException):
            self.submit(template_id='1', field_20='他テンプレートの項目')
        self.submit(template_id='2', field_20='入力')
        self.assertEqual(self.rows('SELECT field_id,value FROM study_field_value'), [(20, '入力')])

    def test_pdf_save_serve_and_invalid_pdf(self):
        for filename, content in [('fake.pdf', b'not pdf'), ('wrong.txt', sample_pdf()), ('broken.pdf', b'%PDF-1.4\n')]:
            with self.assertRaises(HTTPException):
                self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(content), filename=filename))
        self.submit(mode='pdf', userid='b', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='../test.pdf'))
        filename, path, user = self.rows('SELECT filename,pdfpath,userid FROM study')[0]
        self.assertEqual(filename, 'test.pdf')
        self.assertEqual(user, 'a')
        self.assertEqual((self.uploads / path).read_bytes(), sample_pdf())
        self.assertEqual(Path(asyncio.run(main.pdf(1)).path), self.uploads / path)
        self.submit()
        with self.assertRaises(HTTPException):
            asyncio.run(main.pdf(2))

    def test_file_write_failure_rolls_back(self):
        with patch.object(main.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertLogs(level='ERROR'), self.assertRaises(HTTPException) as error:
                self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf'))
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(self.rows('SELECT id FROM study'), [])
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_db_failure_removes_saved_pdf(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("CREATE TRIGGER fail_study BEFORE INSERT ON study BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            db.commit()
        with self.assertLogs(level='ERROR'), self.assertRaises(HTTPException):
            self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf'))
        self.assertEqual(self.rows('SELECT id FROM study'), [])
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_teacher_scope_csrf_and_cascading_delete(self):
        self.submit()
        self.submit(session={**self.session, 'user_id': 'b'})
        session = {'teacher_login': True, 'teacher_id': 'teacher1', 'teacher_studylist_csrf_token': 'token'}
        for study_id, csrf in [(2, 'token'), (1, 'wrong')]:
            asyncio.run(main.TeacherDeleteStudy(request('/teacher/studylist', session), study_id, csrf))
            self.assertEqual(len(self.rows('SELECT id FROM study')), 2)
        asyncio.run(main.TeacherDeleteStudy(request('/teacher/studylist', session), 1, 'token'))
        self.assertEqual(self.rows('SELECT id FROM study'), [(2,)])
        self.assertEqual(self.rows('SELECT DISTINCT study_id FROM study_field_value'), [(2,)])

    def test_admin_deletes_pdf_and_template_only(self):
        unrelated = self.uploads / 'keep.txt'
        unrelated.write_text('keep')
        self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf'))
        self.submit()
        for study_id in (1, 2):
            asyncio.run(main.AdminDeleteStudy(request('/admin/studies', {'admin_login': True}), study_id))
        self.assertEqual(self.rows('SELECT * FROM study_field_value'), [])
        self.assertEqual(list(self.uploads.iterdir()), [unrelated])
        for path in ('../outside.pdf', 'profile/avatar.pdf', 'keep.txt'):
            with self.assertRaises(ValueError):
                study_pdf_path(self.uploads, path)

    def test_teacher_deletes_pdf(self):
        self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf'))
        session = {'teacher_login': True, 'teacher_id': 'teacher1', 'teacher_studylist_csrf_token': 'token'}
        asyncio.run(main.TeacherDeleteStudy(request('/teacher/studylist', session), 1, 'token'))
        self.assertEqual(list(self.uploads.iterdir()), [])
        self.assertEqual(self.rows('SELECT id FROM study'), [])

    def test_real_multipart_request(self):
        fields = {'registration_type': 'template', 'template_id': str(self.template['id']),
                  'csrf_token': 'csrf', 'submission_token': self.token(),
                  'field_1': '日本語の本文\n2行目'}
        body = b''
        for name, value in fields.items():
            body += f'--testboundary\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        body += b'--testboundary--\r\n'
        req = request('/addform', self.session)
        req.scope['method'] = 'POST'
        req.scope['headers'] = [(b'content-type', b'multipart/form-data; boundary=testboundary')]
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        req._receive = receive
        response = asyncio.run(main.Add(req))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.rows('SELECT value FROM study_field_value WHERE field_id=1'), [('日本語の本文\n2行目',)])

    def test_form_and_all_three_lists_render(self):
        self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf'))
        self.submit()
        # Resolve by route because legacy code reuses the StudyList function name.
        for path, session in [('/addform', self.session), ('/studylist', self.session),
                              ('/teacher/studylist', {'teacher_login': True, 'teacher_id': 'teacher1'}),
                              ('/admin/studies', {'admin_login': True, 'admin_id': 'admin'})]:
            endpoint = next(route.endpoint for route in main.app.routes if getattr(route, 'path', '') == path and 'GET' in route.methods)
            response = asyncio.run(endpoint(request(path, session)))
            html = response.body.decode()
            self.assertEqual(response.status_code, 200)
            if path == '/addform':
                self.assertIn('研究レポート（基本）', html)
                self.assertIn('study-templates', html)
                self.assertLess(html.index('id="introduce"'), html.index('class="study-modes"'))
                self.assertEqual(len(response.context['study_templates'][0]['fields']), 9)
            else:
                self.assertIn('/uploads/1.pdf', html)
                self.assertNotIn('/uploads/2.pdf', html)
                self.assertIn('本文表示機能は未実装', html)


if __name__ == '__main__':
    unittest.main()
