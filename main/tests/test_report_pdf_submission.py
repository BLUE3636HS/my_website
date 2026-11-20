"""提出日ごとの提出形式（テンプレート／PDF／どちらでも）、PDFでの提出、レビューへの添付ファイルのテスト。"""
import datetime
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from group_reports import run_scheduled_snapshots
from report_reviews import (
    ATTACHMENT_MAX_COUNT, acquire_review_lock, add_review_attachment, apply_submission_days, build_round_zip,
    delete_review_attachment, list_attachments, normalize_submission_formats, open_targets, pdf_submit_targets,
    return_review, review_returned, round_groups, round_summaries, run_report_deadlines, run_report_reminders,
    save_review, student_submission_context, submit_pdf, submit_report, review_pdf,
)
from test_report_reviews import Base, at

DAY = '2026-11-20'


class PdfSubmissionTests(Base):
    def add_format_round(self, day, fmt, title='中間成果レポート'):
        self.add_round(day, title)
        with self.db() as db, db:
            db.execute('UPDATE group_report_submission_day SET submit_format=? WHERE day=?', (fmt, day))

    def pdf(self, group_id, day, student='u1', attempt=0, now=None, name='report.pdf'):
        with self.db() as db, db:
            return submit_pdf(db, '学校A', group_id, student, day, attempt, f'{group_id}-{attempt}.pdf', name, now or at('2026-11-18'))

    def targets(self, group_id='g1', day=(2026, 11, 18)):
        with self.db() as db:
            return open_targets(db, '学校A', group_id, datetime.date(*day))

    def review(self, sub_id, comment='よくできています', result='returned', due=None, teacher='t1'):
        with self.db() as db, db:
            acquire_review_lock(db, sub_id, '学校A', teacher)
            return_review(db, sub_id, '学校A', teacher, comment, {}, result, due, at('2026-11-19'))

    # ---- 提出形式
    def test_existing_round_defaults_to_template(self):
        self.add_round(DAY)
        target = self.targets()[0]
        self.assertEqual((target['format'], target['can_template'], target['can_pdf']), ('template', True, False))
        with self.assertRaises(HTTPException) as caught:
            self.pdf('g1', DAY)  # テンプレートの提出日にはPDFを出せない
        self.assertEqual(caught.exception.status_code, 409)

    def test_pdf_round_accepts_pdf_only(self):
        self.add_format_round(DAY, 'pdf')
        target = self.targets()[0]
        self.assertEqual((target['format'], target['can_template'], target['can_pdf']), ('pdf', False, True))
        with self.assertRaises(HTTPException) as caught:
            self.submit('g1', DAY)  # PDFの提出日にはテンプレートで出せない
        self.assertEqual(caught.exception.status_code, 409)
        self.pdf('g1', DAY)
        with self.db() as db:
            row = db.execute("SELECT version_id,pdf_path,pdf_filename,method,submitted_by,finalized_at FROM group_report_submission WHERE group_id='g1'").fetchone()
            self.assertEqual(self.targets(), [])  # 提出したら、返却されるまで出し直せない
            self.assertEqual(pdf_submit_targets(db, '学校A', 'g1', datetime.date(2026, 11, 18)), [])
        self.assertIsNone(row[0])
        self.assertEqual(row[1:5], ('g1-0.pdf', 'report.pdf', 'manual', 'u1'))
        self.assertIsNotNone(row[5])
        with self.assertRaises(HTTPException) as again:
            self.pdf('g1', DAY, student='u2')
        self.assertEqual(again.exception.status_code, 409)

    def test_either_round_allows_both_and_each_once(self):
        self.add_format_round(DAY, 'either')
        target = self.targets()[0]
        self.assertEqual((target['can_template'], target['can_pdf']), (True, True))
        self.pdf('g1', DAY)
        after_pdf = self.targets()[0]
        self.assertEqual((after_pdf['can_template'], after_pdf['can_pdf'], after_pdf['has_pdf']), (True, False, True))
        self.submit('g1', DAY)  # PDFを出したあとにテンプレートも出せる
        self.assertEqual(self.targets(), [])
        with self.db() as db:
            row = db.execute("SELECT version_id,pdf_path,method FROM group_report_submission WHERE group_id='g1'").fetchone()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM group_report_submission WHERE group_id='g1'").fetchone()[0], 1)
        self.assertIsNotNone(row[0])
        self.assertEqual(row[1:], ('g1-0.pdf', 'manual'))
        # 先にテンプレート→あとからPDF
        self.submit('g2', DAY, student='u3')
        self.assertEqual(self.targets('g2')[0]['can_pdf'], True)
        self.pdf('g2', DAY, student='u3')
        self.assertEqual(self.targets('g2'), [])

    def test_no_more_pieces_after_teacher_returns(self):
        self.add_format_round(DAY, 'either')
        self.pdf('g1', DAY)
        self.review(self.sub_id('g1', DAY))
        self.assertEqual(self.targets(), [])  # 返却後はどちらも出せない
        with self.assertRaises(HTTPException):
            self.submit('g1', DAY)

    # ---- 期限
    def test_pdf_round_deadline_never_auto_submits_and_marks_no_submission(self):
        self.add_format_round(DAY, 'pdf')
        self.pdf('g1', DAY)
        self.close_round(DAY, at('2026-11-21', 1))
        with self.db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM group_report_submission WHERE group_id='g2'").fetchone())
            groups = {g['id']: g for g in round_groups(db, '学校A', 0, DAY, True)}
            summary = round_summaries(db, '学校A', datetime.date(2026, 11, 22))[0]
        self.assertEqual(groups['g2']['status_label'], '提出なし')
        self.assertEqual(groups['g2']['status'], 'no_draft')
        self.assertEqual(groups['g1']['status'], 'unreviewed')  # PDFを出したグループはレビューを待つ
        self.assertTrue(groups['g1']['attempts'][0]['reviewable'])
        self.assertEqual(summary['format'], 'pdf')

    def test_either_round_deadline_skips_pdf_groups_and_auto_submits_the_rest(self):
        self.add_format_round(DAY, 'either')
        self.pdf('g1', DAY)  # g1 はPDFだけ。g2 は何も出さない
        self.close_round(DAY, at('2026-11-21', 1))
        with self.db() as db:
            g1 = db.execute("SELECT version_id,pdf_path,method FROM group_report_submission WHERE group_id='g1'").fetchone()
            g2 = db.execute("SELECT version_id,pdf_path,method FROM group_report_submission WHERE group_id='g2'").fetchone()
        self.assertIsNone(g1[0])  # PDFがあれば下書きは自動提出しない
        self.assertIsNotNone(g1[1])
        self.assertEqual(g2[:3][0] is not None and g2[1] is None, True)
        self.assertEqual(g2[2], 'auto')

    def test_template_round_deadline_still_auto_submits(self):
        self.add_round(DAY)
        self.close_round(DAY, at('2026-11-21', 1))
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM group_report_submission WHERE method='auto'").fetchone()[0], 2)

    def test_resubmission_follows_round_format(self):
        self.add_format_round(DAY, 'pdf')
        self.pdf('g1', DAY)
        self.review(self.sub_id('g1', DAY), result='resubmit', due='2026-11-25')
        target = self.targets('g1', (2026, 11, 20))[0]
        self.assertEqual((target['attempt'], target['can_pdf'], target['can_template']), (1, True, False))
        self.pdf('g1', DAY, attempt=1, now=at('2026-11-21'))
        # 再提出の期限が来ても、PDFの提出日は自動提出されない
        self.add_format_round('2026-11-22', 'pdf', '別の提出日')
        self.review(self.sub_id('g1', DAY, 1), result='resubmit', due='2026-11-23')
        run_report_deadlines(self.path, at('2026-11-24', 1))
        with self.db() as db:
            row = db.execute("SELECT version_id,pdf_path,finalized_at FROM group_report_submission WHERE group_id='g1' AND attempt=2").fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertIsNotNone(row[2])  # 締め切られて「提出なし」

    def test_reminder_text_depends_on_format(self):
        self.add_format_round('2026-11-19', 'pdf')
        self.assertGreater(run_report_reminders(self.path, at('2026-11-18')), 0)
        with self.db() as db:
            body = db.execute("SELECT body FROM notification WHERE notification_type='report_reminder' LIMIT 1").fetchone()[0]
        self.assertIn('「PDFを提出」', body)
        self.assertIn('提出なし', body)

    def test_reminder_skips_groups_that_submitted_pdf(self):
        self.add_format_round('2026-11-19', 'pdf')
        self.pdf('g1', '2026-11-19')
        run_report_reminders(self.path, at('2026-11-18'))
        with self.db() as db:
            groups = {r[0] for r in db.execute("SELECT group_id FROM group_report_reminder_sent")}
        self.assertNotIn('g1', groups)  # PDFを出したグループには送らない
        self.assertIn('g2', groups)

    # ---- レビュー
    def test_pdf_submission_review_has_no_field_comments_and_students_see_feedback(self):
        self.add_format_round(DAY, 'pdf')
        self.pdf('g1', DAY)
        sub_id = self.sub_id('g1', DAY)
        with self.db() as db, db:
            acquire_review_lock(db, sub_id, '学校A', 't1')
            with self.assertRaises(HTTPException) as caught:
                save_review(db, sub_id, '学校A', 't1', '全体', {'a': 'テーマへのコメント'})
            self.assertEqual(caught.exception.status_code, 422)  # PDFには項目がない
            save_review(db, sub_id, '学校A', 't1', 'PDFを読みました', {})
        self.review(sub_id, comment='PDFを読みました', result='resubmit', due='2026-11-25')
        with self.db() as db:
            context = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 21))
            with self.assertRaises(HTTPException) as no_review_pdf:
                review_pdf(self.path, Path(self.image_dir.name), db, sub_id, group_id='g1')
        record = next(r for r in context['records'] if r['attempt_label'] == '本提出')
        self.assertEqual(record['pdf_filename'], 'report.pdf')
        self.assertEqual(record['feedback']['overall'], 'PDFを読みました')
        self.assertTrue(record['feedback']['pending'])
        self.assertEqual(no_review_pdf.exception.status_code, 404)  # テンプレートの提出だけレビュー付きPDFがある

    # ---- 添付ファイル
    def upload(self, name='資料.pdf', data=b'%PDF-1.7\nbody'):
        return SimpleNamespace(filename=name, file=io.BytesIO(data))

    def started_review(self, teacher='t1'):
        self.add_format_round(DAY, 'pdf')
        self.pdf('g1', DAY)
        sub_id = self.sub_id('g1', DAY)
        with self.db() as db, db:
            acquire_review_lock(db, sub_id, '学校A', teacher)
        return sub_id

    def test_attachments_need_the_review_lock_and_visible_only_after_return(self):
        self.add_format_round(DAY, 'pdf')
        self.pdf('g1', DAY)
        sub_id = self.sub_id('g1', DAY)
        attach_dir = Path(self.image_dir.name) / 'att'
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', self.upload())  # レビューを開始していない
            self.assertEqual(caught.exception.status_code, 423)
            acquire_review_lock(db, sub_id, '学校A', 't1')
            with self.assertRaises(HTTPException) as other:
                add_review_attachment(db, attach_dir, sub_id, '学校A', 't2', self.upload())  # 他の先生はできない
            self.assertEqual(other.exception.status_code, 423)
            item = add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', self.upload())
            self.assertEqual(item['filename'], '資料.pdf')
            self.assertTrue((attach_dir / next(attach_dir.iterdir()).name).is_file())
            self.assertEqual(len(list_attachments(db, sub_id)), 1)
            self.assertFalse(review_returned(db, sub_id))
            return_review(db, sub_id, '学校A', 't1', 'OK', {}, 'returned', None, at('2026-11-19'))
            self.assertTrue(review_returned(db, sub_id))
            with self.assertRaises(HTTPException) as frozen:
                add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', self.upload('b.pdf'))
            self.assertEqual(frozen.exception.status_code, 423)  # 返却するとロックが外れ、編集できない
        with self.db() as db:
            context = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 21))
        record = context['records'][0]
        self.assertEqual([a['filename'] for a in record['feedback']['attachments']], ['資料.pdf'])

    def test_attachment_types_size_count_and_delete(self):
        sub_id = self.started_review()
        attach_dir = Path(self.image_dir.name) / 'att'
        with self.db() as db, db:
            for name, data in (('a.exe', b'MZ\x90'), ('b.png', b'%PDF-1.7'), ('c.pdf', b'')):
                with self.assertRaises(HTTPException) as caught:
                    add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', self.upload(name, data))
                self.assertEqual(caught.exception.status_code, 422, name)
            for name, data in (('d.png', b'\x89PNG\r\n\x1a\nxx'), ('e.jpg', b'\xff\xd8\xffxx'), ('f.docx', b'PK\x03\x04xx'),
                               ('g.xlsx', b'PK\x03\x04xx'), ('h.pptx', b'PK\x03\x04xx'), ('i.doc', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1xx')):
                add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', self.upload(name, data))
            big = self.upload('big.pdf', b'%PDF-' + b'0' * (20 * 1024 * 1024 + 1))
            with self.assertRaises(HTTPException) as too_big:
                add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', big)
            self.assertEqual(too_big.exception.status_code, 413)
            for index in range(ATTACHMENT_MAX_COUNT - 6):
                add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', self.upload(f'n{index}.pdf'))
            with self.assertRaises(HTTPException) as too_many:
                add_review_attachment(db, attach_dir, sub_id, '学校A', 't1', self.upload('over.pdf'))
            self.assertEqual(too_many.exception.status_code, 422)
            first = list_attachments(db, sub_id)[0]
            stored_before = len(list(attach_dir.iterdir()))
            delete_review_attachment(db, attach_dir, sub_id, first['id'], '学校A', 't1')
            self.assertEqual(len(list(attach_dir.iterdir())), stored_before - 1)
            self.assertEqual(len(list_attachments(db, sub_id)), ATTACHMENT_MAX_COUNT - 1)

    def test_attachments_work_for_template_submissions_too(self):
        self.add_round(DAY)
        self.submit('g1', DAY)
        sub_id = self.sub_id('g1', DAY)
        with self.db() as db, db:
            acquire_review_lock(db, sub_id, '学校A', 't1')
            add_review_attachment(db, Path(self.image_dir.name) / 'att', sub_id, '学校A', 't1', self.upload('メモ.pdf'))
            self.assertEqual(len(list_attachments(db, sub_id)), 1)

    def test_other_school_cannot_attach(self):
        sub_id = self.started_review()
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                add_review_attachment(db, Path(self.image_dir.name) / 'att', sub_id, '学校B', 'tb', self.upload())
            self.assertEqual(caught.exception.status_code, 404)

    # ---- 提出日の設定
    def test_formats_are_saved_validated_and_locked_after_submission(self):
        today = datetime.date(2026, 11, 17)
        with self.db() as db, db:
            apply_submission_days(db, '学校A', 0, today, {DAY: '提出日A', '2026-11-27': '提出日B'}, 'boss', 'now',
                                  {DAY: 'pdf', '2026-11-27': 'either'})
            rows = dict(db.execute('SELECT day,submit_format FROM group_report_submission_day').fetchall())
        self.assertEqual(rows, {DAY: 'pdf', '2026-11-27': 'either'})
        with self.db() as db, db:
            apply_submission_days(db, '学校A', 0, today, {DAY: '提出日A', '2026-11-27': '提出日B'}, 'boss', 'now', {DAY: 'template'})
            self.assertEqual(db.execute("SELECT submit_format FROM group_report_submission_day WHERE day=?", (DAY,)).fetchone()[0], 'template')
            self.assertEqual(db.execute("SELECT submit_format FROM group_report_submission_day WHERE day='2026-11-27'").fetchone()[0], 'template')  # 指定がなければテンプレート
        for bad in ({DAY: 'word'}, {'2026-12-01': 'pdf'}, ['pdf']):
            with self.assertRaises(HTTPException):
                normalize_submission_formats(bad, {DAY: 'x'})
        # 提出がある提出日の形式は変えられない
        with self.db() as db, db:
            db.execute('UPDATE group_report_submission_day SET submit_format=? WHERE day=?', ('pdf', DAY))
        self.pdf('g1', DAY)
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                apply_submission_days(db, '学校A', 0, today, {DAY: '提出日A', '2026-11-27': '提出日B'}, 'boss', 'now', {DAY: 'either'})
            self.assertEqual(caught.exception.status_code, 409)
            with self.assertRaises(HTTPException) as removal:
                apply_submission_days(db, '学校A', 0, today, {'2026-11-27': '提出日B'}, 'boss', 'now')  # PDFの提出があれば提出日も外せない
            self.assertEqual(removal.exception.status_code, 409)

    # ---- まとめてダウンロード
    def test_zip_includes_submitted_pdf_files(self):
        self.add_format_round(DAY, 'either')
        pdf_dir = Path(self.image_dir.name) / 'pdfs'
        pdf_dir.mkdir()
        (pdf_dir / 'g1-0.pdf').write_bytes(b'%PDF-1.7 uploaded')
        self.pdf('g1', DAY)  # g1 はPDFだけ
        self.submit('g2', DAY, student='u3')  # g2 はテンプレートだけ
        self.close_round(DAY, at('2026-11-21', 1))
        with self.db() as db:
            data, count = build_round_zip(db, self.path, Path(self.image_dir.name), '学校A', 0, DAY, pdf_dir)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertEqual(count, 2)
        self.assertEqual(len(names), 2)
        self.assertTrue(any('班1' in name for name in names))
        self.assertTrue(any('班2' in name for name in names))
        self.assertEqual(zipfile.ZipFile(io.BytesIO(data)).read(next(n for n in names if '班1' in n)), b'%PDF-1.7 uploaded')


if __name__ == '__main__':
    unittest.main()
