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
                            if getattr(route, 'path', '') == '/studylist' and 'GET' in route.methods)
            coroutine = endpoint(request('/studylist', self.session if session is None else session))
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

    def submit(self, mode='template', token=None, session=None, **values):
        data = {'registration_type': mode, 'template_id': str(self.template['id']),
                'name': '研究名', 'introduce': '紹介文',
                'csrf_token': 'csrf', 'submission_token': token or self.token(), **values}
        req = request('/addform', self.session if session is None else session)
        req._form = FormData(data)
        return asyncio.run(main.Add(req))

    def test_empty_template_and_independent_name_and_values(self):
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.rows('SELECT name, introduce, registration_type, template_id FROM study'),
                         [('研究名', '紹介文', 'template', self.template['id'])])
        self.assertEqual(self.rows('SELECT value FROM study_field_value'), [('',)] * 9)
        values = {f"field_{field['id']}": f"長文\n{field['label']} <script>" for field in self.template['fields']}
        self.submit(name='独立した研究名', introduce='紹介文', **values)
        self.assertEqual(dict(self.rows('SELECT field_id, value FROM study_field_value WHERE study_id=2')),
                         {field['id']: values[f"field_{field['id']}"] for field in self.template['fields']})
        self.assertEqual(self.rows('SELECT name FROM study WHERE id=2'), [('独立した研究名',)])

    def test_name_and_introduction_required_for_both_modes(self):
        for mode in ('pdf', 'template'):
            for field, label in [('name', '名前'), ('introduce', '紹介文')]:
                for value in ('', ' \t\n　', None):
                    with self.subTest(mode=mode, field=field, value=value):
                        data = {'registration_type': mode, 'template_id': str(self.template['id']),
                                'name': '研究名', 'introduce': '紹介文', 'csrf_token': 'csrf',
                                'submission_token': self.token()}
                        if value is None:
                            del data[field]
                        else:
                            data[field] = value
                        if mode == 'pdf':
                            data['pdf'] = UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf')
                        req = request('/addform', self.session)
                        req._form = FormData(data)
                        with self.assertRaises(HTTPException) as error:
                            asyncio.run(main.Add(req))
                        self.assertEqual(error.exception.status_code, 422)
                        self.assertEqual(error.exception.detail, f'{label}を入力してください。')
        self.assertEqual(self.rows('SELECT id FROM study'), [])
        self.assertEqual(list(self.uploads.iterdir()), [])

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

    def test_group_submissions_snapshot_members_for_pdf_and_template(self):
        group_id = self.create_study_group()
        pdf_response = self.submit(mode='pdf', submission_kind='group', chat_group_id=group_id,
                                   pdf=UploadFile(io.BytesIO(sample_pdf()), filename='group.pdf'))
        template_response = self.submit(mode='template', submission_kind='group', chat_group_id=group_id)
        self.assertEqual((pdf_response.status_code, template_response.status_code), (201, 201))
        self.assertEqual(self.rows('SELECT submission_kind, chat_group_name FROM study ORDER BY id'),
                         [('group', '共同研究'), ('group', '共同研究')])
        self.assertEqual(self.rows('SELECT study_id, student_id_snapshot FROM study_submission_member ORDER BY study_id, position'),
                         [(1, 'a'), (1, 'c'), (2, 'a'), (2, 'c')])
        other_session = {'user_login': True, 'user_id': 'c',
                         'user_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         'tekne_chat_session': create_chat_session(self.path, 'c')}
        self.chat.leave(other_session, group_id)
        self.assertEqual(self.rows('SELECT student_id_snapshot FROM study_submission_member WHERE study_id=1 ORDER BY position'),
                         [('a',), ('c',)])

    def test_expired_group_keeps_research_and_all_historical_members_can_view_it(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("INSERT INTO student VALUES ('d','x','school1','D','')")
            link_student_identity(db, 'd', 'school1')
            db.commit()
        group_id = self.create_study_group()
        self.chat.invite(self.session, group_id, 'd')
        former_session = {
            'user_login': True, 'user_id': 'd', 'study_csrf_token': 'csrf',
            'user_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'tekne_chat_session': create_chat_session(self.path, 'd')
        }
        self.chat.leave(former_session, group_id)
        study_file = study_file_path(self.uploads, 'retained.pdf')
        study_file.write_bytes(b'%PDF preserved research')
        with closing(connect_studies(self.path)) as db:
            db.execute("""INSERT INTO study
                (name,introduce,filename,pdfpath,userid,time,registration_type,template_id,submission_key,
                 submission_kind,chat_group_id,chat_group_name)
                VALUES ('保持する研究','説明','retained.pdf','retained.pdf','a','2024-01-01 00:00:00',
                        'pdf',NULL,'preserved-study','group',?,'共同研究')""", (group_id,))
            study_id = db.execute("SELECT id FROM study WHERE submission_key='preserved-study'").fetchone()[0]
            members = db.execute("SELECT id,login_id FROM tekne_student_identity WHERE login_id IN ('a','c')").fetchall()
            db.executemany("""INSERT INTO study_submission_member
                (study_id,tekne_student_id,student_id_snapshot,position) VALUES (?,?,?,?)""",
                [(study_id, member[0], member[1], position) for position, member in enumerate(members)])
            db.commit()
        old = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).isoformat()
        with closing(connect_studies(self.path)) as db:
            db.execute("UPDATE tekne_chat_group SET updated_at=? WHERE id=?", (old, group_id))
            db.execute("UPDATE tekne_chat_member SET left_at=? WHERE group_id=?", (old, group_id))
            db.commit()

        removed = cleanup_expired_groups(
            self.path, self.root / 'chat-files',
            now=datetime.datetime(2025, 1, 2, tzinfo=datetime.timezone.utc), force=True)
        self.assertEqual(removed, 1)
        self.assertEqual(self.rows('SELECT name,chat_group_name FROM study'), [('保持する研究', '共同研究')])
        self.assertTrue(study_file.exists())
        self.assertEqual(self.rows('SELECT student_id_snapshot FROM study_submission_member ORDER BY position'),
                         [('a',), ('c',)])
        self.assertEqual(self.rows('''SELECT i.login_id FROM study_group_access access
            JOIN tekne_student_identity i ON i.id=access.tekne_student_id
            ORDER BY i.login_id'''), [('a',), ('c',), ('d',)])

        former_html = self.render_student_studylist(former_session).body.decode()
        self.assertIn('保持する研究', former_html)
        unrelated_session = {
            'user_login': True, 'user_id': 'b',
            'user_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'tekne_chat_session': create_chat_session(self.path, 'b')
        }
        unrelated_html = self.render_student_studylist(unrelated_session).body.decode()
        self.assertNotIn('保持する研究', unrelated_html)

        pdf_endpoint = next(route.endpoint for route in main.app.routes
                            if getattr(route, 'path', '') == '/uploads/{id}.pdf' and 'GET' in route.methods)
        with patch.object(main, 'tekne_chat_service', self.chat), \
             patch.object(main, 'enabled_for_student', return_value=True):
            allowed_coroutine = pdf_endpoint(study_id, request(f'/uploads/{study_id}.pdf', former_session))
            with self.assertRaises(StopIteration) as returned:
                allowed_coroutine.send(None)
            self.assertEqual(returned.exception.value.media_type, 'application/pdf')
            with self.assertRaises(HTTPException) as denied:
                pdf_endpoint(study_id, request(f'/uploads/{study_id}.pdf', unrelated_session)).send(None)
            self.assertEqual(denied.exception.status_code, 404)

    def test_student_studylist_shows_names_for_individual_and_group_submitters(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("UPDATE student SET name='山田太郎' WHERE id='a'")
            db.execute("UPDATE student SET name='鈴木花子' WHERE id='c'")
            db.commit()
        self.submit(name='個人提出')
        group_id = self.create_study_group()
        self.submit(name='グループ提出', submission_kind='group', chat_group_id=group_id)

        html = self.render_student_studylist().body.decode()

        self.assertIn('山田太郎', html)
        self.assertIn('鈴木花子', html)
        self.assertNotIn('「共同研究」a,c', html)

    def test_student_studylist_falls_back_to_id_when_name_is_unregistered(self):
        self.submit(name='未登録の名前')

        html = self.render_student_studylist().body.decode()

        self.assertIn('<td>a</td>', html)

    def test_group_submission_rejects_missing_or_nonmember_group(self):
        with self.assertRaises(HTTPException) as missing:
            self.submit(submission_kind='group')
        self.assertEqual(missing.exception.status_code, 422)
        with self.assertRaises(HTTPException) as forged:
            self.submit(submission_kind='group', chat_group_id='not-a-member-group')
        self.assertEqual(forged.exception.status_code, 422)
        self.assertEqual(self.rows('SELECT id FROM study'), [])

    def test_group_submission_rejects_member_who_left_before_submit(self):
        group_id = self.create_study_group()
        self.chat.leave(self.session, group_id)
        with self.assertRaises(HTTPException) as error:
            self.submit(submission_kind='group', chat_group_id=group_id)
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.rows('SELECT id FROM study'), [])

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

    def test_dynamic_second_template_field_is_optional(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("INSERT INTO study_template(id,name) VALUES (2,'別テンプレート')")
            db.execute("INSERT INTO study_template_field(id,template_id,label,position,required) VALUES (20,2,'必須項目',0,1)")
            db.commit()
        self.submit(template_id='2', field_20='  ')
        with self.assertRaises(HTTPException):
            self.submit(template_id='1', field_20='他テンプレートの項目')
        self.submit(template_id='2', field_20='入力')
        self.assertEqual(self.rows('SELECT field_id,value FROM study_field_value ORDER BY study_id'), [(20, '  '), (20, '入力')])

    def test_initialization_clears_legacy_required_template_fields(self):
        with closing(connect_studies(self.path)) as db, db:
            db.execute('UPDATE study_template_field SET required=1 WHERE id=1')
            initialize_studies(db)
            self.assertEqual(db.execute('SELECT required FROM study_template_field WHERE id=1').fetchone(), (0,))
            self.assertFalse(get_templates(db)[0]['fields'][0]['required'])

    def test_pdf_save_serve_and_invalid_pdf(self):
        for filename, content in [('fake.pdf', b'not pdf'), ('broken.pdf', b'%PDF-1.4\n')]:
            with self.assertRaises(HTTPException):
                self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(content), filename=filename))
        self.submit(mode='pdf', userid='b', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='../test.pdf'))
        filename, path, user = self.rows('SELECT filename,pdfpath,userid FROM study')[0]
        self.assertEqual(filename, 'test.pdf')
        self.assertEqual(user, 'a')
        self.assertEqual((self.uploads / path).read_bytes(), sample_pdf())
        self.assertEqual(Path(asyncio.run(main.pdf(1, request('/uploads/1.pdf', self.session))).path), self.uploads / path)
        self.assertEqual(asyncio.run(main.pdf(1, request('/uploads/1.pdf', self.session))).headers['content-disposition'], 'inline; filename="study-1.pdf"')
        self.submit()
        self.assertEqual(asyncio.run(main.pdf(2, request('/uploads/2.pdf', self.session))).status_code, 200)

    def test_non_pdf_upload_is_saved_and_downloaded_as_attachment(self):
        self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(b'plain text'), filename='../research.TXT'))
        filename, path = self.rows('SELECT filename,pdfpath FROM study')[0]
        self.assertEqual(filename, 'research.TXT')
        self.assertTrue(path.endswith('.txt'))
        self.assertEqual((self.uploads / path).read_bytes(), b'plain text')
        response = asyncio.run(main.pdf(1, request('/uploads/1.pdf', self.session)))
        self.assertEqual(response.headers['content-disposition'], 'attachment; filename="research.TXT"')
        self.assertEqual(response.headers['content-type'], 'text/plain; charset=utf-8')

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
        self.assertEqual(study_file_path(self.uploads, 'keep.txt'), self.uploads / 'keep.txt')
        with self.assertRaises(ValueError):
            study_file_path(self.uploads, '../outside.txt')

    def test_teacher_deletes_pdf(self):
        self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='test.pdf'))
        session = {'teacher_login': True, 'teacher_id': 'teacher1', 'teacher_studylist_csrf_token': 'token'}
        asyncio.run(main.TeacherDeleteStudy(request('/teacher/studylist', session), 1, 'token'))
        self.assertEqual(list(self.uploads.iterdir()), [])
        self.assertEqual(self.rows('SELECT id FROM study'), [])

    def test_teacher_deletes_non_pdf_upload(self):
        self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(b'text'), filename='notes.txt'))
        session = {'teacher_login': True, 'teacher_id': 'teacher1', 'teacher_studylist_csrf_token': 'token'}
        asyncio.run(main.TeacherDeleteStudy(request('/teacher/studylist', session), 1, 'token'))
        self.assertEqual(list(self.uploads.iterdir()), [])
        self.assertEqual(self.rows('SELECT id FROM study'), [])

    def test_teacher_studylist_shows_submission_time_newest_first_with_id_tiebreak(self):
        with closing(connect_studies(self.path)) as db:
            db.executemany("""INSERT INTO study
                (id, name, introduce, filename, pdfpath, userid, time)
                VALUES (?, ?, '', '', '', ?, ?)""", [
                    (1, 'Same time lower id', 'a', '2026-10-03 10:00:00'),
                    (2, 'Older submission', 'c', '2026-10-02 10:00:00'),
                    (3, 'Same time higher id', 'a', '2026-10-03 10:00:00'),
                    (4, 'Other school submission', 'b', '2026-10-04 10:00:00'),
                ])
            db.commit()

        html = self.render_teacher_studylist().body.decode()
        self.assertIn('<th>提出日時</th>', html)
        self.assertIn('2026-10-03 10:00:00', html)
        self.assertLess(html.index('Same time higher id'), html.index('Same time lower id'))
        self.assertLess(html.index('Same time lower id'), html.index('Older submission'))
        self.assertNotIn('Other school submission', html)

    def test_teacher_studylist_empty_state_remains(self):
        html = self.render_teacher_studylist().body.decode()
        self.assertIn('確認できる提出物はありません。', html)
        self.assertIn('id="teacher-study-search"', html)

    def test_super_teacher_sees_school_feature_link_on_studylist(self):
        with closing(connect_studies(self.path)) as db:
            main.initialize_teacher_roles(db)
            db.execute("UPDATE teacher SET role = 'super_teacher' WHERE id = 'teacher1'")
            db.commit()

        html = self.render_teacher_studylist().body.decode()
        self.assertIn('href="/teacher/school-features"', html)

    def test_student_studylist_shows_submission_time_newest_first(self):
        with closing(connect_studies(self.path)) as db:
            db.executemany("""INSERT INTO study
                (id, name, introduce, filename, pdfpath, userid, time)
                VALUES (?, ?, '', '', '', ?, ?)""", [
                    (1, 'Older student submission', 'a', '2026-10-01 10:00:00'),
                    (2, 'Newest lower id', 'a', '2026-10-03 10:00:00'),
                    (3, 'Newest higher id', 'a', '2026-10-03 10:00:00'),
                    (4, 'Another student submission', 'c', '2026-10-04 10:00:00'),
                ])
            db.commit()
        main.seed_school_feature_settings(self.path)

        html = self.render_student_studylist().body.decode()
        self.assertIn('<th>提出日時</th>', html)
        self.assertIn('2026-10-03 10:00:00', html)
        self.assertLess(html.index('Newest higher id'), html.index('Newest lower id'))
        self.assertLess(html.index('Newest lower id'), html.index('Older student submission'))
        self.assertNotIn('Another student submission', html)

    def test_addform_submission_kind_and_group_selector_precede_name(self):
        main.seed_school_feature_settings(self.path)
        with patch.object(main, 'tekne_chat_service', self.chat):
            coroutine = main.AddForm(request('/addform', self.session))
            try:
                response = coroutine.send(None)
            except StopIteration as result:
                response = result.value
            else:
                self.fail('AddForm unexpectedly awaited an async operation')
        html = response.body.decode()
        self.assertLess(html.index('name="submission_kind"'), html.index('id="group-submission-panel"'))
        self.assertLess(html.index('id="group-submission-panel"'), html.index('id="name"'))

    def test_teacher_studylist_renders_searchable_submission_rows_and_no_results_message(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("""INSERT INTO study
                (name, introduce, filename, pdfpath, userid, time)
                VALUES ('Searchable title', 'Searchable summary', '', '', 'a', '2026-10-03 10:00:00')""")
            db.commit()
        html = self.render_teacher_studylist().body.decode()
        self.assertIn('data-searchable-row', html)
        self.assertIn('class="teacher-study-no-results" hidden', html)
        self.assertIn('該当する提出物はありません。', html)

    def test_real_multipart_request(self):
        fields = {'registration_type': 'template', 'template_id': str(self.template['id']),
                  'name': '研究名', 'introduce': '紹介文',
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
                self.assertIn('id="name" name="name" required', html)
                self.assertIn('id="introduce" name="introduce" required', html)
                self.assertLess(html.index('name="submission_kind"'), html.index('id="name"'))
                self.assertLess(html.index('id="group-submission-panel"'), html.index('id="name"'))
                self.assertEqual(len(response.context['study_templates'][0]['fields']), 9)
            else:
                self.assertIn('/uploads/1.pdf', html)
                self.assertIn('/uploads/2.pdf', html)
                self.assertIn('PDFを開く', html)


if __name__ == '__main__':
    unittest.main()
