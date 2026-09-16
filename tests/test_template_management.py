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
from template_management import TemplateCreate, Visibility, create_template_router, require_admin


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
                         [('先頭', 0, 24, 16, 1, 5, 1), ('次の項目', 1, 13, 11, 0, 0, 0)])
        with closing(connect_studies(self.path)) as db:
            self.assertEqual(len(get_templates(db)), 2)

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
                             [('元の項目', 1, 13, 11, 0, 0)])
            self.assertEqual(db.execute('SELECT name FROM study_template').fetchall(), [('元の名前',)])

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
