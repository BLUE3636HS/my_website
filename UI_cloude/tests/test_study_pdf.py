import asyncio
import datetime
import io
import unittest
from contextlib import closing
from unittest.mock import patch

from fastapi import HTTPException
from pypdf import PdfReader

import main
from studies import connect_studies
from study_pdf import render_study_pdf
import test_studies as fixtures
from test_studies import request, sample_pdf, UploadFile


class RenderTests(unittest.TestCase):
    def test_japanese_special_characters_long_text_and_embedded_font(self):
        long_text = '日本語の長い文章を折り返して表示します。' * 500
        reader = PdfReader(io.BytesIO(render_study_pdf([
            ('テーマ', '植物の成長\n温度と光 <b>本文</b> & 比較'),
            ('空欄は非表示', ' \n　'), ('結果', long_text), ('英数字', 'ABC123' * 300)])))
        self.assertGreater(len(reader.pages), 1)
        text = ''.join(page.extract_text() for page in reader.pages)
        self.assertIn('植物の成長', text)
        self.assertIn('<b>本文</b> & 比較', text)
        self.assertNotIn('空欄は非表示', text)
        self.assertIn(long_text, text.replace('\n', ''))
        self.assertIn('ABC123' * 300, text.replace('\n', ''))
        fonts = reader.pages[0]['/Resources']['/Font'].get_object()
        self.assertTrue(any('/FontFile2' in font.get_object().get('/FontDescriptor', {}) for font in fonts.values()))

    def test_empty_is_one_blank_a4_page(self):
        reader = PdfReader(io.BytesIO(render_study_pdf([('未入力', '')])))
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(reader.pages[0].extract_text(), '')
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.276, places=2)


class PdfRouteTests(unittest.TestCase):
    # Reuse isolated DB fixture without inheriting and rerunning its tests.
    setUp = fixtures.StudyTests.setUp
    rows = fixtures.StudyTests.rows
    token = fixtures.StudyTests.token
    submit = fixtures.StudyTests.submit

    def get(self, study_id, session):
        return asyncio.run(main.pdf(study_id, request(f'/uploads/{study_id}.pdf', session)))

    def test_all_roles_and_unauthenticated_or_expired(self):
        self.submit()
        self.submit(mode='pdf', pdf=UploadFile(io.BytesIO(sample_pdf()), filename='sample.pdf'))
        for study_id in (1, 2):
            for role in ('user', 'teacher', 'admin'):
                session = {f'{role}_login': True, f'{role}_id': 'someone',
                           f'{role}_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                response = self.get(study_id, session)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'private, no-store')
                self.assertIn('inline', response.headers['content-disposition'])
                if study_id == 1:
                    text = PdfReader(io.BytesIO(response.body)).pages[0].extract_text()
                    self.assertIn('ユーザーID：a', text)
                    self.assertNotIn('someone', text)
                session[f'{role}_time'] = (datetime.datetime.now() - datetime.timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
                response = self.get(study_id, session)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers['location'], '/login')
            self.assertEqual(self.get(study_id, {}).status_code, 303)
        with self.assertRaises(HTTPException) as error:
            self.get(999, self.session)
        self.assertEqual(error.exception.status_code, 404)

    def test_current_order_content_only_no_writes_and_render_failure(self):
        self.submit(name='非掲載の名前', introduce='非掲載の紹介文', field_1='テーマ本文', field_2='背景本文')
        before = self.path.read_bytes()
        response = self.get(1, self.session)
        text = PdfReader(io.BytesIO(response.body)).pages[0].extract_text()
        self.assertIn('テーマ本文', text)
        self.assertNotIn('非掲載', text)
        self.assertLess(text.index('テーマ'), text.index('背景'))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.uploads.iterdir()), [])
        with closing(connect_studies(self.path)) as db:
            db.execute("UPDATE study_template_field SET label='変更後の背景', position=-1 WHERE id=2")
            db.commit()
        text = PdfReader(io.BytesIO(self.get(1, self.session).body)).pages[0].extract_text()
        self.assertLess(text.index('変更後の背景'), text.index('テーマ'))
        before = self.path.read_bytes()
        with patch.object(main, 'render_study_pdf', side_effect=RuntimeError('test')):
            with self.assertLogs(level='ERROR'), self.assertRaises(HTTPException) as error:
                self.get(1, self.session)
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(before, self.path.read_bytes())

    def test_threadpool_is_used(self):
        self.submit()
        from unittest.mock import AsyncMock
        with patch.object(main, 'run_in_threadpool', new_callable=AsyncMock, return_value=b'%PDF-test') as run:
            self.get(1, self.session)
            run.assert_awaited_once()

    def test_middleware_allows_pdf_for_roles_and_expires_sessions(self):
        self.submit()
        middleware = main.LoginCheckMiddleware(main.app)
        async def next_response(req):
            return await main.pdf(1, req)
        for role in ('user', 'teacher', 'admin'):
            for days, expected in ((0, 200), (2, 303)):
                session = {f'{role}_login': True, f'{role}_id': 'person',
                           f'{role}_time': (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')}
                response = asyncio.run(middleware.dispatch(request('/uploads/1.pdf', session), next_response))
                self.assertEqual(response.status_code, expected)
