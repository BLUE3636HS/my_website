import asyncio
import datetime
import hashlib
import hmac
import io
import json
import secrets
import sqlite3
import tempfile
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import FormData, UploadFile
from starlette.requests import Request

import main
from tekne_chat import (TekneChatService, cleanup_expired_groups, create_chat_session,
                        initialize_tekne_chat, link_student_identity)
from school_features import initialize_school_feature_settings
from studies import connect_studies, get_templates, initialize_studies, study_file_path, study_pdf_path


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
            db.executescript("""CREATE TABLE student(id TEXT, pwd TEXT, school TEXT, name TEXT, furigana TEXT);
                CREATE TABLE registered_school(name TEXT NOT NULL UNIQUE);
                INSERT INTO registered_school VALUES ('school1'), ('school2');
                CREATE TABLE teacher(id TEXT, pwd TEXT, school TEXT);
                CREATE TABLE admin(id TEXT PRIMARY KEY, pwd TEXT, name TEXT, role TEXT, deleted_at TEXT, session_version INTEGER DEFAULT 0);
                INSERT INTO admin VALUES ('person', 'x', 'Person', 'admin', NULL, 0);
                INSERT INTO student VALUES ('a', 'x', 'school1', 'A', ''), ('b', 'x', 'school2', 'B', ''), ('c', 'x', 'school1', 'C', '');
                INSERT INTO teacher VALUES ('teacher1', 'x', 'school1');
                CREATE TABLE unrelated(value TEXT);
                CREATE TABLE notification(recipient_user_id TEXT, is_read INTEGER);
                INSERT INTO unrelated VALUES ('preserve');""")
            initialize_studies(db)
            initialize_school_feature_settings(db)
            db.commit()
            initialize_tekne_chat(db)
            self.template = get_templates(db)[0]
        self.chat = TekneChatService(self.path, self.root / 'chat-files')
        for name, value in [('DATABASE_PATH', self.path), ('UPLOADS_DIR', self.uploads)]:
            p = patch.object(main, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.session = {'user_login': True, 'user_id': 'a', 'study_csrf_token': 'csrf', 'user_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        self.session['tekne_chat_session'] = create_chat_session(self.path, 'a')

    def rows(self, sql, args=()):
        with closing(connect_studies(self.path)) as db:
            return db.execute(sql, args).fetchall()

    def render_teacher_studylist(self):
        endpoint = next(route.endpoint for route in main.app.routes
                        if getattr(route, 'path', '') == '/teacher/studylist' and 'GET' in route.methods)
        coroutine = endpoint(request('/teacher/studylist', {'teacher_login': True, 'teacher_id': 'teacher1'}))
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        self.fail('Teacher study list unexpectedly awaited an async operation')

    def render_student_studylist(self, session=None):
        with patch.object(main, 'tekne_chat_service', self.chat), \
             patch.object(main.templates, 'context_processors', [
                 lambda _request: {'student_features': {}, 'unread_notification_count': 0,
                                    'is_super_admin': False}
             ]):
            endpoint = next(route.endpoint for route in main.app.routes
                            if getattr(route, 'path', '') == '/addform' and 'GET' in route.methods)
            coroutine = endpoint(request('/addform', self.session if session is None else session))
            try:
                coroutine.send(None)
            except StopIteration as result:
                return result.value
        self.fail('Student study list unexpectedly awaited an async operation')

    def create_study_group(self):
        group_id = self.chat.create_group(self.session, '共同研究')["id"]
        self.chat.invite(self.session, group_id, 'c')
        return group_id

    def token(self):
        nonce = secrets.token_hex(32)
        return nonce + '.' + hmac.new(b'csrf', nonce.encode(), hashlib.sha256).hexdigest()

    def submit(self, mode='pdf', token=None, session=None, **values):
        data = {'registration_type': mode, 'name': '研究名', 'introduce': '紹介文',
                'csrf_token': 'csrf', 'submission_token': token or self.token(), **values}
        if mode == 'pdf' and 'pdf' not in data:
            data['pdf'] = UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf')
        req = request('/addform', self.session if session is None else session)
        req._form = FormData(data)
        return asyncio.run(main.Add(req))

    def insert_template_study(self, userid='a', name='テンプレート研究', template_id=None, values=None):
        """テンプレート提出は廃止したが、過去に提出されたデータは残るので直接DBに作る。

        values は {field_id: 本文}。省略した項目は空文字で保存する。"""
        values = values or {}
        with closing(connect_studies(self.path)) as db, db:
            template_id = template_id or self.template['id']
            template = next(item for item in get_templates(db, include_inactive=True) if item['id'] == template_id)
            study_id = db.execute("""INSERT INTO study
                (name, introduce, filename, pdfpath, userid, time, registration_type, template_id)
                VALUES (?, '紹介文', '', '', ?, '2026-10-01 10:00:00', 'template', ?)""",
                (name, userid, template_id)).lastrowid
            db.executemany('INSERT INTO study_field_value(study_id, field_id, value) VALUES (?, ?, ?)',
                           [(study_id, field['id'], values.get(field['id'], '')) for field in template['fields']])
        return study_id

    def insert_legacy_file_study(self, filename, content, stored_name):
        """PDF以外のファイルも提出できた頃のデータ。"""
        (self.uploads / stored_name).write_bytes(content)
        with closing(connect_studies(self.path)) as db, db:
            return db.execute("""INSERT INTO study
                (name, introduce, filename, pdfpath, userid, time, registration_type)
                VALUES ('旧ファイル', '紹介文', ?, ?, 'a', '2026-10-01 10:00:00', 'pdf')""",
                (filename, stored_name)).lastrowid





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
            self.assertEqual(db.execute('SELECT submission_kind, chat_group_id FROM study').fetchall(), [('individual', None)])











    def test_initialization_clears_legacy_required_template_fields(self):
        with closing(connect_studies(self.path)) as db, db:
            db.execute('UPDATE study_template_field SET required=1 WHERE id=1')
            initialize_studies(db)
            self.assertEqual(db.execute('SELECT required FROM study_template_field WHERE id=1').fetchone(), (0,))
            self.assertFalse(get_templates(db)[0]['fields'][0]['required'])





















if __name__ == '__main__':
    unittest.main()
