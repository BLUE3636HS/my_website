"""成果レポートの提出日・提出・レビュー・リマインドのテスト。"""
import asyncio
import datetime
import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

from group_reports import JST, run_scheduled_snapshots
from notifications import initialize_notification_tables
from report_reviews import (
    acquire_review_lock, build_report_review_router, build_round_zip, force_release_review_lock, input_status,
    open_targets, release_review_lock, return_review, round_groups, round_summaries, run_report_deadlines,
    run_report_reminders, save_review, student_submission_context, submit_report,
)
from studies import initialize_studies
from template_management import SnapshotDays, create_template_router, snapshot_today

TEMPLATE = {'id': 1, 'name': 'T', 'fields': [
    {'stable_key': 'a', 'label': 'テーマ', 'field_type': 'text'},
    {'stable_key': 'b', 'label': '結果', 'field_type': 'both'},
    {'stable_key': 'c', 'label': '写真', 'field_type': 'image'},
]}
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'templates'


def at(day, hour=12, minute=0):
    return datetime.datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=JST)


class Base(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        self.image_dir = tempfile.TemporaryDirectory()
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_studies(db)
            initialize_notification_tables(db)
            db.executescript("""
                CREATE TABLE teacher(id TEXT, pwd TEXT, school TEXT, role TEXT);
                CREATE TABLE student(id TEXT PRIMARY KEY, name TEXT, furigana TEXT);
                CREATE TABLE tekne_school_identity(id TEXT PRIMARY KEY, school_name TEXT UNIQUE, created_at TEXT);
                CREATE TABLE tekne_student_identity(id TEXT PRIMARY KEY, login_id TEXT, school_id TEXT, created_at TEXT, deleted_at TEXT);
                CREATE TABLE tekne_chat_group(id TEXT PRIMARY KEY, school_id TEXT, name TEXT, created_by TEXT, kind TEXT DEFAULT 'group');
                CREATE TABLE tekne_chat_member(group_id TEXT, school_id TEXT, student_id TEXT, joined_at TEXT, left_at TEXT);
                INSERT INTO teacher VALUES('boss','x','学校A','super_teacher'),('t1','x','学校A','teacher'),('t2','x','学校A','teacher'),('tb','x','学校B','teacher');
                INSERT INTO tekne_school_identity VALUES('sA','学校A','now'),('sB','学校B','now');
                INSERT INTO student VALUES('u1','生徒1','せいと1'),('u2','生徒2','せいと2'),('u3','生徒3','せいと3'),('u9','生徒9','せいと9');
                INSERT INTO tekne_student_identity VALUES('i1','u1','sA','now',NULL),('i2','u2','sA','now',NULL),('i3','u3','sA','now',NULL),('i9','u9','sB','now',NULL);
                INSERT INTO tekne_chat_group VALUES('g1','sA','班1','i1','group'),('g2','sA','班2','i3','group'),('p3','sA','メモ','i3','personal'),('g9','sB','B班','i9','group');
                INSERT INTO tekne_chat_member VALUES('g1','sA','i1','2026-01-01',NULL),('g1','sA','i2','2026-01-02',NULL),
                    ('g2','sA','i3','2026-01-01',NULL),('p3','sA','i3','2026-01-01',NULL),('g9','sB','i9','2026-01-01',NULL);
            """)
            for group_id, school_id, school in (('g1', 'sA', '学校A'), ('g2', 'sA', '学校A'), ('g9', 'sB', '学校B')):
                self.set_draft(db, group_id, school_id, school, {'a': {'value': f'{group_id}テーマ', 'images': []}, 'b': {'value': '', 'images': []}, 'c': {'value': '', 'images': []}})

    def tearDown(self):
        os.remove(self.path)
        self.image_dir.cleanup()

    def set_draft(self, db, group_id, school_id, school, data):
        db.execute("""INSERT INTO group_report_draft VALUES(?,?,?,1,?,?,'2026-11-01T00:00:00+00:00',0)
            ON CONFLICT(group_id) DO UPDATE SET report_data=excluded.report_data""",
                   (group_id, school_id, school, json.dumps(TEMPLATE, ensure_ascii=False), json.dumps(data, ensure_ascii=False)))

    def db(self):
        return closing(sqlite3.connect(self.path))

    def add_round(self, day, title='中間成果レポート', school='学校A'):
        with self.db() as db, db:
            db.execute("INSERT INTO group_report_snapshot_day(school,day,created_by,created_at) VALUES(?,?,'boss','now')", (school, day))
            db.execute("INSERT INTO group_report_submission_day(school,day,title,created_by,created_at) VALUES(?,?,?,'boss','now')", (school, day, title))

    def submit(self, group_id, day, student='u1', attempt=0, now=None):
        with self.db() as db, db:
            return submit_report(db, '学校A', group_id, student, day, attempt, now or at('2026-11-18'))

    def close_round(self, day, now):
        run_scheduled_snapshots(self.path, now)
        return run_report_deadlines(self.path, now)

    def sub_id(self, group_id, day, attempt=0):
        with self.db() as db:
            return db.execute('SELECT id FROM group_report_submission WHERE group_id=? AND round_day=? AND attempt=?', (group_id, day, attempt)).fetchone()[0]

    def call(self, fn, *args, **kwargs):
        with self.db() as db, db:
            return fn(db, *args, **kwargs)


class SubmissionTests(Base):
    def test_student_can_resubmit_until_deadline_and_last_one_counts(self):
        self.add_round('2026-11-20')
        self.submit('g1', '2026-11-20')
        with self.db() as db, db:
            self.set_draft(db, 'g1', 'sA', '学校A', {'a': {'value': '直した', 'images': []}})
        self.submit('g1', '2026-11-20', student='u2')
        with self.db() as db:
            versions = db.execute("SELECT kind,report_snapshot FROM group_report_version WHERE group_id='g1' ORDER BY version_number").fetchall()
            sub = db.execute("SELECT method,submitted_by,version_id FROM group_report_submission WHERE group_id='g1'").fetchone()
            current = db.execute('SELECT report_snapshot FROM group_report_version WHERE id=?', (sub[2],)).fetchone()[0]
        self.assertEqual([v[0] for v in versions], ['manual', 'submission'])
        self.assertEqual(sub[:2], ('manual', 'u2'))
        self.assertIn('直した', current)

    def test_only_nearest_open_round_and_before_deadline(self):
        self.add_round('2026-11-20')
        self.add_round('2026-12-10', '最終')
        with self.db() as db:
            targets = open_targets(db, '学校A', 'g1', datetime.date(2026, 11, 18))
        self.assertEqual([(t['round_day'], t['title']) for t in targets], [('2026-11-20', '中間成果レポート')])
        with self.assertRaises(HTTPException) as caught:
            self.submit('g1', '2026-12-10')
        self.assertEqual(caught.exception.status_code, 409)
        self.submit('g1', '2026-11-20', now=at('2026-11-20', 23, 59))  # 当日23:59までは提出できる
        with self.assertRaises(HTTPException) as caught:
            self.submit('g1', '2026-11-20', now=at('2026-11-21', 0, 1))
        self.assertEqual(caught.exception.status_code, 409)

    def test_submit_is_blocked_while_another_member_edits(self):
        self.add_round('2026-11-20')
        with self.db() as db, db:
            db.execute("INSERT INTO group_report_lock VALUES('g1','a','u2','生徒2','now')")
        with self.assertRaises(HTTPException) as caught:
            self.submit('g1', '2026-11-20', student='u1')
        self.assertEqual(caught.exception.status_code, 423)
        self.submit('g1', '2026-11-20', student='u2')

    def test_deadline_auto_submits_from_scheduled_snapshot_and_keeps_early_submission(self):
        self.add_round('2026-11-20')
        self.submit('g1', '2026-11-20')
        self.assertEqual(self.close_round('2026-11-20', at('2026-11-20', 23, 59)), 0)  # 期限前は何もしない
        self.assertEqual(self.close_round('2026-11-20', at('2026-11-21', 0, 5)), 1)
        self.assertEqual(self.close_round('2026-11-20', at('2026-11-21', 0, 10)), 0)  # 何度実行しても同じ
        with self.db() as db:
            rows = {r[0]: r[1:] for r in db.execute('''SELECT s.group_id,s.method,v.kind,s.finalized_at IS NOT NULL
                FROM group_report_submission s JOIN group_report_version v ON v.id=s.version_id''')}
        self.assertEqual(rows['g1'], ('manual', 'submission', 1))
        self.assertEqual(rows['g2'], ('auto', 'scheduled', 1))
        self.assertNotIn('g9', rows)  # 他校
        self.assertNotIn('p3', rows)  # 下書きなし
        with self.db() as db:
            groups = {g['id']: g for g in round_groups(db, '学校A', 0, '2026-11-20', True)}
        self.assertEqual(groups['p3']['status_label'], '未提出（下書きなし）')
        self.assertEqual(groups['g2']['status'], 'unreviewed')

    def test_deadline_waits_for_snapshot_processing(self):
        self.add_round('2026-11-20')
        self.assertEqual(run_report_deadlines(self.path, at('2026-11-21')), 0)
        with self.db() as db:
            self.assertIsNone(db.execute('SELECT finalized_at FROM group_report_submission_day').fetchone()[0])
        self.assertEqual(self.close_round('2026-11-20', at('2026-11-21')), 2)

    def test_input_status_counts_empty_fields(self):
        data = {'a': {'value': ' ', 'images': []}, 'b': {'value': '', 'images': [{'content_hash': 'x'}]}, 'c': {'images': []}}
        self.assertEqual(input_status(json.dumps(TEMPLATE), json.dumps(data)), {'empty': 2, 'total': 3})


class ReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.add_round('2026-11-20')
        self.close_round('2026-11-20', at('2026-11-21', 0, 5))
        self.sid = self.sub_id('g1', '2026-11-20')
        self.now = at('2026-11-22')

    def test_review_requires_deadline_to_pass(self):
        self.add_round('2026-11-30', '次')
        self.submit('g1', '2026-11-30', now=at('2026-11-25'))
        early = self.sub_id('g1', '2026-11-30')
        with self.assertRaises(HTTPException) as caught:
            self.call(acquire_review_lock, early, '学校A', 't1')
        self.assertEqual(caught.exception.status_code, 409)

    def test_lock_blocks_other_teachers_and_anyone_can_force_release(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        self.call(acquire_review_lock, self.sid, '学校A', 't1')  # 本人は何度でも
        with self.assertRaises(HTTPException) as caught:
            self.call(acquire_review_lock, self.sid, '学校A', 't2')
        self.assertEqual(caught.exception.status_code, 423)
        self.assertIn('t1', caught.exception.detail)
        with self.assertRaises(HTTPException):
            self.call(save_review, self.sid, '学校A', 't2', 'x', {})
        with self.db() as db:
            groups = {g['id']: g for g in round_groups(db, '学校A', 0, '2026-11-20', True)}
        self.assertEqual(groups['g1']['attempts'][0]['lock_holder'], 't1')
        self.assertEqual(groups['g1']['status'], 'reviewing')
        self.assertEqual(self.call(force_release_review_lock, self.sid, '学校A'), 't1')
        self.call(acquire_review_lock, self.sid, '学校A', 't2')
        with self.assertRaises(HTTPException) as caught:
            self.call(save_review, self.sid, '学校A', 't1', 'x', {})
        self.assertEqual(caught.exception.status_code, 423)

    def test_other_school_cannot_touch(self):
        with self.assertRaises(HTTPException) as caught:
            self.call(acquire_review_lock, self.sid, '学校B', 'tb')
        self.assertEqual(caught.exception.status_code, 404)

    def test_editors_are_all_listed_and_returned_review_is_frozen_and_notified(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        self.call(save_review, self.sid, '学校A', 't1', '全体', {'a': 'テーマがよい', 'b': '  '}, now=self.now)
        self.call(release_review_lock, self.sid, '学校A', 't1')
        self.call(acquire_review_lock, self.sid, '学校A', 't2')
        with self.assertRaises(HTTPException) as caught:
            self.call(save_review, self.sid, '学校A', 't2', '全体', {'zzz': 'x'})
        self.assertEqual(caught.exception.status_code, 422)
        self.call(return_review, self.sid, '学校A', 't2', '全体2', {'a': 'テーマがよい'}, 'returned', now=self.now)
        with self.db() as db:
            review = db.execute('SELECT overall_comment,field_comments,result,returned_by FROM group_report_review WHERE submission_id=?', (self.sid,)).fetchone()
            editors = [r[0] for r in db.execute('SELECT teacher_id FROM group_report_review_editor WHERE submission_id=? ORDER BY first_edited_at,teacher_id', (self.sid,))]
            notified = sorted(r[0] for r in db.execute("SELECT recipient_user_id FROM notification WHERE notification_type='report_returned'"))
            lock = db.execute('SELECT 1 FROM group_report_review_lock').fetchone()
            context = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 22))
        self.assertEqual(review, ('全体2', '{"a": "テーマがよい"}', 'returned', 't2'))
        self.assertEqual(editors, ['t1', 't2'])
        self.assertEqual(notified, ['u1', 'u2'])
        self.assertIsNone(lock)
        feedback = context['records'][0]['feedback']
        self.assertEqual(feedback['fields'], [{'label': 'テーマ', 'comment': 'テーマがよい'}])
        self.assertEqual(feedback['editors'], ['t1', 't2'])
        for fn, args in ((acquire_review_lock, ('t1',)), (save_review, ('t2', 'x', {}))):
            with self.assertRaises(HTTPException) as caught:
                self.call(fn, self.sid, '学校A', *args)
            self.assertIn(caught.exception.status_code, (409, 423))

    def test_draft_review_is_hidden_from_students(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        self.call(save_review, self.sid, '学校A', 't1', '下書き', {})
        with self.db() as db:
            context = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 22))
        self.assertIsNone(context['records'][0]['feedback'])
        self.assertEqual(context['records'][0]['status'], '提出済み（レビュー待ち）')

    def test_finishing_without_writing_keeps_unreviewed(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        self.call(save_review, self.sid, '学校A', 't1', '', {'a': ''})
        self.call(release_review_lock, self.sid, '学校A', 't1')
        with self.db() as db:
            groups = {g['id']: g for g in round_groups(db, '学校A', 0, '2026-11-20', True)}
        self.assertEqual(groups['g1']['status'], 'unreviewed')

    def test_resubmit_flow_with_due_date_auto_submission_and_new_review(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        for bad in (None, '2026-11-21', '2026/11/30', '2028-01-01'):
            with self.subTest(due=bad), self.assertRaises(HTTPException) as caught:
                self.call(return_review, self.sid, '学校A', 't1', 'x', {}, 'resubmit', bad, now=self.now)
            self.assertEqual(caught.exception.status_code, 422)
        self.call(return_review, self.sid, '学校A', 't1', '直して', {}, 'resubmit', '2026-11-25', now=self.now)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notification WHERE notification_type='report_resubmit'").fetchone()[0], 2)
            targets = open_targets(db, '学校A', 'g1', datetime.date(2026, 11, 23))
            groups = {g['id']: g for g in round_groups(db, '学校A', 0, '2026-11-20', True)}
        self.assertEqual([(t['attempt'], t['due_day']) for t in targets], [(1, '2026-11-25')])
        self.assertEqual(groups['g1']['status'], 'awaiting')
        # g2 は「要再提出」→ 期限までに出さなかったので自動提出
        sid2 = self.sub_id('g2', '2026-11-20')
        self.call(acquire_review_lock, sid2, '学校A', 't1')
        self.call(return_review, sid2, '学校A', 't1', '', {}, 'resubmit', '2026-11-25', now=self.now)
        self.submit('g1', '2026-11-20', attempt=1, now=at('2026-11-25', 20))
        self.assertEqual(run_report_deadlines(self.path, at('2026-11-25', 23, 59)), 0)
        self.assertEqual(run_report_deadlines(self.path, at('2026-11-26', 0, 1)), 1)
        with self.db() as db:
            rows = {r[0]: r[1:] for r in db.execute('''SELECT group_id,method,finalized_at IS NOT NULL FROM group_report_submission WHERE attempt=1''')}
        self.assertEqual(rows, {'g1': ('manual', 1), 'g2': ('auto', 1)})
        resub = self.sub_id('g1', '2026-11-20', 1)
        self.call(acquire_review_lock, resub, '学校A', 't2')
        self.call(return_review, resub, '学校A', 't2', 'OK', {}, 'returned', now=at('2026-11-27'))
        with self.db() as db:
            context = student_submission_context(db, '学校A', 'g1', datetime.date(2026, 11, 27))
            groups = {g['id']: g for g in round_groups(db, '学校A', 0, '2026-11-20', True)}
        self.assertEqual([r['attempt_label'] for r in context['records']], ['再提出1', '本提出'])
        self.assertEqual(context['records'][1]['feedback']['resubmit_due'], '11/25（水）')
        self.assertEqual(groups['g1']['status'], 'returned')

    def test_zip_contains_latest_submission_pdf_for_each_group(self):
        with self.db() as db:
            data, count = build_round_zip(db, self.path, self.image_dir.name, '学校A', 0, '2026-11-20')
        self.assertEqual(count, 2)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            self.assertTrue(all(archive.read(name).startswith(b'%PDF') for name in names))
        self.assertEqual(sorted(names), ['01_班1_本提出.pdf', '02_班2_本提出.pdf'])

    def test_round_summary_counts(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        with self.db() as db:
            rounds = round_summaries(db, '学校A', datetime.date(2026, 11, 22))
        self.assertEqual(rounds[0]['phase'], '締切済み')
        self.assertEqual(rounds[0]['counts'], {'reviewing': 1, 'unreviewed': 1, 'no_draft': 1})


class ReminderTests(Base):
    def notifications(self):
        with self.db() as db:
            return sorted(db.execute("SELECT recipient_user_id,title FROM notification WHERE notification_type='report_reminder'").fetchall())

    def test_three_days_and_one_day_before_once_each_skipping_submitted_groups(self):
        self.add_round('2026-11-20')
        self.assertEqual(run_report_reminders(self.path, at('2026-11-16')), 0)
        self.submit('g2', '2026-11-20', student='u3', now=at('2026-11-16'))
        self.assertEqual(run_report_reminders(self.path, at('2026-11-17')), 2)  # g1 と個人スペース p3
        self.assertEqual(run_report_reminders(self.path, at('2026-11-18')), 0)
        self.assertEqual(run_report_reminders(self.path, at('2026-11-19')), 2)
        self.assertEqual(run_report_reminders(self.path, at('2026-11-20')), 0)
        recipients = [r[0] for r in self.notifications()]
        self.assertEqual(recipients, ['u1', 'u1', 'u2', 'u2', 'u3', 'u3'])

    def test_late_created_day_sends_only_one_day_reminder(self):
        self.add_round('2026-11-20')
        self.assertEqual(run_report_reminders(self.path, at('2026-11-20')), 3)  # g1・g2・p3（前日扱いの1回だけ）
        self.assertEqual(run_report_reminders(self.path, at('2026-11-20', 18)), 0)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT DISTINCT offset_days FROM group_report_reminder_sent').fetchall(), [(1,)])


class SettingsTests(Base):
    def setUp(self):
        super().setUp()
        routes = create_template_router(lambda: self.path, SimpleNamespace(TemplateResponse=lambda **kwargs: kwargs)).routes
        self.save = next(r.endpoint for r in routes if r.path == '/teacher/school-report/snapshot-days' and 'POST' in r.methods)
        self.page = next(r.endpoint for r in routes if r.path == '/teacher/school-report/snapshot-days' and 'GET' in r.methods)

    def future(self, offset):
        return (snapshot_today() + datetime.timedelta(days=offset)).isoformat()

    def call_save(self, days, submissions):
        request = SimpleNamespace(session={'teacher_login': True, 'teacher_id': 'boss', 'school_report_template_csrf': 'token'})
        return asyncio.run(self.save(request, SnapshotDays(csrf_token='token', days=days, submissions=submissions)))

    def test_submission_days_are_saved_with_titles_and_validated(self):
        result = self.call_save([self.future(3), self.future(10)], {self.future(10): ' 最終 '})
        self.assertEqual([(d['submission'], d['title']) for d in result['days']], [(False, ''), (True, '最終')])
        for submissions in ({self.future(5): 'x'}, {self.future(3): '  '}, {self.future(3): 'x' * 51}):
            with self.subTest(submissions=submissions), self.assertRaises(HTTPException) as caught:
                self.call_save([self.future(3)], submissions)
            self.assertEqual(caught.exception.status_code, 422)
        result = self.call_save([self.future(3), self.future(10)], {self.future(10): '最終改'})
        self.assertEqual(result['days'][1]['title'], '最終改')

    def test_removing_submission_day_turns_early_submission_into_normal_history(self):
        day = self.future(3)
        self.call_save([day], {day: '中間'})
        with self.db() as db, db:
            submit_report(db, '学校A', 'g1', 'u1', day, 0)
        self.call_save([day], {})
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM group_report_submission').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT kind FROM group_report_version WHERE group_id='g1'").fetchall(), [('manual',)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM group_report_snapshot_day').fetchone()[0], 1)


class FakeRequest(SimpleNamespace):
    async def json(self):
        return self.body


class RouterTests(Base):
    """ルーターの権限・CSRF と、画面テンプレートが描画できること。"""

    def setUp(self):
        super().setUp()
        today = snapshot_today()
        self.day = (today - datetime.timedelta(days=2)).isoformat()
        self.add_round(self.day)
        self.close_round(self.day, datetime.datetime.now(JST))
        self.sid = self.sub_id('g1', self.day)
        self.rendered = []
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)

        def render(request, name, context):
            html = env.get_template(name).render(**context)
            self.rendered.append(html)
            return {'name': name, 'context': context, 'html': html}
        router = build_report_review_router(self.path, SimpleNamespace(TemplateResponse=render), None, self.image_dir.name)
        self.routes = {(r.path, tuple(sorted(r.methods))[0]): r.endpoint for r in router.routes}

    def request(self, teacher='t1', path='/teacher/report-reviews', body=None, token='tok'):
        return FakeRequest(session={'teacher_login': True, 'teacher_id': teacher, 'report_review_csrf': token},
                           url=SimpleNamespace(path=path), body=body or {})

    def endpoint(self, path, method='GET'):
        return self.routes[(path, method)]

    def test_pages_render_for_teacher(self):
        page = asyncio.run(self.endpoint('/teacher/report-reviews')(self.request()))
        self.assertIn('中間成果レポート', page['html'])
        page = asyncio.run(self.endpoint('/teacher/report-reviews/rounds/{frame_key}/{day}')(self.request(), 0, self.day))
        self.assertIn('班1', page['html'])
        self.assertIn('download.zip', page['html'])
        review = self.endpoint('/teacher/report-reviews/submissions/{submission_id}')
        page = asyncio.run(review(self.request(), self.sid))
        self.assertIn('レビューを開始', page['html'])
        self.call(acquire_review_lock, self.sid, '学校A', 't2')
        page = asyncio.run(review(self.request(), self.sid))
        self.assertIn('t2 先生</strong>がレビュー中', page['html'])
        self.assertIn('ロックを外す', page['html'])
        page = asyncio.run(review(self.request('t2'), self.sid))
        self.assertIn('返却する', page['html'])
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(review(self.request('tb'), self.sid))
        self.assertEqual(caught.exception.status_code, 404)

    def test_actions_check_csrf_and_lock(self):
        lock = self.endpoint('/teacher/api/report-reviews/{submission_id}/lock', 'POST')
        save = self.endpoint('/teacher/api/report-reviews/{submission_id}/save', 'POST')
        force = self.endpoint('/teacher/api/report-reviews/{submission_id}/force-unlock', 'POST')
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(lock(self.request(body={'csrf_token': 'bad'}), self.sid))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(asyncio.run(lock(self.request(body={'csrf_token': 'tok'}), self.sid))['holder'], 't1')
        result = asyncio.run(save(self.request(body={'csrf_token': 'tok', 'overall_comment': 'よい', 'finish': True}), self.sid))
        self.assertEqual(result['editors'], ['t1'])
        with self.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM group_report_review_lock').fetchone())
        asyncio.run(lock(self.request('t2', body={'csrf_token': 'tok'}), self.sid))
        self.assertEqual(asyncio.run(force(self.request('t1', body={'csrf_token': 'tok'}), self.sid))['previous_holder'], 't2')

    def test_zip_download(self):
        response = asyncio.run(self.endpoint('/teacher/report-reviews/rounds/{frame_key}/{day}/download.zip')(self.request(), 0, self.day))
        self.assertEqual(response.media_type, 'application/zip')
        self.assertIn("filename*=UTF-8''", response.headers['content-disposition'])

    def test_student_page_template_renders_submission_and_feedback(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        self.call(return_review, self.sid, '学校A', 't1', '全体', {'a': 'よい'}, 'resubmit', (snapshot_today() + datetime.timedelta(days=3)).isoformat())
        with self.db() as db:
            submission = student_submission_context(db, '学校A', 'g1')
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        template = env.from_string(env.loader.get_source(env, 'group_report.html')[0].replace("{% extends 'base.html' %}", ''))
        html = template.render(request=SimpleNamespace(session={'user_id': 'u1'}), group={'id': 'g1', 'name': '班1', 'kind': 'group'},
                               state={'template': TEMPLATE}, submission=submission, csrf_token='x')
        self.assertIn('提出し直す' if submission['targets'][0]['submitted_at'] else '>提出<', html)
        self.assertIn('再提出が必要です', html)
        self.assertIn('レビューした先生：t1', html)


if __name__ == '__main__':
    unittest.main()
