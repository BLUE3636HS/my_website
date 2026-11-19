"""ワークスペースの枠（作成・削除・所属の変更・先生の画面の絞り込み）のテスト。"""
import asyncio
import datetime
import io
import sqlite3
import unittest
import zipfile
from contextlib import closing
from types import SimpleNamespace

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

import test_report_reviews as rr
from group_reports import build_group_report_router
from group_reports import run_scheduled_snapshots
from report_reviews import (
    acquire_review_lock, return_review,
    build_report_review_router, build_round_zip, open_targets, round_groups, round_summaries, run_report_deadlines,
    run_report_reminders, school_groups, submit_report,
)
from studies import add_frame_to_report_days, initialize_studies
from template_management import SnapshotDays, create_template_router, snapshot_today
from workspace_frames import (
    build_workspace_frame_router, create_frame, delete_frame, ensure_frame_schema, frame_filter, school_frames,
    set_workspace_frame, update_frame,
)


class FrameBase(rr.Base):
    def setUp(self):
        super().setUp()
        with self.db() as db, db:
            ensure_frame_schema(db)  # テスト用のグループ表は後から作るので、列をここで足す
            self.f1 = create_frame(db, '学校A', '2026年度 2年生 理数科', '課題研究', 'boss')['id']
            self.f2 = create_frame(db, '学校A', '2026年度 1年生', '', 'boss')['id']
            self.fb = create_frame(db, '学校B', 'B校の枠', '', 'boss')['id']
            set_workspace_frame(db, '学校A', 'g1', self.f1)
            set_workspace_frame(db, '学校A', 'p3', self.f1)

    def add_frame_round(self, day, frame_key, title='中間成果レポート'):
        with self.db() as db, db:
            db.execute("INSERT INTO group_report_snapshot_day(school,frame_id,day,created_by,created_at) VALUES('学校A',?,?,'boss','now')", (frame_key, day))
            db.execute("INSERT INTO group_report_submission_day(school,frame_id,day,title,created_by,created_at) VALUES('学校A',?,?,?,'boss','now')",
                       (frame_key, day, title))


class FrameModelTests(FrameBase):
    def test_existing_workspaces_start_without_frame_and_schema_is_idempotent(self):
        with self.db() as db, db:
            ensure_frame_schema(db)
            ensure_frame_schema(db)
            self.assertIsNone(db.execute("SELECT frame_id FROM tekne_chat_group WHERE id='g2'").fetchone()[0])

    def test_names_are_validated_and_unique_per_school(self):
        with self.db() as db, db:
            for bad in ('', '   ', 'x' * 51, 'a\x01b'):
                with self.assertRaises(HTTPException) as caught:
                    create_frame(db, '学校A', bad, '', 'boss')
                self.assertEqual(caught.exception.status_code, 422)
            with self.assertRaises(HTTPException) as caught:
                create_frame(db, '学校A', '2026年度　2年生  理数科', '', 'boss')  # 空白の違いだけなら同じ名前
            self.assertEqual(caught.exception.status_code, 409)
            create_frame(db, '学校B', '2026年度 2年生 理数科', '', 'boss')  # 別の学校なら同じ名前でもよい
            with self.assertRaises(HTTPException) as caught:
                create_frame(db, '学校A', '新しい枠', 'x' * 201, 'boss')
            self.assertEqual(caught.exception.status_code, 422)
            update_frame(db, '学校A', self.f2, '2026年度 1年生 普通科', '説明')
            with self.assertRaises(HTTPException):
                update_frame(db, '学校A', self.f2, '2026年度 2年生 理数科', '')
            with self.assertRaises(HTTPException):
                update_frame(db, '学校A', self.fb, '他校の枠を変更', '')
        with self.db() as db:
            frames = school_frames(db, '学校A', with_counts=True)
        self.assertEqual([(f['name'], f['count']) for f in frames], [('2026年度 1年生 普通科', 0), ('2026年度 2年生 理数科', 2)])

    def test_cannot_use_other_schools_frame_or_workspace(self):
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                set_workspace_frame(db, '学校A', 'g2', self.fb)
            self.assertEqual(caught.exception.status_code, 422)
            with self.assertRaises(HTTPException) as caught:
                set_workspace_frame(db, '学校A', 'g9', self.f1)
            self.assertEqual(caught.exception.status_code, 404)

    def test_deleting_frame_returns_workspaces_to_none(self):
        with self.db() as db, db:
            result = delete_frame(db, '学校A', self.f1)
            self.assertEqual(result['moved'], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tekne_chat_group WHERE frame_id IS NOT NULL").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tekne_chat_group").fetchone()[0], 4)
            with self.assertRaises(HTTPException):
                delete_frame(db, '学校A', self.fb)

    def test_filter_parsing_and_group_filtering(self):
        with self.db() as db:
            self.assertEqual(frame_filter(db, '学校A', 'bogus')['value'], 'all')
            self.assertEqual(frame_filter(db, '学校A', str(self.fb))['value'], 'all')  # 他校の枠は使えない
            selected = frame_filter(db, '学校A', str(self.f1))
            self.assertEqual((selected['label'], selected['query']), ('2026年度 2年生 理数科', f'?frame={self.f1}'))
            self.assertEqual([o['label'] for o in selected['options']], ['すべての枠', '2026年度 1年生', '2026年度 2年生 理数科', '枠なし'])
            self.assertEqual({g['id'] for g in school_groups(db, '学校A', str(self.f1))}, {'g1', 'p3'})
            self.assertEqual({g['id'] for g in school_groups(db, '学校A', 'none')}, {'g2'})
            self.assertEqual({g['id'] for g in school_groups(db, '学校A', str(self.f2))}, set())
            self.assertEqual({g['id'] for g in school_groups(db, '学校A')}, {'g1', 'g2', 'p3'})

    def test_frame_with_days_cannot_be_deleted(self):
        self.add_frame_round('2026-11-20', self.f2)
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                delete_frame(db, '学校A', self.f2)
            self.assertEqual(caught.exception.status_code, 409)
            db.execute('DELETE FROM group_report_submission_day')
            with self.assertRaises(HTTPException):
                delete_frame(db, '学校A', self.f2)  # 自動保存日だけでも削除できない
            db.execute('DELETE FROM group_report_snapshot_day')
            delete_frame(db, '学校A', self.f2)


class FrameDayTests(FrameBase):
    """自動保存日・提出日は枠ごと（0＝枠なし）。"""
    day = (snapshot_today() - datetime.timedelta(days=2)).isoformat()

    def close(self):
        return self.close_round(self.day, rr.at(snapshot_today().isoformat(), 1))

    def subs(self):
        with self.db() as db:
            return {(r[0], r[1]): r[2] for r in db.execute(
                'SELECT group_id,round_frame,method FROM group_report_submission WHERE round_day=?', (self.day,))}

    def test_old_day_tables_are_migrated_to_no_frame(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript("""
                CREATE TABLE group_report_snapshot_day(school TEXT NOT NULL, day TEXT NOT NULL, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, processed_at TEXT, PRIMARY KEY(school,day));
                CREATE TABLE group_report_submission_day(school TEXT NOT NULL, day TEXT NOT NULL, title TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL, finalized_at TEXT, PRIMARY KEY(school,day));
                CREATE TABLE group_report_submission(id INTEGER PRIMARY KEY, group_id TEXT);
                INSERT INTO group_report_snapshot_day VALUES('学校A','2026-11-20','boss','now',NULL);
                INSERT INTO group_report_submission_day VALUES('学校A','2026-11-20','中間','boss','now',NULL);
                INSERT INTO group_report_submission VALUES(1,'g1');""")
            add_frame_to_report_days(db)
            add_frame_to_report_days(db)
            self.assertEqual(db.execute('SELECT school,day,frame_id FROM group_report_snapshot_day').fetchall(), [('学校A', '2026-11-20', 0)])
            self.assertEqual(db.execute('SELECT title,frame_id FROM group_report_submission_day').fetchall(), [('中間', 0)])
            self.assertEqual(db.execute('SELECT round_frame FROM group_report_submission').fetchall(), [(0,)])
            # 同じ日でも枠が違えば別の日として登録できる
            db.execute("INSERT INTO group_report_snapshot_day(school,day,created_by,created_at,frame_id) VALUES('学校A','2026-11-20','boss','now',3)")

    def test_snapshot_and_deadline_only_touch_workspaces_in_the_frame(self):
        self.add_frame_round(self.day, self.f1)
        self.close()
        self.assertEqual(self.subs(), {('g1', self.f1): 'auto'})  # p3 は下書きがない。g2 は枠なしなので対象外
        with self.db() as db:
            scheduled = {r[0] for r in db.execute("SELECT group_id FROM group_report_version WHERE kind='scheduled'")}
        self.assertEqual(scheduled, {'g1'})

    def test_no_frame_days_cover_workspaces_without_frame(self):
        self.add_frame_round(self.day, 0)
        self.close()
        self.assertEqual(self.subs(), {('g2', 0): 'auto'})

    def test_students_submit_to_their_current_frames_round(self):
        self.add_frame_round('2026-11-20', self.f1, 'A枠の中間')
        self.add_frame_round('2026-11-25', 0, '枠なしの中間')
        with self.db() as db:
            self.assertEqual([t['title'] for t in open_targets(db, '学校A', 'g1', datetime.date(2026, 11, 18))], ['A枠の中間'])
            self.assertEqual([t['title'] for t in open_targets(db, '学校A', 'g2', datetime.date(2026, 11, 18))], ['枠なしの中間'])
        with self.assertRaises(HTTPException):
            self.submit('g2', '2026-11-20')  # 枠なしのワークスペースは A枠の提出日に提出できない

    def test_submission_stays_in_old_round_after_frame_change(self):
        self.add_frame_round('2026-11-20', self.f1)
        self.submit('g1', '2026-11-20', now=rr.at('2026-11-18'))
        with self.db() as db, db:
            set_workspace_frame(db, '学校A', 'g1', None)  # 期限前に「枠なし」へ移る
        run_scheduled_snapshots(self.path, rr.at('2026-11-21', 1))
        run_report_deadlines(self.path, rr.at('2026-11-21', 1))
        with self.db() as db:
            row = db.execute("SELECT round_frame,method,finalized_at FROM group_report_submission WHERE group_id='g1'").fetchone()
            groups = {g['id']: g for g in round_groups(db, '学校A', self.f1, '2026-11-20', True)}
        self.assertEqual(row[:2], (self.f1, 'manual'))
        self.assertIsNotNone(row[2])
        self.assertEqual(set(groups), {'g1', 'p3'})
        self.assertTrue(groups['g1']['moved'])
        self.assertFalse(groups['p3']['moved'])

    def test_resubmission_stays_in_the_original_frames_round(self):
        self.add_frame_round('2026-11-20', self.f1)
        self.submit('g1', '2026-11-20', now=rr.at('2026-11-18'))
        sid = self.sub_id('g1', '2026-11-20')
        self.call(acquire_review_lock, sid, '学校A', 't1')
        self.call(return_review, sid, '学校A', 't1', '直して', {}, 'resubmit', '2026-11-25', now=rr.at('2026-11-19'))
        self.submit('g1', '2026-11-20', attempt=1, now=rr.at('2026-11-19'))
        with self.db() as db:
            frames = db.execute('SELECT attempt,round_frame FROM group_report_submission WHERE group_id=? ORDER BY attempt', ('g1',)).fetchall()
            groups = {g['id']: g for g in round_groups(db, '学校A', self.f1, '2026-11-20', False)}
        self.assertEqual(frames, [(0, self.f1), (1, self.f1)])
        self.assertEqual([(a['attempt'], a['reviewable']) for a in groups['g1']['attempts']], [(0, True), (1, True)])  # 再提出もレビューできる

    def test_migration_moves_resubmission_rows_to_the_original_frame(self):
        self.add_frame_round('2026-11-20', self.f1)
        self.submit('g1', '2026-11-20', now=rr.at('2026-11-18'))
        with self.db() as db, db:
            db.execute("INSERT INTO group_report_submission(school,group_id,round_day,attempt,due_day,created_at,round_frame) VALUES('学校A','g1','2026-11-20',1,'2026-11-25','now',0)")
            initialize_studies(db)
            initialize_studies(db)  # 何度実行しても同じ
            self.assertEqual(db.execute('SELECT attempt,round_frame FROM group_report_submission ORDER BY attempt').fetchall(), [(0, self.f1), (1, self.f1)])

    def test_reminders_follow_each_frames_days(self):
        self.add_frame_round('2026-11-20', self.f1)
        sent = run_report_reminders(self.path, rr.at('2026-11-17'))
        self.assertEqual(sent, 2)  # A枠の g1 と p3 だけ（g2 は枠なし）
        with self.db() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT group_id FROM group_report_reminder_sent')}, {'g1', 'p3'})

    def test_round_list_zip_and_counts_per_frame(self):
        self.add_frame_round(self.day, self.f1, 'A枠の回')
        self.add_frame_round(self.day, 0, '枠なしの回')
        self.close()
        with self.db() as db:
            rounds = round_summaries(db, '学校A', snapshot_today())
            self.assertEqual(sorted((r['frame_label'], r['title'], r['total']) for r in rounds),
                             [('2026年度 2年生 理数科', 'A枠の回', 2), ('枠なし', '枠なしの回', 1)])
            self.assertEqual([r['title'] for r in round_summaries(db, '学校A', snapshot_today(), 'none')], ['枠なしの回'])
            self.assertEqual([r['title'] for r in round_summaries(db, '学校A', snapshot_today(), str(self.f2))], [])
            self.assertEqual([g['id'] for g in round_groups(db, '学校A', 0, self.day, True)], ['g2'])
            data, count = build_round_zip(db, self.path, self.image_dir.name, '学校A', self.f1, self.day)
        self.assertEqual(count, 1)  # p3 は下書きがないので提出版がない
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertIn('班1', names[0])

    def test_settings_are_saved_per_frame(self):
        router = create_template_router(lambda: self.path, SimpleNamespace(TemplateResponse=lambda **kw: kw))
        save = next(r.endpoint for r in router.routes if r.path == '/teacher/school-report/snapshot-days' and 'POST' in r.methods)
        page = next(r.endpoint for r in router.routes if r.path == '/teacher/school-report/snapshot-days' and 'GET' in r.methods)
        request = rr.FakeRequest(session={'teacher_login': True, 'teacher_id': 'boss', 'school_report_template_csrf': 'tok'})
        future = (snapshot_today() + datetime.timedelta(days=5)).isoformat()
        later = (snapshot_today() + datetime.timedelta(days=9)).isoformat()
        asyncio.run(save(request, SnapshotDays(csrf_token='tok', days=[future], submissions={future: 'A枠の提出'}, frame=str(self.f1))))
        result = asyncio.run(save(request, SnapshotDays(csrf_token='tok', days=[later], frame='none')))
        self.assertEqual([d['day'] for d in result['days']], [later])
        with self.db() as db:
            rows = sorted(db.execute('SELECT frame_id,day FROM group_report_snapshot_day').fetchall())
            titles = db.execute('SELECT frame_id,title FROM group_report_submission_day').fetchall()
        self.assertEqual(rows, sorted([(self.f1, future), (0, later)]))
        self.assertEqual(titles, [(self.f1, 'A枠の提出')])
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(save(request, SnapshotDays(csrf_token='tok', days=[future], frame=str(self.fb))))  # 他校の枠
        self.assertEqual(caught.exception.status_code, 422)
        shown = asyncio.run(page(request, str(self.f1)))['context']
        self.assertEqual(shown['frame']['label'], '2026年度 2年生 理数科')
        self.assertEqual([d['title'] for d in shown['days']], ['A枠の提出'])
        self.assertEqual([o['label'] for o in shown['frame_options']], ['枠なし', '2026年度 1年生', '2026年度 2年生 理数科'])


class FrameRouterTests(FrameBase):
    def setUp(self):
        super().setUp()
        env = Environment(loader=FileSystemLoader(str(rr.TEMPLATES_DIR)), autoescape=True)

        def render(request, name, context):
            return {'name': name, 'context': context, 'html': env.get_template(name).render(**context)}
        self.templates = SimpleNamespace(TemplateResponse=render)
        router = build_workspace_frame_router(self.path, self.templates)
        self.routes = {(r.path, sorted(r.methods)[0]): r.endpoint for r in router.routes}

    def request(self, teacher='boss', body=None, token='tok', path='/teacher/workspace-frames'):
        return rr.FakeRequest(session={'teacher_login': True, 'teacher_id': teacher, 'workspace_frame_csrf': token,
                                       'report_review_csrf': token},
                              url=SimpleNamespace(path=path), body=body or {})

    def test_only_super_teacher_can_manage(self):
        page = asyncio.run(self.routes[('/teacher/workspace-frames', 'GET')](self.request()))
        self.assertIn('2026年度 2年生 理数科', page['html'])
        self.assertIn('ワークスペースの所属', page['html'])
        self.assertIn('href="/teacher/workspace-frames/new"', page['html'])
        self.assertIn('<span class="ws-members-track">生徒1、生徒2</span>', page['html'])  # グループはメンバー名を出す
        self.assertEqual(page['html'].count('class="ws-members"'), 2)  # 個人スペースには出さない（班1・班2のみ）
        self.assertIn('枠の一覧（2）', page['html'])
        page = asyncio.run(self.routes[('/teacher/workspace-frames/list', 'GET')](self.request()))
        self.assertIn('2 ワークスペース', page['html'])
        self.assertIn('課題研究', page['html'])
        page = asyncio.run(self.routes[('/teacher/workspace-frames/new', 'GET')](self.request()))
        self.assertIn('id="frame-create"', page['html'])
        for endpoint, args in [(self.routes[('/teacher/workspace-frames', 'GET')], ()),
                               (self.routes[('/teacher/workspace-frames/new', 'GET')], ()),
                               (self.routes[('/teacher/workspace-frames/list', 'GET')], ()),
                               (self.routes[('/teacher/api/workspace-frames', 'POST')], ()),
                               (self.routes[('/teacher/api/workspaces/{group_id}/frame', 'POST')], ('g2',))]:
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(endpoint(self.request('t1', body={'csrf_token': 'tok', 'name': 'x'}), *args))
            self.assertEqual(caught.exception.status_code, 403)

    def test_api_checks_csrf_and_changes_frames(self):
        create = self.routes[('/teacher/api/workspace-frames', 'POST')]
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(create(self.request(body={'csrf_token': 'bad', 'name': '新しい枠'})))
        self.assertEqual(caught.exception.status_code, 403)
        created = asyncio.run(create(self.request(body={'csrf_token': 'tok', 'name': '新しい枠', 'description': '説明'})))
        move = self.routes[('/teacher/api/workspaces/{group_id}/frame', 'POST')]
        self.assertEqual(asyncio.run(move(self.request(body={'csrf_token': 'tok', 'frame_id': created['id']}), 'g2'))['frame_name'], '新しい枠')
        self.assertIsNone(asyncio.run(move(self.request(body={'csrf_token': 'tok', 'frame_id': None}), 'g1'))['frame_id'])
        update = self.routes[('/teacher/api/workspace-frames/{frame_id}', 'POST')]
        asyncio.run(update(self.request(body={'csrf_token': 'tok', 'name': '改名', 'description': ''}), created['id']))
        remove = self.routes[('/teacher/api/workspace-frames/{frame_id}/delete', 'POST')]
        self.assertEqual(asyncio.run(remove(self.request(body={'csrf_token': 'tok'}), created['id']))['moved'], 1)

    def test_teacher_pages_show_filter_for_every_teacher(self):
        day = (snapshot_today() - datetime.timedelta(days=2)).isoformat()
        self.add_frame_round(day, self.f1)
        self.add_frame_round(day, 0)
        self.close_round(day, rr.at(snapshot_today().isoformat(), 1))
        review = build_report_review_router(self.path, self.templates, None, self.image_dir.name)
        routes = {(r.path, sorted(r.methods)[0]): r.endpoint for r in review.routes}
        redirect = asyncio.run(routes[('/teacher/report-reviews', 'GET')](self.request('t1'), str(self.f1)))
        self.assertEqual(redirect.headers['location'], f'/teacher/reports?tab=submissions&frame={self.f1}')  # 旧URLは統合画面へ
        reports = build_group_report_router(self.path, self.templates, None, self.image_dir.name)
        teacher_reports = next(r.endpoint for r in reports.routes if r.path == '/teacher/reports')
        page = asyncio.run(teacher_reports(self.request('t1'), str(self.f1), 'submissions'))
        self.assertIn(f'/teacher/report-reviews/rounds/{self.f1}/{day}?frame={self.f1}', page['html'])
        self.assertNotIn(f'/teacher/report-reviews/rounds/0/{day}', page['html'])
        self.assertIn('対象 2', page['html'])
        self.assertNotIn('枠を管理', page['html'])  # 管理ページへのリンクは super_teacher だけ
        page = asyncio.run(routes[('/teacher/report-reviews/rounds/{frame_key}/{day}', 'GET')](self.request('t1'), 0, day, 'all'))
        self.assertIn('班2', page['html'])
        self.assertNotIn('班1', page['html'])
        self.assertIn(f'/rounds/0/{day}/download.zip', page['html'])
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(routes[('/teacher/report-reviews/rounds/{frame_key}/{day}', 'GET')](self.request('t1'), self.f2, day, 'all'))
        self.assertEqual(caught.exception.status_code, 404)
        page = asyncio.run(teacher_reports(self.request('boss'), str(self.f1)))
        self.assertEqual([g['id'] for g in page['context']['groups']], ['g1'])
        self.assertIn('枠を管理', page['html'])
        page = asyncio.run(teacher_reports(self.request('boss'), 'all'))
        self.assertEqual({g['id'] for g in page['context']['groups']}, {'g1', 'g2'})


try:
    import test_studies as fixtures
except Exception:  # main を読み込めない環境では、生徒側のテストを飛ばす
    fixtures = None


@unittest.skipIf(fixtures is None, 'main を読み込めません')
class StudentFrameTests(unittest.TestCase):
    def setUp(self):
        fixtures.StudyTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.frame = create_frame(db, 'school1', '2026年度 2年生 理数科', '課題研究', 'teacher1')['id']
            self.other = create_frame(db, 'school2', '他校', '', 'x')['id']
        self.session_c = {'user_login': True, 'user_id': 'c', 'user_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                          'tekne_chat_session': fixtures.create_chat_session(self.path, 'c')}

    def test_students_see_frames_and_can_create_with_or_without_frame(self):
        self.assertEqual([f['name'] for f in self.chat.frames(self.session)], ['2026年度 2年生 理数科'])
        plain = self.chat.create_group(self.session, '枠なしの班')
        framed = self.chat.create_group(self.session, '理数の班', 'group', str(self.frame))
        self.assertIsNone(plain['frame_id'])
        self.assertEqual(framed['frame_name'], '2026年度 2年生 理数科')
        listed = {g['name']: g['frame_name'] for g in self.chat.groups(self.session)}
        self.assertEqual(listed, {'枠なしの班': None, '理数の班': '2026年度 2年生 理数科'})
        with self.assertRaises(HTTPException) as caught:
            self.chat.create_group(self.session, '他校の枠', 'group', str(self.other))
        self.assertEqual(caught.exception.status_code, 422)

    def test_any_member_can_change_frame_any_time_but_not_outsiders(self):
        group = self.chat.create_group(self.session, '1班')
        self.chat.invite(self.session, group['id'], 'c')
        self.assertEqual(self.chat.set_group_frame(self.session_c, group['id'], str(self.frame))['frame_id'], self.frame)
        self.assertIsNone(self.chat.set_group_frame(self.session, group['id'], '')['frame_id'])
        personal = self.chat.create_group(self.session, 'メモ', 'personal')
        with self.assertRaises(HTTPException) as caught:
            self.chat.set_group_frame(self.session_c, personal['id'], str(self.frame))
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            self.chat.set_group_frame(self.session, group['id'], str(self.other))
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == '__main__':
    unittest.main()
