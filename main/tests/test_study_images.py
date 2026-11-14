import asyncio
import io
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
from starlette.datastructures import FormData, UploadFile

import main
import test_studies as fixtures
from studies import connect_studies, get_templates
from study_images import normalize_study_image


def png_bytes(size=(80, 60), color=(30, 120, 200, 180)):
    output = io.BytesIO()
    Image.new('RGBA', size, color).save(output, 'PNG')
    return output.getvalue()


def oversized_pixel_png():
    output = io.BytesIO()
    Image.new('1', (5001, 5000)).save(output, 'PNG')
    return output.getvalue()


class StudyImageTests(unittest.TestCase):
    setUp = fixtures.StudyTests.setUp
    rows = fixtures.StudyTests.rows
    token = fixtures.StudyTests.token

    def configure_image_template(self, required=1, image_size='small', alignment='right'):
        with closing(connect_studies(self.path)) as db, db:
            template_id = db.execute("INSERT INTO study_template(name,active) VALUES ('画像テンプレート',1)").lastrowid
            field_id = db.execute("""INSERT INTO study_template_field
                (template_id,label,position,required,field_type,image_size,image_alignment,max_length)
                VALUES (?,?,?,?,?,?,?,50)""",
                (template_id, '実験写真', 0, required, 'image', image_size, alignment)).lastrowid
        self.image_dir = self.root / 'study-images'
        self.image_dir.mkdir()
        patcher = patch.object(main, 'STUDY_IMAGE_UPLOADS_DIR', self.image_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        return template_id, field_id

    def insert_image_study(self, template_id, field_id, images):
        """テンプレート提出は廃止したので、過去に提出された画像付きの研究を直接DBに作る。"""
        with closing(connect_studies(self.path)) as db, db:
            study_id = db.execute("""INSERT INTO study
                (name, introduce, filename, pdfpath, userid, time, registration_type, template_id)
                VALUES ('画像研究', '画像を含む研究', '', '', 'a', '2026-10-01 10:00:00', 'template', ?)""",
                (template_id,)).lastrowid
            for position, (caption, content) in enumerate(images):
                stored_name = f'{study_id}-{position}.png'
                (self.image_dir / stored_name).write_bytes(content)
                db.execute("""INSERT INTO study_field_image
                    (study_id,field_id,position,stored_name,original_name,caption,image_format,width,height)
                    VALUES (?,?,?,?,?,?,'PNG',80,60)""",
                    (study_id, field_id, position, stored_name, f'{position}.png', caption))
        return study_id

    def test_legacy_image_study_pdf_order_and_admin_cleanup(self):
        template_id, field_id = self.configure_image_template()
        self.insert_image_study(template_id, field_id, [
            ('赤い画像', png_bytes(color=(255, 0, 0, 255))), ('青い画像', png_bytes(color=(0, 0, 255, 255))),
            ('緑の画像', png_bytes(color=(0, 255, 0, 255)))])
        pdf = asyncio.run(main.pdf(1, fixtures.request('/uploads/1.pdf', self.session)))
        self.assertEqual(pdf.media_type, 'application/pdf')
        text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.body)).pages)
        self.assertIn('実験写真', text)
        self.assertLess(text.index('赤い画像'), text.index('青い画像'))
        self.assertLess(text.index('青い画像'), text.index('緑の画像'))
        asyncio.run(main.AdminDeleteStudy(fixtures.request('/admin/studies', {'admin_login': True}), 1))
        self.assertEqual(self.rows('SELECT id FROM study_field_image'), [])
        self.assertEqual(list(self.image_dir.iterdir()), [])

    def test_template_image_upload_is_rejected_without_saving_files(self):
        template_id, field_id = self.configure_image_template()
        entries = [('registration_type', 'template'), ('template_id', str(template_id)),
                   ('name', '画像研究'), ('introduce', '画像を含む研究'),
                   ('csrf_token', 'csrf'), ('submission_token', self.token()),
                   (f'image_{field_id}', UploadFile(io.BytesIO(png_bytes()), filename='one.png')),
                   (f'caption_{field_id}', '説明')]
        req = fixtures.request('/addform', self.session)
        req._form = FormData(entries)
        with self.assertRaises(HTTPException) as error:
            asyncio.run(main.Add(req))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.rows('SELECT id FROM study'), [])
        self.assertEqual(list(self.image_dir.iterdir()), [])

    def test_image_normalization_rejects_invalid_images(self):
        # グループレポートの画像アップロードで引き続き使う検証処理
        cases = [
            ('bad.png', b'not an image', '正常なJPEGまたはPNG'),
            ('large.png', b'x' * (5 * 1024 * 1024 + 1), '5MB以下'),
            ('pixels.png', oversized_pixel_png(), '解像度が大きすぎ'),
        ]
        for name, content, message in cases:
            with self.subTest(message=message), self.assertRaises(ValueError) as error:
                normalize_study_image(UploadFile(io.BytesIO(content), filename=name))
            self.assertIn(message, str(error.exception))

    def test_teacher_delete_removes_image_file(self):
        template_id, field_id = self.configure_image_template()
        self.insert_image_study(template_id, field_id, [('説明', png_bytes())])
        session = {'teacher_login': True, 'teacher_id': 'teacher1',
                   'teacher_studylist_csrf_token': 'token'}
        asyncio.run(main.TeacherDeleteStudy(fixtures.request('/teacher/studylist', session), 1, 'token'))
        self.assertEqual(self.rows('SELECT id FROM study_field_image'), [])
        self.assertEqual(list(self.image_dir.iterdir()), [])

    def test_migration_and_template_payload_include_image_metadata(self):
        template_id, field_id = self.configure_image_template(required=0, image_size='large', alignment='left')
        with closing(connect_studies(self.path)) as db:
            template = next(item for item in get_templates(db) if item['id'] == template_id)
            db.execute('PRAGMA foreign_keys=ON')
            columns = {row[1] for row in db.execute('PRAGMA table_info(study_field_image)')}
        self.assertTrue({'stored_name', 'caption', 'position', 'width', 'height'}.issubset(columns))
        self.assertEqual(template['fields'][0]['field_type'], 'image')
        self.assertEqual(template['fields'][0]['image_size'], 'large')
        self.assertEqual(template['fields'][0]['image_alignment'], 'left')


if __name__ == '__main__':
    unittest.main()
