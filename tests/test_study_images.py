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

    def submit_images(self, template_id, field_id, images, captions):
        entries = [('registration_type', 'template'), ('template_id', str(template_id)),
                   ('name', '画像研究'), ('introduce', '画像を含む研究'),
                   ('csrf_token', 'csrf'), ('submission_token', self.token())]
        entries.extend((f'image_{field_id}', UploadFile(io.BytesIO(content), filename=name))
                       for name, content in images)
        entries.extend((f'caption_{field_id}', caption) for caption in captions)
        req = fixtures.request('/addform', self.session)
        req._form = FormData(entries)
        return asyncio.run(main.Add(req))

    def test_upload_order_pdf_and_admin_cleanup(self):
        template_id, field_id = self.configure_image_template()
        response = self.submit_images(template_id, field_id,
            [('one.png', png_bytes(color=(255, 0, 0, 255))), ('two.png', png_bytes(color=(0, 0, 255, 255))),
             ('three.png', png_bytes(color=(0, 255, 0, 255)))],
            ['赤い画像', '青い画像', '緑の画像'])
        self.assertEqual(response.status_code, 201)
        records = self.rows('SELECT position,caption,stored_name,width,height FROM study_field_image ORDER BY position')
        self.assertEqual([row[:2] for row in records], [(0, '赤い画像'), (1, '青い画像'), (2, '緑の画像')])
        self.assertTrue(all((self.image_dir / row[2]).is_file() for row in records))
        pdf = asyncio.run(main.pdf(1, fixtures.request('/uploads/1.pdf', self.session)))
        self.assertEqual(pdf.media_type, 'application/pdf')
        text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.body)).pages)
        self.assertIn('実験写真', text)
        self.assertIn('赤い画像', text)
        asyncio.run(main.AdminDeleteStudy(fixtures.request('/admin/studies', {'admin_login': True}), 1))
        self.assertEqual(self.rows('SELECT id FROM study_field_image'), [])
        self.assertEqual(list(self.image_dir.iterdir()), [])

    def test_required_counts_captions_and_file_validation(self):
        template_id, field_id = self.configure_image_template()
        cases = [
            ([], [], '画像を1枚以上'),
            ([('a.png', png_bytes())], [], '数が一致'),
            ([('a.png', png_bytes())], [''], '画像説明'),
            ([('a.png', png_bytes())], ['あ' * 51], '50文字以内'),
            ([('bad.png', b'not an image')], ['説明'], '正常なJPEGまたはPNG'),
            ([('large.png', b'x' * (5 * 1024 * 1024 + 1))], ['説明'], '5MB以下'),
            ([('pixels.png', oversized_pixel_png())], ['説明'], '解像度が大きすぎ'),
            ([(f'{i}.png', png_bytes()) for i in range(5)], ['説明'] * 5, '最大4枚'),
        ]
        for images, captions, message in cases:
            with self.subTest(message=message), self.assertRaises(HTTPException) as error:
                self.submit_images(template_id, field_id, images, captions)
            self.assertEqual(error.exception.status_code, 422)
            self.assertIn(message, error.exception.detail)
            self.assertEqual(self.rows('SELECT id FROM study'), [])
            self.assertEqual(list(self.image_dir.iterdir()), [])

    def test_database_failure_cleans_images(self):
        template_id, field_id = self.configure_image_template()
        with closing(connect_studies(self.path)) as db:
            db.execute("""CREATE TRIGGER fail_study_image BEFORE INSERT ON study_field_image
                BEGIN SELECT RAISE(ABORT,'failure'); END""")
            db.commit()
        with self.assertLogs(level='ERROR'), self.assertRaises(HTTPException) as error:
            self.submit_images(template_id, field_id, [('one.png', png_bytes())], ['説明'])
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(self.rows('SELECT id FROM study'), [])
        self.assertEqual(list(self.image_dir.iterdir()), [])

    def test_teacher_delete_removes_image_file(self):
        template_id, field_id = self.configure_image_template()
        self.submit_images(template_id, field_id, [('one.png', png_bytes())], ['説明'])
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
