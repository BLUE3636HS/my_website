import asyncio
import io
import json
import sqlite3
import unittest
from contextlib import closing

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from pypdf import PdfReader

import main
import test_studies as fixtures
from studies import connect_studies, get_templates, initialize_studies
from study_pdf import render_study_pdf
from template_management import SchoolReportTemplate, TemplateCreate, Visibility, create_template_router, require_admin


class TemplateManagementTests(unittest.TestCase):
    setUp = fixtures.StudyTests.setUp
    rows = fixtures.StudyTests.rows
    token = fixtures.StudyTests.token
    submit = fixtures.StudyTests.submit

    def endpoint(self, path, method):
        router = create_template_router(lambda: self.path, main.templates)
        return next(route.endpoint for route in router.routes if route.path == path and method in route.methods)

    def admin_request(self, path='/admin/study-templates'):
        return fixtures.request(path, {'admin_login': True, 'admin_id': 'admin', 'study_template_csrf_token': 'token'})

    def create(self, fields=None):
        data = TemplateCreate(csrf_token='token', name='新しいテンプレート', fields=fields or [
            {'label': '先頭', 'heading_font_size': 24, 'body_font_size': 16, 'hide_heading': True,
             'max_length': 5, 'required': True}, {'label': '次の項目'}])
        return asyncio.run(self.endpoint('/admin/study-templates', 'POST')(self.admin_request(), data))

    def test_create_and_schema_defaults(self):
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.rows('SELECT seed_key,active FROM study_template WHERE id=2'), [(None, 1)])
        self.assertEqual(self.rows('SELECT label,position,heading_font_size,body_font_size,hide_heading,max_length,required FROM study_template_field WHERE template_id=2'),
                         [('先頭', 0, 24, 16, 1, 5, 0), ('次の項目', 1, 11, 10, 0, 0, 0)])
        with closing(connect_studies(self.path)) as db:
            self.assertEqual(len(get_templates(db)), 2)

    def test_school_report_settings_apply_inline_and_preserve_draft_values(self):
        with closing(connect_studies(self.path)) as db, db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(teacher)')}
            if 'role' not in columns:
                db.execute('ALTER TABLE teacher ADD COLUMN role TEXT')
            db.execute("UPDATE teacher SET role='super_teacher' WHERE id='teacher1'")
            db.execute("INSERT INTO school_report_template VALUES('school1', 1, 'before', 'teacher1')")
            fields = get_templates(db)[0]['fields']
            snapshot = {'id': 1, 'name': '研究レポート（基本）', 'fields': fields}
            db.execute('''INSERT INTO group_report_draft(group_id,school_id,school_name,template_id,template_snapshot,report_data,updated_at,revision)
                VALUES('group1','school1','school1',1,?,?, 'before',2)''',
                (json.dumps(snapshot, ensure_ascii=False), json.dumps({fields[0]['stable_key']:{'value':'下書き本文','images':[]}}, ensure_ascii=False)))
            db.execute("INSERT INTO group_report_lock VALUES('group1', ?, 'student1', '生徒', 'now')", (fields[0]['stable_key'],))
        router = create_template_router(lambda: self.path, main.templates)
        endpoint = next(route.endpoint for route in router.routes
                        if route.path == '/teacher/school-report/apply' and 'POST' in route.methods)
        data = SchoolReportTemplate(csrf_token='token', fields=[
            {'label': '更新後の項目', 'stable_key': fields[0]['stable_key']}, {'label': '新規項目'}])

        result = asyncio.run(endpoint(
            fixtures.request('/teacher/school-report/apply', {
                'teacher_login': True, 'teacher_id': 'teacher1', 'school_report_template_csrf': 'token'}), data))

        self.assertTrue(result['ok'])
        new_id = result['id']
        self.assertEqual(self.rows("SELECT template_id FROM school_report_template WHERE school='school1'"), [(new_id,)])
        self.assertEqual(self.rows("SELECT name,active FROM study_template WHERE owner_school='school1'"), [('学校成果レポート', 1)])
        self.assertEqual(self.rows('SELECT active FROM study_template WHERE id=1'), [(1,)])
        draft = self.rows('SELECT template_id,report_data,revision FROM group_report_draft WHERE group_id=\'group1\'')[0]
        self.assertEqual(draft[0], new_id)
        self.assertEqual(json.loads(draft[1])[fields[0]['stable_key']]['value'], '下書き本文')
        self.assertEqual(draft[2], 3)
        self.assertEqual(self.rows("SELECT * FROM group_report_lock WHERE group_id='group1'"), [])

    def test_school_report_apply_rejects_invalid_source_and_csrf_without_changes(self):
        with closing(connect_studies(self.path)) as db, db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(teacher)')}
            if 'role' not in columns:
                db.execute('ALTER TABLE teacher ADD COLUMN role TEXT')
            db.execute("UPDATE teacher SET role='super_teacher' WHERE id='teacher1'")
            db.execute("INSERT INTO school_report_template VALUES('school1', 1, 'before', 'teacher1')")
        endpoint = self.endpoint('/teacher/school-report/apply', 'POST')
        data = SchoolReportTemplate(csrf_token='token', name='新設定', fields=[{'label': '項目'}], source_template_id=999)
        with self.assertRaises(HTTPException) as error:
            asyncio.run(endpoint(fixtures.request('/teacher/school-report/apply', {
                'teacher_login': True, 'teacher_id': 'teacher1', 'school_report_template_csrf': 'token'}), data))
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.rows("SELECT template_id FROM school_report_template WHERE school='school1'"), [(1,)])
        self.assertEqual(self.rows("SELECT id FROM study_template WHERE owner_school='school1'"), [])
        data = SchoolReportTemplate(csrf_token='wrong', name='新設定', fields=[{'label': '項目'}])
        with self.assertRaises(HTTPException) as error:
            asyncio.run(endpoint(fixtures.request('/teacher/school-report/apply', {
                'teacher_login': True, 'teacher_id': 'teacher1', 'school_report_template_csrf': 'token'}), data))
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.rows("SELECT template_id FROM school_report_template WHERE school='school1'"), [(1,)])
        with closing(connect_studies(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_school_report_field BEFORE INSERT ON study_template_field WHEN NEW.label='失敗項目' BEGIN SELECT RAISE(ABORT,'failure'); END")
        data = SchoolReportTemplate(csrf_token='token', name='新設定', fields=[{'label': '失敗項目'}])
        with self.assertLogs(level='ERROR'), self.assertRaises(HTTPException) as error:
            asyncio.run(endpoint(fixtures.request('/teacher/school-report/apply', {
                'teacher_login': True, 'teacher_id': 'teacher1', 'school_report_template_csrf': 'token'}), data))
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(self.rows("SELECT template_id FROM school_report_template WHERE school='school1'"), [(1,)])
        self.assertEqual(self.rows("SELECT id FROM study_template WHERE owner_school='school1'"), [])

    def test_independent_alignment_and_bold_saved_and_rendered(self):
        self.create([{'label': '中央見出し', 'heading_alignment': 'center',
                      'body_alignment': 'right', 'heading_bold': True, 'body_bold': False},
                     {'label': '左見出し', 'heading_alignment': 'left',
                      'body_alignment': 'center', 'heading_bold': False, 'body_bold': True}])
        with closing(connect_studies(self.path)) as db:
            fields = get_templates(db)[1]['fields']
        self.assertEqual([(f['heading_alignment'], f['body_alignment'], f['heading_bold'], f['body_bold']) for f in fields],
                         [('center', 'right', True, False), ('left', 'center', False, True)])
        self.submit(template_id='2', **{f"field_{f['id']}": '本文' for f in fields})
        response = asyncio.run(main.pdf(1, fixtures.request('/uploads/1.pdf', self.session)))
        reader = PdfReader(io.BytesIO(response.body))
        fonts = []
        reader.pages[0].extract_text(visitor_text=lambda text, cm, tm, font, size:
                                    fonts.append(str(font.get('/BaseFont'))) if text.strip() and font else None)
        self.assertTrue(any('Bold' in font for font in fonts))
        self.assertTrue(any('Regular' in font for font in fonts))
        html = asyncio.run(self.endpoint('/admin/study-templates', 'GET')(self.admin_request())).body.decode()
        self.assertIn('中央よせ・太字', html)
        self.assertIn('右よせ・通常', html)

    def test_alignment_and_bold_validation_and_migration_defaults(self):
        for change in ({'heading_alignment': 'justify'}, {'body_alignment': ''},
                       {'heading_bold': 1}, {'body_bold': 'true'}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                TemplateCreate(csrf_token='token', name='テスト', fields=[{'label': '項目', **change}])
        self.submit(field_1='保持する本文')
        with closing(connect_studies(self.path)) as db:
            before = db.execute('SELECT * FROM study_field_value').fetchall()
            initialize_studies(db)
            initialize_studies(db)
            self.assertEqual(db.execute('SELECT * FROM study_field_value').fetchall(), before)
            field = get_templates(db)[0]['fields'][0]
        self.assertEqual((field['heading_alignment'], field['body_alignment'], field['heading_bold'], field['body_bold']),
                         ('left', 'left', False, False))

    def test_arbitrary_field_count_and_input_validation(self):
        self.create([{'label': f'項目{i}'} for i in range(20)])
        self.assertEqual(len(self.rows('SELECT id FROM study_template_field WHERE template_id=2')), 20)
        for change in ({'label': '　'}, {'heading_font_size': 5}, {'body_font_size': 37},
                       {'heading_font_size': 12.5}, {'max_length': -1}, {'max_length': True},
                       {'hide_heading': 'true'}, {'required': 1}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                TemplateCreate(csrf_token='token', name='テスト', fields=[{'label': '項目', **change}])
        for name, fields in [('　', [{'label': 'a'}]), ('test', [])]:
            with self.assertRaises(ValidationError):
                TemplateCreate(csrf_token='token', name=name, fields=fields)

    def test_image_field_settings_and_validation(self):
        response = self.create([{'label': '実験写真', 'field_type': 'image', 'max_length': 50,
                                 'image_size': 'large', 'image_alignment': 'right',
                                 'body_font_size': 9, 'body_alignment': 'center', 'required': True}])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.rows('''SELECT field_type,image_size,image_alignment,max_length,body_font_size,
            body_alignment,required FROM study_template_field WHERE template_id=2'''),
            [('image', 'large', 'right', 50, 9, 'center', 0)])
        for change in ({'field_type': 'video'}, {'image_size': 'medium'},
                       {'image_alignment': 'justify'}, {'field_type': 'image', 'max_length': 49}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                TemplateCreate(csrf_token='token', name='画像', fields=[{'label': '画像', **change}])

    def test_admin_and_csrf(self):
        for session in ({}, {'teacher_login': True}, {'user_login': True}):
            with self.assertRaises(HTTPException) as error:
                require_admin(fixtures.request('/admin/study-templates', session))
            self.assertEqual(error.exception.status_code, 403)
        data = TemplateCreate(csrf_token='不正', name='テスト', fields=[{'label': '項目'}])
        with self.assertRaises(HTTPException) as error:
            asyncio.run(self.endpoint('/admin/study-templates', 'POST')(self.admin_request(), data))
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException):
            asyncio.run(self.endpoint('/admin/study-templates/{template_id}/visibility', 'POST')(
                self.admin_request(), 1, Visibility(csrf_token='wrong', active=False)))

    def test_failed_field_insert_rolls_back_template(self):
        with closing(connect_studies(self.path)) as db:
            db.execute("CREATE TRIGGER fail_field BEFORE INSERT ON study_template_field WHEN NEW.label='次の項目' BEGIN SELECT RAISE(ABORT,'failure'); END")
            db.commit()
        with self.assertLogs(level='ERROR'), self.assertRaises(HTTPException):
            self.create()
        self.assertEqual(self.rows('SELECT id FROM study_template'), [(1,)])
        self.assertEqual(len(self.rows('SELECT id FROM study_template_field')), 9)

    def test_hide_disallows_new_submission_but_preserves_pdf(self):
        self.submit(field_1='既存の本文')
        toggle = self.endpoint('/admin/study-templates/{template_id}/visibility', 'POST')
        asyncio.run(toggle(self.admin_request(), 1, Visibility(csrf_token='token', active=False)))
        with closing(connect_studies(self.path)) as db:
            self.assertEqual(get_templates(db), [])
            self.assertEqual(len(get_templates(db, include_inactive=True)), 1)
        with self.assertRaises(HTTPException):
            self.submit()
        response = asyncio.run(main.pdf(1, fixtures.request('/uploads/1.pdf', self.session)))
        self.assertIn('既存の本文', PdfReader(io.BytesIO(response.body)).pages[0].extract_text())
        asyncio.run(toggle(self.admin_request(), 1, Visibility(csrf_token='token', active=True)))
        self.assertEqual(self.submit().status_code, 201)

    def test_character_limits_codepoints_and_lf_normalization(self):
        self.create()
        field_id = self.rows('SELECT id FROM study_template_field WHERE template_id=2 ORDER BY position')[0][0]
        key = f'field_{field_id}'
        self.assertEqual(self.submit(template_id='2', **{key: 'あ😀\r\n b'}).status_code, 201)
        self.assertEqual(self.rows('SELECT value FROM study_field_value WHERE field_id=?', (field_id,)), [('あ😀\n b',)])
        for value in ('あ😀\r\n bc', '', '  '):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                self.submit(template_id='2', **{key: value})

    def test_pages_render_and_escape_definition(self):
        self.create([{'label': '<script>test</script>'}])
        for path in ('/admin/study-templates', '/admin/study-templates/new'):
            response = asyncio.run(self.endpoint(path, 'GET')(self.admin_request(path)))
            self.assertEqual(response.status_code, 200)
            html = response.body.decode()
            self.assertIn('テンプレート管理', html)
            self.assertIn('aria-current="page"', html)
            if path.endswith('new'):
                self.assertIn('PDF簡易プレビュー', html)
            else:
                self.assertIn('&lt;script&gt;', html)

    def test_migration_preserves_existing_definitions_and_values(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('''CREATE TABLE study_template(id INTEGER PRIMARY KEY,seed_key TEXT UNIQUE,name TEXT NOT NULL,active INTEGER DEFAULT 1);
                CREATE TABLE study_template_field(id INTEGER PRIMARY KEY,template_id INTEGER,label TEXT,position INTEGER,required INTEGER);
                INSERT INTO study_template VALUES(1,'basic-research-v1','元の名前',1);
                INSERT INTO study_template_field VALUES(1,1,'元の項目',0,1);''')
            initialize_studies(db)
            initialize_studies(db)
            self.assertEqual(db.execute('SELECT label,required,heading_font_size,body_font_size,hide_heading,max_length FROM study_template_field').fetchall(),
                             [('元の項目', 0, 11, 10, 0, 0)])
            self.assertEqual(db.execute('SELECT name FROM study_template').fetchall(), [('元の名前',)])
            self.assertEqual(db.execute('SELECT heading_alignment,body_alignment,heading_bold,body_bold FROM study_template_field').fetchall(),
                             [('left', 'left', 0, 0)])

    def test_pdf_field_styles_and_hidden_heading(self):
        reader = PdfReader(io.BytesIO(render_study_pdf([
            ('表示する見出し', '本文を表示', 24, 18, False),
            ('隠す見出し', '本文は隠さない', 36, 8, True)], 'student-1')))
        text = reader.pages[0].extract_text()
        self.assertIn('表示する見出し', text)
        self.assertNotIn('隠す見出し', text)
        self.assertIn('本文は隠さない', text)
        self.assertIn('student-1', text)
        sizes = set()
        reader.pages[0].extract_text(visitor_text=lambda text, cm, tm, font, size: sizes.add(size) if text.strip() else None)
        self.assertTrue({24, 18, 8}.issubset(sizes))

    def test_http_json_api_checks_auth_validation_and_csrf(self):
        app = FastAPI()
        app.include_router(create_template_router(lambda: self.path, main.templates))

        async def send(session, payload):
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
            async def output(message):
                messages.append(message)
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'method': 'POST',
                     'path': '/admin/study-templates', 'raw_path': b'/admin/study-templates',
                     'root_path': '', 'scheme': 'http', 'server': ('test', 80), 'client': ('test', 1),
                     'headers': [(b'content-type', b'application/json')], 'query_string': b'', 'session': session}
            await app(scope, receive, output)
            return next(message['status'] for message in messages if message['type'] == 'http.response.start')

        session = {'admin_login': True, 'study_template_csrf_token': 'token'}
        payload = {'csrf_token': 'token', 'name': 'API作成', 'fields': [{'label': '項目'}]}
        self.assertEqual(asyncio.run(send({}, payload)), 403)
        self.assertEqual(asyncio.run(send(session, {**payload, 'csrf_token': 'wrong'})), 403)
        self.assertEqual(asyncio.run(send(session, {**payload, 'fields': []})), 422)
        self.assertEqual(asyncio.run(send(session, payload)), 201)

    def test_long_body_uses_remaining_page_space(self):
        reader = PdfReader(io.BytesIO(render_study_pdf([
            ('テーマ', '短い導入文', 24, 16, False),
            ('実験結果', '記録した本文を表示します。\n' * 150, 13, 11, False)], 'student-1')))
        self.assertGreater(len(reader.pages), 1)
        first_page = reader.pages[0].extract_text()
        self.assertIn('実験結果', first_page)
        self.assertIn('記録した本文を表示します。', first_page)


if __name__ == '__main__':
    unittest.main()
