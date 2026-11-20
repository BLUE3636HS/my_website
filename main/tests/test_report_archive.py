"""成果レポートのアーカイブ（保存・検索・閲覧・削除・テーマ必須）のテスト。"""
import asyncio
import datetime
import json
import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

from report_archive import (archive_detail, archive_facets, archive_many, archive_pdf, archive_submission, archive_zip,
                            archive_round, build_report_archive_router, delete_archive, fiscal_range, fiscal_year, normalize_search,
                            parse_filters, round_archive_status, round_latest, search_archives)
from report_reviews import acquire_review_lock, return_review
from studies import (THEME_KEY, default_theme_field, ensure_theme_field, find_theme_field, initialize_studies,
                     migrate_theme_fields, theme_value)
from workspace_frames import ensure_frame_schema
from template_management import SchoolReportTemplate, TemplateCreate
from test_report_reviews import TEMPLATES_DIR, Base, FakeRequest, at

DAY = '2026-11-20'


class ArchiveBase(Base):
    def setUp(self):
        super().setUp()
        with self.db() as db, db:
            db.execute('ALTER TABLE student ADD COLUMN school TEXT')
            db.execute("UPDATE student SET school='学校A' WHERE id IN ('u1','u2','u3')")
            db.execute("UPDATE student SET school='学校B' WHERE id='u9'")
        self.add_round(DAY)
        self.close_round(DAY, at('2026-11-21', 0, 5))
        self.sid = self.sub_id('g1', DAY)
        self.sid2 = self.sub_id('g2', DAY)

    def filters(self, **kwargs):
        return parse_filters(**kwargs)

    def archive(self, submission_id=None, actor='boss'):
        with self.db() as db, db:
            return archive_submission(db, '学校A', submission_id or self.sid, actor)


class SaveTests(ArchiveBase):
    def test_archive_copies_everything_and_survives_deletions(self):
        with self.db() as db, db:
            ensure_frame_schema(db)
            db.execute("INSERT INTO workspace_frame(id,school,name,description,created_by,created_at,updated_at) VALUES(1,'学校A','探究枠','説明文','boss','now','now')")
            db.execute('UPDATE tekne_chat_group SET frame_id=1 WHERE id=?', ('g1',))
            db.execute('UPDATE group_report_submission SET round_frame=0')
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        self.call(return_review, self.sid, '学校A', 't1', 'よくできました', {'a': 'テーマが良い'}, 'returned', now=at('2026-11-22'))
        archive_id = self.archive()
        with self.db() as db:
            detail = archive_detail(db, archive_id, '/teacher/report-archive', '学校A')
        self.assertEqual(detail['theme'], 'g1テーマ')
        self.assertEqual(detail['group_name'], '班1')
        self.assertEqual(detail['round_title'], '中間成果レポート')
        self.assertEqual(detail['archived_by'], 'boss')
        self.assertEqual([m['name'] for m in detail['members']], ['生徒1', '生徒2'])
        self.assertEqual(detail['review_overall'], 'よくできました')
        self.assertEqual(detail['fields'][0]['comment'], 'テーマが良い')
        self.assertRegex(detail['archived_label'], r'^\d{4}/\d{2}/\d{2}$')
        # グループも枠も提出も消えた・名前が変わった後でも、保存した時点の内容が残る
        with self.db() as db, db:
            db.execute("DELETE FROM tekne_chat_member WHERE group_id='g1'")
            db.execute("DELETE FROM tekne_chat_group WHERE id='g1'")
            db.execute('DELETE FROM workspace_frame')
            db.execute('DELETE FROM group_report_review')
            db.execute('DELETE FROM group_report_submission')
            db.execute('DELETE FROM group_report_version')
        with self.db() as db:
            detail = archive_detail(db, archive_id, '/teacher/report-archive', '学校A')
            self.assertEqual((detail['group_name'], detail['theme'], len(detail['members'])), ('班1', 'g1テーマ', 2))
            self.assertEqual(search_archives(db, '学校A', self.filters(q='生徒2'))['total'], 1)

    def test_empty_theme_can_be_archived(self):
        with self.db() as db, db:
            db.execute("UPDATE group_report_version SET report_snapshot=? WHERE group_id='g2'",
                       (json.dumps({'a': {'value': '', 'images': []}}),))
        archive_id = self.archive(self.sid2)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT theme FROM report_archive WHERE id=?', (archive_id,)).fetchone()[0], '')
            self.assertEqual(search_archives(db, '学校A', self.filters())['rows'][0]['theme'], '')

    def test_duplicate_other_school_and_unsubmitted_are_rejected(self):
        self.archive()
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                archive_submission(db, '学校A', self.sid, 'boss')
        self.assertEqual(caught.exception.status_code, 409)
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                archive_submission(db, '学校B', self.sid2, 'boss')
        self.assertEqual(caught.exception.status_code, 404)
        with self.db() as db, db:
            db.execute('UPDATE group_report_submission SET finalized_at=NULL WHERE id=?', (self.sid2,))
            with self.assertRaises(HTTPException) as caught:
                archive_submission(db, '学校A', self.sid2, 'boss')
        self.assertEqual(caught.exception.status_code, 409)

    def test_archive_many_skips_saved_and_reports_counts(self):
        self.archive()
        with self.db() as db, db:
            result = archive_many(db, '学校A', [self.sid, self.sid2, 99999], 'boss')
        self.assertEqual(result, {'saved': 1, 'skipped': 2})

    def test_images_are_referenced_not_copied(self):
        digest = 'a' * 64
        report = {'a': {'value': 'x', 'images': []}, 'c': {'value': '', 'images': [{'content_hash': digest, 'caption': '図1'}]}}
        with self.db() as db, db:
            db.execute("UPDATE group_report_version SET report_snapshot=? WHERE group_id='g1'", (json.dumps(report),))
        archive_id = self.archive()
        with self.db() as db:
            self.assertEqual(db.execute('SELECT content_hash FROM report_archive_asset WHERE archive_id=?', (archive_id,)).fetchall(), [(digest,)])
            self.assertEqual(search_archives(db, '学校A', self.filters(q='図1'))['total'], 1)

    def test_pdf_and_zip_are_built_from_the_archive(self):
        archive_id = self.archive()
        with self.db() as db:
            archive = {**dict(zip([c[0] for c in db.execute('SELECT * FROM report_archive WHERE id=?', (archive_id,)).description],
                                  db.execute('SELECT * FROM report_archive WHERE id=?', (archive_id,)).fetchone()))}
            pdf = archive_pdf(db, self.image_dir.name, archive)
            data, count = archive_zip(db, self.image_dir.name, '学校A', self.filters())
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(count, 1)


class SearchTests(ArchiveBase):
    def setUp(self):
        super().setUp()
        self.add_round('2027-02-10', '最終成果レポート')
        self.submit('g1', '2027-02-10', now=at('2027-02-05'))
        self.add_round('2026-03-01', '前年度の成果')
        self.a1 = self.archive()
        self.a2 = self.archive(self.sid2)
        self.a3 = self.archive(self.sub_id('g1', '2027-02-10'))

    def total(self, **kwargs):
        with self.db() as db:
            return search_archives(db, '学校A', self.filters(**kwargs))['total']

    def test_keyword_matches_theme_names_furigana_and_is_and_search(self):
        self.assertEqual(self.total(q='g1テーマ'), 2)
        self.assertEqual(self.total(q='生徒1'), 2)
        self.assertEqual(self.total(q='ｾｲﾄ1'), 2)  # 半角カタカナ・ひらがな・カタカナの違いを無視
        self.assertEqual(self.total(q='せいと3'), 1)
        self.assertEqual(self.total(q='生徒1 g2'), 0)  # すべてを含むものだけ
        self.assertEqual(self.total(q='存在しない語'), 0)
        self.assertEqual(self.total(q='G1テーマ'), 2)

    def test_fiscal_year_and_date_range_and_frame_and_title(self):
        self.assertEqual(self.total(fy='2026'), 3)  # 2026-11-20 と 2027-02-10 は2026年度（4月始まり）
        self.assertEqual(self.total(fy='2025'), 0)
        self.assertEqual(self.total(date_from='2027-01-01'), 1)
        self.assertEqual(self.total(date_to='2026-12-31'), 2)
        self.assertEqual(self.total(date_from='2026-11-20', date_to='2026-11-20'), 2)
        self.assertEqual(self.total(title='最終成果レポート'), 1)
        self.assertEqual(self.total(frame='枠なし'), 3)
        self.assertEqual(self.total(frame='別の枠'), 0)
        self.assertEqual(self.total(date_from='不正な日付'), 3)  # 不正な日付は無視する
        self.assertEqual((fiscal_year('2027-03-31'), fiscal_year('2027-04-01')), (2026, 2027))
        self.assertEqual(fiscal_range(2026), ('2026-04-01', '2027-03-31'))

    def test_facets_list_past_and_current_years_and_results_are_newest_first(self):
        with self.db() as db:
            facets = archive_facets(db, '学校A')
            items = search_archives(db, '学校A', self.filters())['rows']
        self.assertEqual(facets['years'], [2026])
        self.assertEqual(sorted(facets['titles']), ['中間成果レポート', '最終成果レポート'])
        self.assertEqual([i['round_day'] for i in items], sorted((i['round_day'] for i in items), reverse=True))

    def test_pagination(self):
        with self.db() as db:
            first = search_archives(db, '学校A', self.filters(page='1'), per_page=2)
            second = search_archives(db, '学校A', self.filters(page='2'), per_page=2)
            beyond = search_archives(db, '学校A', self.filters(page='99'), per_page=2)
        self.assertEqual((len(first['rows']), len(second['rows']), first['pages']), (2, 1, 2))
        self.assertEqual(beyond['page'], 2)

    def test_other_school_is_never_listed(self):
        with self.db() as db:
            self.assertEqual(search_archives(db, '学校B', self.filters())['total'], 0)
            self.assertEqual(search_archives(db, None, self.filters())['total'], 3)  # super admin は全校
            self.assertEqual(search_archives(db, None, self.filters(school='学校B'))['total'], 0)
            self.assertEqual(archive_facets(db, None)['schools'], ['学校A'])

    def test_normalize_search(self):
        self.assertEqual(normalize_search('ＡＢＣ　テスト'), 'abc てすと')


class DeleteTests(ArchiveBase):
    def test_confirm_word_is_required_and_a_log_is_left(self):
        archive_id = self.archive()
        for wrong in (None, '', 'del', 'remove'):
            with self.db() as db, db:
                with self.assertRaises(HTTPException) as caught:
                    delete_archive(db, archive_id, 'boss', 'super_teacher', wrong, school='学校A')
            self.assertEqual(caught.exception.status_code, 422)
        with self.db() as db, db:
            with self.assertRaises(HTTPException) as caught:
                delete_archive(db, archive_id, 'boss', 'super_teacher', 'delete', school='学校B')  # 他校の先生は消せない
        self.assertEqual(caught.exception.status_code, 404)
        with self.db() as db, db:
            delete_archive(db, archive_id, 'boss', 'super_teacher', ' Delete ', school='学校A')
        with self.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM report_archive').fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM report_archive_asset').fetchone())
            log = db.execute('SELECT school,archive_id,group_name,round_title,deleted_by,deleted_role FROM report_archive_deletion_log').fetchone()
        self.assertEqual(log, ('学校A', archive_id, '班1', '中間成果レポート', 'boss', 'super_teacher'))

    def test_super_admin_can_delete_any_school(self):
        archive_id = self.archive()
        with self.db() as db, db:
            delete_archive(db, archive_id, 'root', 'super_admin', 'delete')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT deleted_role FROM report_archive_deletion_log').fetchone()[0], 'super_admin')


class RouterTests(ArchiveBase):
    def setUp(self):
        super().setUp()
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        defaults = {'student_features': {'report_archive': True, 'community': True,
                                          'dashboard': True}, 'current_path': '/', 'unread_notification_count': 0,
                    'is_super_admin': True, 'admin_id': 'root', 'mentor_inquiry_unread_count': 0}

        def render(request, name, context):
            return {'name': name, 'context': context, 'html': env.get_template(name).render(**{**defaults, **context})}
        self.admin_calls = []

        def require_super_admin(db, request):
            if request.session.get('admin_role') != 'super_admin':
                raise HTTPException(403, '最上位管理者の権限が必要です。')
            return ('root', '管理者', 'super_admin')
        router = build_report_archive_router(self.path, SimpleNamespace(TemplateResponse=render), self.image_dir.name, require_super_admin)
        self.routes = {(r.path, tuple(sorted(r.methods))[0]): r.endpoint for r in router.routes}

    def teacher(self, who='boss', body=None, query=None, token='tok'):
        return FakeRequest(session={'teacher_login': True, 'teacher_id': who, 'report_archive_csrf': token},
                           url=SimpleNamespace(path='/teacher/report-archive'), body=body or {}, query_params=query or {})

    def student(self, who='u1', query=None):
        return FakeRequest(session={'user_login': True, 'user_id': who}, url=SimpleNamespace(path='/report-archive'),
                           body={}, query_params=query or {})

    def admin(self, role='super_admin', body=None, token='tok'):
        return FakeRequest(session={'admin_login': True, 'admin_role': role, 'report_archive_admin_csrf': token},
                           url=SimpleNamespace(path='/admin/report-archive'), body=body or {}, query_params={})

    def run_route(self, path, method, request, *args):
        return asyncio.run(self.routes[(path, method)](request, *args))

    def test_only_super_teacher_can_save_and_the_whole_round_is_saved_automatically(self):
        body = {'csrf_token': 'tok', 'frame_key': 0, 'day': DAY}
        for who in ('t1', 'tb'):
            with self.assertRaises(HTTPException) as caught:
                self.run_route('/teacher/api/report-archive', 'POST', self.teacher(who, body))
            self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            self.run_route('/teacher/api/report-archive', 'POST', self.teacher('boss', {**body, 'csrf_token': 'bad'}))
        self.assertEqual(caught.exception.status_code, 403)
        for bad in ({'frame_key': 'x', 'day': DAY}, {'frame_key': True, 'day': DAY}, {'frame_key': 0, 'day': 5}, {}):
            with self.assertRaises(HTTPException) as caught:
                self.run_route('/teacher/api/report-archive', 'POST', self.teacher('boss', {'csrf_token': 'tok', **bad}))
            self.assertEqual(caught.exception.status_code, 422)
        result = self.run_route('/teacher/api/report-archive', 'POST', self.teacher('boss', body))
        self.assertEqual((result['saved'], result['already']), (2, 0))  # 選ばなくても、提出のあったグループが全部保存される
        with self.assertRaises(HTTPException) as caught:  # もう保存できるものがない
            self.run_route('/teacher/api/report-archive', 'POST', self.teacher('boss', body))
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(HTTPException) as caught:
            self.run_route('/teacher/api/report-archive', 'POST', self.teacher('boss', {**body, 'day': '2030-01-01'}))
        self.assertEqual(caught.exception.status_code, 404)

    def test_round_save_takes_only_the_latest_attempt_of_each_group(self):
        self.call(acquire_review_lock, self.sid, '学校A', 't1')
        self.call(return_review, self.sid, '学校A', 't1', '直して', {}, 'resubmit', '2026-12-25', now=at('2026-11-22'))
        with self.db() as db:
            status = round_archive_status(db, '学校A', 0, DAY)
        self.assertEqual(status['waiting'], 1)  # 班1は再提出待ち
        self.submit('g1', DAY, attempt=1, now=at('2026-11-23'))
        with self.db() as db:
            status = round_archive_status(db, '学校A', 0, DAY)
            latest = round_latest(db, '学校A', 0, DAY)
        self.assertEqual((status['pending'], status['waiting']), (2, 0))
        self.assertEqual(sorted(latest), sorted([self.sub_id('g1', DAY, 1), self.sid2]))  # 本提出ではなく再提出が選ばれる
        with self.db() as db, db:
            result = archive_round(db, '学校A', 0, DAY, 'boss')
        self.assertEqual(result['saved'], 2)
        with self.db() as db:
            rows = db.execute("SELECT group_id,attempt FROM report_archive ORDER BY group_id").fetchall()
            self.assertEqual(round_archive_status(db, '学校A', 0, DAY)['pending'], 0)
        self.assertEqual(rows, [('g1', 1), ('g2', 0)])

    def test_round_save_skips_archived_groups_and_needs_a_closed_round(self):
        self.archive(self.sid2)  # 班2は保存済み
        with self.db() as db, db:
            result = archive_round(db, '学校A', 0, DAY, 'boss')
        self.assertEqual((result['saved'], result['already']), (1, 1))
        self.add_round('2026-12-20', '最終')
        self.submit('g1', '2026-12-20', now=at('2026-12-01'))
        with self.db() as db, db:  # 期限前の提出回は保存できない
            with self.assertRaises(HTTPException) as caught:
                archive_round(db, '学校A', 0, '2026-12-20', 'boss')
        self.assertEqual(caught.exception.status_code, 409)

    def test_round_page_shows_one_save_button_with_the_pending_count(self):
        from report_reviews import build_report_review_router
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        router = build_report_review_router(self.path, SimpleNamespace(TemplateResponse=lambda request, name, context: {
            'html': env.get_template(name).render(**context)}), None, self.image_dir.name)
        page = next(r.endpoint for r in router.routes if r.path == '/teacher/report-reviews/rounds/{frame_key}/{day}')
        request = FakeRequest(session={'teacher_login': True, 'teacher_id': 'boss'}, url=SimpleNamespace(path='/x'), body={}, query_params={})
        html = asyncio.run(page(request, 0, DAY))['html']
        self.assertIn('id="ra-save"', html)
        self.assertIn('data-pending="2"', html)
        self.assertNotIn('type="checkbox"', html)  # 一つ一つ選ばせない
        plain = FakeRequest(session={'teacher_login': True, 'teacher_id': 't1'}, url=SimpleNamespace(path='/x'), body={}, query_params={})
        self.assertNotIn('id="ra-save"', asyncio.run(page(plain, 0, DAY))['html'])  # 一般の先生には出ない

    def test_teacher_and_student_see_the_same_full_list_and_detail(self):
        self.archive()
        self.archive(self.sid2)
        page = self.run_route('/teacher/report-archive', 'GET', self.teacher('t1'))
        self.assertEqual(page['context']['result']['total'], 2)
        self.assertIn('g1テーマ', page['html'])
        student_page = self.run_route('/report-archive', 'GET', self.student('u3'))  # 別グループの生徒にも全件が見える
        self.assertEqual(student_page['context']['result']['total'], 2)
        self.assertIn('班1', student_page['html'])
        self.assertIn('レポートアーカイブ', student_page['html'])
        with self.db() as db:
            archive_id = db.execute("SELECT id FROM report_archive WHERE group_id='g1'").fetchone()[0]
        for page in (self.run_route('/teacher/report-archive/{archive_id}', 'GET', self.teacher('t1'), archive_id),
                     self.run_route('/report-archive/{archive_id}', 'GET', self.student('u3'), archive_id)):
            self.assertIn('班1', page['html'])
            self.assertIn('生徒2', page['html'])
            self.assertNotIn('ra-delete', page['html'])  # 一般の先生・生徒には削除欄が出ない
        boss = self.run_route('/teacher/report-archive/{archive_id}', 'GET', self.teacher('boss'), archive_id)
        self.assertIn('ra-delete', boss['html'])
        self.assertIn('data-word="delete"', boss['html'])
        pdf = self.run_route('/report-archive/{archive_id}/report.pdf', 'GET', self.student('u1'), archive_id)
        self.assertTrue(pdf.body.startswith(b'%PDF'))

    def test_other_school_cannot_view(self):
        archive_id = self.archive()
        for call in (lambda: self.run_route('/teacher/report-archive/{archive_id}', 'GET', self.teacher('tb'), archive_id),
                     lambda: self.run_route('/report-archive/{archive_id}', 'GET', self.student('u9'), archive_id),
                     lambda: self.run_route('/report-archive/{archive_id}/report.pdf', 'GET', self.student('u9'), archive_id),
                     lambda: self.run_route('/report-archive/{archive_id}/assets/{content_hash}', 'GET', self.student('u9'), archive_id, 'a' * 64)):
            with self.assertRaises(HTTPException) as caught:
                call()
            self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.run_route('/teacher/report-archive', 'GET', self.teacher('tb'))['context']['result']['total'], 0)
        with self.assertRaises(HTTPException) as caught:
            self.run_route('/report-archive', 'GET', FakeRequest(session={}, url=SimpleNamespace(path='/report-archive'), body={}, query_params={}))
        self.assertEqual(caught.exception.status_code, 401)

    def test_asset_needs_archive_reference_and_same_school(self):
        digest = 'b' * 64
        report = {'a': {'value': 'x', 'images': []}, 'c': {'value': '', 'images': [{'content_hash': digest, 'caption': 'z'}]}}
        with self.db() as db, db:
            db.execute("UPDATE group_report_version SET report_snapshot=? WHERE group_id='g1'", (json.dumps(report),))
            db.execute("INSERT INTO group_report_asset VALUES(?,?,?,?,?)", (digest, 'img.png', 'image/png', 3, 'now'))
        (__import__('pathlib').Path(self.image_dir.name) / 'img.png').write_bytes(b'png')
        archive_id = self.archive()
        response = self.run_route('/report-archive/{archive_id}/assets/{content_hash}', 'GET', self.student('u1'), archive_id, digest)
        self.assertEqual(response.body, b'png')
        with self.assertRaises(HTTPException):  # アーカイブが参照していない画像は出ない
            self.run_route('/report-archive/{archive_id}/assets/{content_hash}', 'GET', self.student('u1'), archive_id, 'c' * 64)
        with self.assertRaises(HTTPException):
            self.run_route('/report-archive/{archive_id}/assets/{content_hash}', 'GET', self.student('u9'), archive_id, digest)

    def test_delete_permissions_and_confirmation(self):
        archive_id = self.archive()
        path = '/teacher/api/report-archive/{archive_id}/delete'
        with self.assertRaises(HTTPException) as caught:  # 一般の先生は不可
            self.run_route(path, 'POST', self.teacher('t1', {'csrf_token': 'tok', 'confirm': 'delete'}), archive_id)
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:  # 入力確認が違う
            self.run_route(path, 'POST', self.teacher('boss', {'csrf_token': 'tok', 'confirm': 'yes'}), archive_id)
        self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(HTTPException) as caught:  # CSRF
            self.run_route(path, 'POST', self.teacher('boss', {'csrf_token': 'bad', 'confirm': 'delete'}), archive_id)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.run_route(path, 'POST', self.teacher('boss', {'csrf_token': 'tok', 'confirm': 'delete'}), archive_id), {'ok': True})
        with self.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM report_archive').fetchone())

    def test_super_admin_lists_and_deletes_all_schools(self):
        archive_id = self.archive()
        page = self.run_route('/admin/report-archive', 'GET', self.admin())
        self.assertEqual(page['context']['result']['total'], 1)
        self.assertIn('学校A', page['html'])
        detail = self.run_route('/admin/report-archive/{archive_id}', 'GET', self.admin(), archive_id)
        self.assertIn('data-word="delete"', detail['html'])
        with self.assertRaises(HTTPException) as caught:
            self.run_route('/admin/report-archive', 'GET', self.admin('admin'))
        self.assertEqual(caught.exception.status_code, 403)
        path = '/admin/api/report-archive/{archive_id}/delete'
        with self.assertRaises(HTTPException) as caught:
            self.run_route(path, 'POST', self.admin('admin', {'csrf_token': 'tok', 'confirm': 'delete'}), archive_id)
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            self.run_route(path, 'POST', self.admin(body={'csrf_token': 'tok', 'confirm': 'nope'}), archive_id)
        self.assertEqual(caught.exception.status_code, 422)
        self.run_route(path, 'POST', self.admin(body={'csrf_token': 'tok', 'confirm': 'delete'}), archive_id)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT deleted_role FROM report_archive_deletion_log').fetchone()[0], 'super_admin')

    def test_back_query_only_accepts_safe_values(self):
        archive_id = self.archive()
        page = self.run_route('/report-archive/{archive_id}', 'GET', self.student('u1', {'back': '?q=%E3%83%86&page=2'}), archive_id)
        self.assertEqual(page['context']['back_query'], '?q=%E3%83%86&page=2')
        page = self.run_route('/report-archive/{archive_id}', 'GET', self.student('u1', {'back': 'https://evil.example/'}), archive_id)
        self.assertEqual(page['context']['back_query'], '')


class ThemeTests(unittest.TestCase):
    def test_ensure_theme_field(self):
        fields = [{'stable_key': 'x', 'label': '背景', 'field_type': 'text'}]
        result = ensure_theme_field(fields)
        self.assertEqual([f['stable_key'] for f in result], [THEME_KEY, 'x'])
        # すでにあれば先頭へ移し、文字だけにする。重複して足さない
        moved = ensure_theme_field([{'stable_key': 'x', 'label': '背景', 'field_type': 'text'},
                                    {'stable_key': THEME_KEY, 'label': '研究テーマ', 'field_type': 'both'}])
        self.assertEqual([(f['stable_key'], f['field_type']) for f in moved], [(THEME_KEY, 'text'), ('x', 'text')])
        legacy = ensure_theme_field([{'stable_key': 'x', 'label': '背景', 'field_type': 'text'},
                                     {'stable_key': 'old', 'label': 'テーマ', 'field_type': 'text'}])
        self.assertEqual([f['stable_key'] for f in legacy], ['old', 'x'])
        self.assertEqual(find_theme_field(legacy)['stable_key'], 'old')
        self.assertEqual(theme_value({'fields': legacy}, {'old': {'value': ' 光の研究 '}}), '光の研究')
        self.assertEqual(theme_value({'fields': legacy}, {}), '')

    def test_template_models_always_get_a_theme_first(self):
        field = {'label': '背景'}
        created = TemplateCreate(csrf_token='t', name='名前', fields=[field])
        self.assertEqual([(f.stable_key, f.label) for f in created.fields], [(THEME_KEY, 'テーマ'), ('', '背景')])
        school = SchoolReportTemplate(csrf_token='t', fields=[{'label': '背景'}, {'label': '研究テーマ', 'stable_key': THEME_KEY}])
        self.assertEqual(school.fields[0].stable_key, THEME_KEY)
        self.assertEqual(len(school.fields), 2)

    def test_migration_adds_theme_to_templates_and_drafts_once(self):
        db = sqlite3.connect(':memory:')
        initialize_studies(db)
        db.execute("INSERT INTO study_template(name,active,owner_school) VALUES('学校用',1,'学校A')")
        template_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute("INSERT INTO study_template_field(template_id,label,position,required,stable_key) VALUES(?,?,0,0,'k1')", (template_id, '背景'))
        snapshot = {'id': template_id, 'name': '学校用', 'fields': [{'stable_key': 'k1', 'label': '背景', 'field_type': 'text'}]}
        db.execute("INSERT INTO group_report_draft VALUES('g','s','学校A',?,?,?,'now',0)",
                   (template_id, json.dumps(snapshot), json.dumps({'k1': {'value': 'あ', 'images': []}})))
        migrate_theme_fields(db)
        migrate_theme_fields(db)  # 何度呼んでもよい
        labels = [r[0] for r in db.execute('SELECT label FROM study_template_field WHERE template_id=? ORDER BY position,id', (template_id,))]
        self.assertEqual(labels, ['テーマ', '背景'])
        template, data, revision = db.execute('SELECT template_snapshot,report_data,revision FROM group_report_draft').fetchone()
        self.assertEqual([f['stable_key'] for f in json.loads(template)['fields']], [THEME_KEY, 'k1'])
        self.assertEqual(json.loads(data)[THEME_KEY], {'value': '', 'images': []})
        self.assertEqual(json.loads(data)['k1']['value'], 'あ')
        self.assertEqual(revision, 1)  # 1回だけ更新される
        # 基本テンプレートは最初から「テーマ」項目を持つので変更しない
        basic = db.execute("SELECT COUNT(*) FROM study_template_field f JOIN study_template t ON t.id=f.template_id WHERE t.seed_key='basic-research-v1' AND f.label='テーマ'").fetchone()[0]
        self.assertEqual(basic, 1)
        self.assertEqual(default_theme_field()['stable_key'], THEME_KEY)


if __name__ == '__main__':
    unittest.main()
