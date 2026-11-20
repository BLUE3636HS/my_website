"""生徒の「PDFを提出」と先生の添付ファイルのAPI・画面を、偽のリクエストで確かめるテスト。"""
import asyncio
import datetime
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader
from starlette.datastructures import UploadFile

from report_reviews import (
    acquire_review_lock, add_review_attachment, build_report_review_router, return_review, student_submission_context, submit_pdf,
)
from test_report_reviews import Base, TEMPLATE, TEMPLATES_DIR, at

DAY = '2026-11-20'
VALID_PDF = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\nstartxref\n9\n%%EOF'
JST = datetime.timezone(datetime.timedelta(hours=9))


class FakeForm:
    def __init__(self, fields):
        self.fields = fields

    def get(self, key, default=None):
        value = self.fields.get(key, default)
        return value[0] if isinstance(value, list) else value

    def getlist(self, key):
        value = self.fields.get(key)
        return [] if value is None else value if isinstance(value, list) else [value]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeRequest(SimpleNamespace):
    async def json(self):
        return self.body

    def form(self, **kwargs):
        return FakeForm(self.fields)


class RouterPdfTests(Base):
    def setUp(self):
        super().setUp()
        self.files = tempfile.TemporaryDirectory()
        self.add_round(DAY)
        self.set_format(DAY, 'pdf')
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        chat = SimpleNamespace(groups=lambda session: [{'id': 'g1'}] if session.get('user_id') == 'u1' else [{'id': 'g2'}])
        router = build_report_review_router(
            self.path, SimpleNamespace(TemplateResponse=lambda request, name, context: {'html': env.get_template(name).render(**context)}),
            chat, self.image_dir.name, self.files.name)
        self.routes = {(r.path, tuple(sorted(r.methods))[0]): r.endpoint for r in router.routes}
        self.pdf_dir = Path(self.files.name) / 'report-submissions'
        self.attach_dir = Path(self.files.name) / 'review-attachments'

    def tearDown(self):
        self.files.cleanup()
        super().tearDown()

    def set_format(self, day, fmt):
        with self.db() as db, db:
            db.execute('UPDATE group_report_submission_day SET submit_format=? WHERE day=?', (fmt, day))

    def student(self, fields=None, user='u1'):
        return FakeRequest(session={'user_login': True, 'user_id': user, 'tekne_chat_csrf': 'tok'}, fields=fields or {})

    def teacher(self, fields=None, body=None, teacher='t1'):
        return FakeRequest(session={'teacher_login': True, 'teacher_id': teacher, 'report_review_csrf': 'tt'}, fields=fields or {},
                           body=body or {}, url=SimpleNamespace(path='/teacher/x'))

    def route(self, path, method='GET'):
        return self.routes[(path, method)]

    def future_round(self, title='期末レポート', fmt='pdf'):
        future = (datetime.datetime.now(JST).date() + datetime.timedelta(days=3)).isoformat()
        self.add_round(future, title)
        self.set_format(future, fmt)
        return future

    def submit_form(self, data=VALID_PDF, name='提出.pdf', csrf='tok', day=DAY, attempt='0', user='u1', group='g1'):
        fields = {'csrf_token': csrf, 'round_day': day, 'attempt': attempt, 'pdf': UploadFile(file=io.BytesIO(data), filename=name)}
        return asyncio.run(self.route('/api/group-reports/{group_id}/submit-pdf', 'POST')(self.student(fields, user), group))

    def stored_pdfs(self):
        return list(self.pdf_dir.iterdir()) if self.pdf_dir.exists() else []

    def pdf(self, group_id, day, name='提出.pdf'):
        with self.db() as db, db:
            return submit_pdf(db, '学校A', group_id, 'u1', day, 0, f'{group_id}-0.pdf', name, at('2026-11-18'))

    def prepare_submitted(self):
        self.pdf('g1', DAY)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        (self.pdf_dir / 'g1-0.pdf').write_bytes(VALID_PDF)
        return self.sub_id('g1', DAY)

    def lock(self, sub_id, teacher='t1'):
        with self.db() as db, db:
            acquire_review_lock(db, sub_id, '学校A', teacher)

    def attach(self, sub_id, name='解説.pdf', csrf='tt'):
        add = self.route('/teacher/api/report-reviews/{submission_id}/attachments', 'POST')
        upload = UploadFile(file=io.BytesIO(VALID_PDF), filename=name)
        return asyncio.run(add(self.teacher({'csrf_token': csrf, 'file': upload}), sub_id))

    # ---- 生徒の提出
    def test_student_targets_and_submission(self):
        future = self.future_round()
        with self.db() as db, db:
            db.execute('DELETE FROM group_report_submission_day WHERE day=?', (DAY,))
        targets_route = self.route('/api/group-reports/{group_id}/pdf-targets')
        targets = asyncio.run(targets_route(self.student(), 'g1'))['targets']
        self.assertEqual([t['round_day'] for t in targets], [future])
        self.assertTrue(self.submit_form(day=future)['ok'])
        self.assertEqual(len(self.stored_pdfs()), 1)
        self.assertEqual(asyncio.run(targets_route(self.student(), 'g1'))['targets'], [])
        with self.assertRaises(HTTPException) as again:
            self.submit_form(day=future)
        self.assertEqual(again.exception.status_code, 409)
        self.assertEqual(len(self.stored_pdfs()), 1)  # 出し直しに失敗したファイルは残らない
        with self.assertRaises(HTTPException) as other:
            self.submit_form(day=future, group='g1', user='u3')
        self.assertEqual(other.exception.status_code, 403)

    def test_submission_rejects_bad_input_and_leaves_no_files(self):
        future = self.future_round()
        cases = [({'csrf': 'bad'}, 403), ({'data': b'not a pdf', 'name': 'a.pdf'}, 422), ({'data': VALID_PDF, 'name': 'a.png'}, 422),
                 ({'attempt': 'x'}, 422), ({'day': '2099-01-01'}, 409)]
        for kwargs, status in cases:
            with self.assertRaises(HTTPException) as caught:
                self.submit_form(**{'day': future, **kwargs})
            self.assertEqual(caught.exception.status_code, status, kwargs)
        self.assertEqual(self.stored_pdfs(), [])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM group_report_submission').fetchone()[0], 0)

    def test_template_round_refuses_pdf(self):
        future = self.future_round(fmt='template')
        with self.assertRaises(HTTPException) as caught:
            self.submit_form(day=future)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.stored_pdfs(), [])

    # ---- ダウンロード
    def test_pdf_downloads_check_ownership(self):
        sub_id = self.prepare_submitted()
        student = self.route('/api/group-reports/{group_id}/submissions/{submission_id}/submitted.pdf')
        response = asyncio.run(student(self.student(), 'g1', sub_id))
        self.assertEqual(response.media_type, 'application/pdf')
        self.assertEqual(Path(response.path).read_bytes(), VALID_PDF)
        self.assertIn('inline', response.headers['content-disposition'])
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        with self.assertRaises(HTTPException) as not_member:
            asyncio.run(student(self.student(user='u3'), 'g1', sub_id))
        self.assertEqual(not_member.exception.status_code, 403)
        with self.assertRaises(HTTPException) as wrong_group:
            asyncio.run(student(self.student(user='u3'), 'g2', sub_id))
        self.assertEqual(wrong_group.exception.status_code, 404)
        teacher = self.route('/teacher/report-reviews/submissions/{submission_id}/submitted.pdf')
        self.assertEqual(Path(asyncio.run(teacher(self.teacher(), sub_id)).path).read_bytes(), VALID_PDF)
        with self.assertRaises(HTTPException) as other_school:
            asyncio.run(teacher(self.teacher(teacher='tb'), sub_id))
        self.assertEqual(other_school.exception.status_code, 404)

    # ---- 添付ファイル
    def test_attachment_flow_and_student_download_after_return(self):
        sub_id = self.prepare_submitted()
        self.lock(sub_id)
        with self.assertRaises(HTTPException) as bad_csrf:
            self.attach(sub_id, csrf='bad')
        self.assertEqual(bad_csrf.exception.status_code, 403)
        attachment_id = self.attach(sub_id)['attachment']['id']
        teacher_download = self.route('/teacher/report-reviews/attachments/{attachment_id}')
        self.assertEqual(Path(asyncio.run(teacher_download(self.teacher(), attachment_id)).path).read_bytes(), VALID_PDF)
        with self.assertRaises(HTTPException):
            asyncio.run(teacher_download(self.teacher(teacher='tb'), attachment_id))
        student_download = self.route('/api/group-reports/{group_id}/review-attachments/{attachment_id}')
        with self.assertRaises(HTTPException) as hidden:
            asyncio.run(student_download(self.student(), 'g1', attachment_id))
        self.assertEqual(hidden.exception.status_code, 404)  # 返却前は生徒に見えない
        with self.db() as db, db:
            return_review(db, sub_id, '学校A', 't1', 'OK', {}, 'returned', None, at('2026-11-19'))
        response = asyncio.run(student_download(self.student(), 'g1', attachment_id))
        self.assertIn('attachment', response.headers['content-disposition'])
        with self.assertRaises(HTTPException):
            asyncio.run(student_download(self.student(user='u3'), 'g2', attachment_id))
        delete = self.route('/teacher/api/report-reviews/{submission_id}/attachments/{attachment_id}/delete', 'POST')
        with self.assertRaises(HTTPException):
            asyncio.run(delete(self.teacher(body={'csrf_token': 'tt'}), sub_id, attachment_id))  # 返却後は削除できない

    def test_attachment_delete_endpoint_removes_file(self):
        sub_id = self.prepare_submitted()
        self.lock(sub_id)
        attachment_id = self.attach(sub_id, 'a.pdf')['attachment']['id']
        self.assertEqual(len(list(self.attach_dir.iterdir())), 1)
        delete = self.route('/teacher/api/report-reviews/{submission_id}/attachments/{attachment_id}/delete', 'POST')
        self.assertTrue(asyncio.run(delete(self.teacher(body={'csrf_token': 'tt'}), sub_id, attachment_id))['ok'])
        self.assertEqual(list(self.attach_dir.iterdir()), [])

    # ---- 画面
    def test_teacher_review_page_for_pdf_submission(self):
        sub_id = self.prepare_submitted()
        self.lock(sub_id)
        page = asyncio.run(self.route('/teacher/report-reviews/submissions/{submission_id}')(self.teacher(), sub_id))['html']
        self.assertIn('提出されたPDF（提出.pdf）を開く', page)
        self.assertIn('submitted.pdf', page)
        self.assertNotIn('提出版PDFを開く', page)
        self.assertIn('項目ごとのコメントはありません', page)
        self.assertIn('id="review-attachment-file"', page)
        self.assertIn('id="review-return"', page)

    def test_round_page_shows_format_and_pdf_marks(self):
        self.prepare_submitted()
        page = asyncio.run(self.route('/teacher/report-reviews/rounds/{frame_key}/{day}')(self.teacher(), 0, DAY))['html']
        self.assertIn('提出形式：PDF', page)
        self.assertIn('review-muted">PDF<', page)

    def render_student_page(self, submission, state=True):
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        template = env.from_string(env.loader.get_source(env, 'group_report.html')[0].replace("{% extends 'base.html' %}", ''))
        return template.render(request=SimpleNamespace(session={'user_id': 'u1'}), group={'id': 'g1', 'name': '班1', 'kind': 'group'},
                               state={'template': TEMPLATE} if state else None, submission=submission, csrf_token='x')

    def test_student_report_page_for_pdf_round(self):
        with self.db() as db:
            submission = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 18))
        html = self.render_student_page(submission)
        self.assertIn('この提出日はPDFで提出します', html)
        self.assertNotIn('class="report-submit"', html)  # PDFの提出日にはテンプレートの「提出」ボタンを出さない
        sub_id = self.prepare_submitted()
        self.lock(sub_id)
        self.attach(sub_id, '補足.pdf')
        with self.db() as db, db:
            return_review(db, sub_id, '学校A', 't1', '読みました', {}, 'returned', None, at('2026-11-19'))
        with self.db() as db:
            submission = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 20))
        html = self.render_student_page(submission)
        self.assertIn('提出したPDF（提出.pdf）', html)
        self.assertIn('読みました', html)
        self.assertIn('補足.pdf', html)
        self.assertIn('review-attachments/', html)
        self.assertNotIn('レビュー付きPDF', html)  # PDFで提出されたものにはレビュー付きPDFがない
        # 学校のテンプレートがなくても、PDFの提出状況とレビューは見られる
        self.assertIn('提出したPDF（提出.pdf）', self.render_student_page(submission, state=False))

    def test_either_round_shows_both_ways_on_student_page(self):
        self.set_format(DAY, 'either')
        with self.db() as db:
            submission = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 18))
        html = self.render_student_page(submission)
        self.assertIn('PDFでもテンプレートでも提出できます', html)
        self.assertIn('class="report-submit"', html)


if __name__ == '__main__':
    unittest.main()
