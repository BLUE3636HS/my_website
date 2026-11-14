"""項目の内容「文字と画像（文字の下に画像）」のテスト。"""
import asyncio
import io
import json
import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfReader
from starlette.datastructures import FormData, UploadFile

import main
import test_studies as fixtures
from group_reports import pdf_sections
from school_features import initialize_school_feature_settings
from studies import connect_studies, get_templates, initialize_studies
from study_pdf import render_study_pdf
from template_management import SchoolReportTemplate, TemplateCreate, create_template_router
from test_study_images import png_bytes


def field(label, field_type, **extra):
    return {'label': label, 'field_type': field_type, 'max_length': 50 if field_type == 'image' else 0, **extra}


class TemplateStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize_studies(self.db)

    def tearDown(self):
        self.db.close()

    def test_both_is_stored_as_text_with_images_and_read_back(self):
        template_id = self.db.execute("INSERT INTO study_template(name,active) VALUES('t',1)").lastrowid
        self.db.execute("""INSERT INTO study_template_field(template_id,label,position,required,field_type,with_images,stable_key)
            VALUES(?,'考察',0,0,'text',1,'k1'),(?,'本文',1,0,'text',0,'k2'),(?,'写真',2,0,'image',0,'k3')""", (template_id,) * 3)
        fields = next(t for t in get_templates(self.db) if t['id'] == template_id)['fields']
        self.assertEqual([f['field_type'] for f in fields], ['both', 'text', 'image'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO study_template_field(template_id,label,position,required,with_images) VALUES(?,'x',3,0,2)", (template_id,))

    def test_group_pdf_text_then_images_and_empty_rules(self):
        template = {'fields': [
            {'stable_key': 'a', 'label': '両方（文字なし・画像なし）', 'field_type': 'both'},
            {'stable_key': 'b', 'label': '画像だけ（画像なし）', 'field_type': 'image'},
            {'stable_key': 'c', 'label': '両方（文字あり）', 'field_type': 'both', 'hide_heading': True}]}
        values = {'c': {'value': '本文', 'images': []}}
        sections = pdf_sections(self.db, '.', template, values, '')
        self.assertEqual([s[:2] for s in sections], [('両方（文字なし・画像なし）', '(未入力)'), ('画像だけ（画像なし）', '(未入力)'), ('両方（文字あり）', '本文')])
        self.assertTrue(sections[2][4])  # 見出しを隠す設定が文字側で効く

    def test_group_pdf_images_only_has_no_placeholder_and_both_hides_image_heading(self):
        with patch('group_reports.Path.is_file', return_value=True):
            self.db.execute("INSERT INTO group_report_asset VALUES('h','x.png','image/png',1,'now')")
            template = {'fields': [
                {'stable_key': 'a', 'label': '画像だけ', 'field_type': 'image'},
                {'stable_key': 'b', 'label': '文字と画像', 'field_type': 'both'}]}
            image = {'content_hash': 'h', 'caption': '説明'}
            sections = pdf_sections(self.db, 'imgs', template, {'a': {'value': '', 'images': [image]}, 'b': {'value': '', 'images': [image]}}, '')
        self.assertEqual(sections[0]['label'], '画像だけ')
        self.assertFalse(sections[0]['hide_heading'])
        self.assertEqual(sections[1][:2], ('文字と画像', '(未入力)'))
        self.assertTrue(sections[2]['hide_heading'])
        self.assertEqual(len(sections), 3)


class TemplateApiTests(unittest.TestCase):
    setUp = fixtures.StudyTests.setUp

    def test_admin_and_school_templates_save_both(self):
        router = create_template_router(lambda: self.path, SimpleNamespace(TemplateResponse=lambda **kwargs: kwargs))
        create = next(r.endpoint for r in router.routes if r.path == '/admin/study-templates' and 'POST' in r.methods)
        data = TemplateCreate(csrf_token='csrf', name='新', fields=[field('考察', 'both', max_length=200), field('写真', 'image')])
        response = asyncio.run(create(fixtures.request('/admin/study-templates', {'admin_login': True, 'study_template_csrf_token': 'csrf'}), data))
        self.assertEqual(response.status_code, 201)
        with closing(connect_studies(self.path)) as db:
            rows = db.execute('SELECT label,field_type,with_images,max_length FROM study_template_field WHERE template_id=? ORDER BY position',
                              (json.loads(response.body)['id'],)).fetchall()
        self.assertEqual(rows, [('考察', 'text', 1, 200), ('写真', 'image', 0, 50)])
        self.assertEqual(SchoolReportTemplate(csrf_token='x', fields=[field('a', 'both')]).fields[0].field_type, 'both')


class LegacyIndividualSubmissionTests(unittest.TestCase):
    """テンプレート提出は廃止。過去に提出された研究のPDF表示を確認する。"""
    setUp = fixtures.StudyTests.setUp
    rows = fixtures.StudyTests.rows

    def insert_study(self, template_id, value, field_id, images=()):
        with closing(connect_studies(self.path)) as db, db:
            study_id = db.execute("""INSERT INTO study
                (name, introduce, filename, pdfpath, userid, time, registration_type, template_id)
                VALUES ('研究', '紹介', '', '', 'a', '2026-10-01 10:00:00', 'template', ?)""", (template_id,)).lastrowid
            db.execute('INSERT INTO study_field_value(study_id, field_id, value) VALUES (?, ?, ?)', (study_id, field_id, value))
            for position, (stored_name, caption) in enumerate(images):
                db.execute("""INSERT INTO study_field_image
                    (study_id,field_id,position,stored_name,original_name,caption,image_format,width,height)
                    VALUES (?,?,?,?,?,?,'PNG',80,60)""", (study_id, field_id, position, stored_name, stored_name, caption))
        return study_id

    def test_text_and_images_are_printed_under_the_text(self):
        with closing(connect_studies(self.path)) as db, db:
            initialize_school_feature_settings(db)
            template_id = db.execute("INSERT INTO study_template(name,active) VALUES ('文字と画像',1)").lastrowid
            field_id = db.execute("""INSERT INTO study_template_field(template_id,label,position,required,field_type,with_images,max_length)
                VALUES (?,'観察',0,0,'text',1,100)""", (template_id,)).lastrowid
        image_dir = self.root / 'study-images'; image_dir.mkdir()
        (image_dir / 'a.png').write_bytes(png_bytes())
        with patch.object(main, 'STUDY_IMAGE_UPLOADS_DIR', image_dir):
            self.insert_study(template_id, '観察した文章', field_id, [('a.png', '実験の様子')])
            pdf = asyncio.run(main.pdf(1, fixtures.request('/uploads/1.pdf', self.session)))
        text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.body)).pages)
        self.assertEqual(text.count('観察'), 2)  # 見出しは1回（「観察」＋本文「観察した文章」）
        self.assertLess(text.index('観察した文章'), text.index('実験の様子'))

    def test_empty_text_in_both_prints_placeholder(self):
        with closing(connect_studies(self.path)) as db, db:
            initialize_school_feature_settings(db)
            template_id = db.execute("INSERT INTO study_template(name,active) VALUES ('文字と画像',1)").lastrowid
            field_id = db.execute("""INSERT INTO study_template_field(template_id,label,position,required,field_type,with_images)
                VALUES (?,'観察',0,0,'text',1)""", (template_id,)).lastrowid
        self.insert_study(template_id, '', field_id)
        pdf = asyncio.run(main.pdf(1, fixtures.request('/uploads/1.pdf', self.session)))
        text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.body)).pages)
        self.assertIn('(未入力)', text)


if __name__ == '__main__':
    unittest.main()
