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
    insert_template_study = fixtures.StudyTests.insert_template_study

    def get(self, study_id, session):
        return asyncio.run(main.pdf(study_id, request(f'/uploads/{study_id}.pdf', session)))




