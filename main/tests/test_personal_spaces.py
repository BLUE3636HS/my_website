"""ワークスペースの個人スペース（本人だけが使うグループ）のテスト。"""
import asyncio
import datetime
import io
import json
import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import bcrypt
from fastapi import HTTPException, WebSocketDisconnect
from starlette.datastructures import FormData, UploadFile
from starlette.requests import Request

import main
import test_studies as fixtures
from group_reports import build_group_report_router, run_scheduled_snapshots
from studies import connect_studies
from PIL import Image
from tekne_chat import (_connect, build_tekne_chat_router, cleanup_expired_groups,
                        initialize_tekne_chat)


class PersonalSpaceTests(unittest.TestCase):
    setUp = fixtures.StudyTests.setUp
    rows = fixtures.StudyTests.rows
    token = fixtures.StudyTests.token

    def personal(self, name='植物の成長の研究', session=None):
        return self.chat.create_group(session or self.session, name, 'personal')

    def test_existing_groups_become_group_kind_on_migration(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript("""
                CREATE TABLE student(id TEXT, pwd TEXT, school TEXT);
                CREATE TABLE registered_school(name TEXT NOT NULL UNIQUE);
                INSERT INTO registered_school VALUES ('school1');
                INSERT INTO student VALUES ('a','x','school1');
                CREATE TABLE tekne_chat_group(id TEXT PRIMARY KEY NOT NULL, school_id TEXT NOT NULL, name TEXT NOT NULL,
                    icon_key TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(id, school_id));
                INSERT INTO tekne_chat_group VALUES ('old','s','旧グループ',NULL,'u','now','now');""")
            initialize_tekne_chat(db)
            initialize_tekne_chat(db)
            self.assertEqual(db.execute('SELECT kind FROM tekne_chat_group').fetchall(), [('group',)])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE tekne_chat_group SET kind='other'")

    def test_student_can_create_several_personal_spaces(self):
        first = self.personal('研究A')
        second = self.personal('研究B')
        group = self.chat.create_group(self.session, '1班')
        self.assertEqual((first['kind'], second['kind'], group['kind']), ('personal', 'personal', 'group'))
        kinds = {item['name']: item['kind'] for item in self.chat.groups(self.session)}
        self.assertEqual(kinds, {'研究A': 'personal', '研究B': 'personal', '1班': 'group'})
        with self.assertRaises(HTTPException) as error:
            self.chat.create_group(self.session, '名前', 'other')
        self.assertEqual(error.exception.status_code, 422)
        with self.assertRaises(HTTPException) as error:
            self.chat.create_group(self.session, 'a/b', 'personal')
        self.assertIn('個人スペース名', error.exception.detail)

    def test_personal_space_is_private_and_cannot_invite_or_leave(self):
        space = self.personal()
        with self.assertRaises(HTTPException) as error:
            self.chat.invite(self.session, space['id'], 'c')
        self.assertEqual(error.exception.status_code, 422)
        with self.assertRaises(HTTPException) as error:
            self.chat.leave(self.session, space['id'])
        self.assertEqual(error.exception.status_code, 422)
        other = {'user_login': True, 'user_id': 'c',
                 'user_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                 'tekne_chat_session': fixtures.create_chat_session(self.path, 'c')}
        self.assertEqual(self.chat.groups(other), [])
        with self.assertRaises(HTTPException) as error:
            self.chat.message_history(other, space['id'])
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual([m['login_id'] for m in self.chat.members(self.session, space['id'])], ['a'])

    def test_personal_space_can_be_renamed_and_used_for_memos_and_files(self):
        space = self.personal()
        self.assertEqual(self.chat.rename_group(self.session, space['id'], '新しい研究名')['name'], '新しい研究名')
        message = self.chat.create_message(self.session, space['id'], '実験メモ')
        self.assertEqual(message['body'], '実験メモ')
        with closing(_connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT kind FROM tekne_chat_group WHERE id=?', (space['id'],)).fetchone()[0], 'personal')

    def test_normal_group_still_invites_and_leaves(self):
        group = self.chat.create_group(self.session, '共同研究')
        self.chat.invite(self.session, group['id'], 'c')
        self.assertEqual(len(self.chat.members(self.session, group['id'])), 2)
        self.assertEqual(self.chat.leave(self.session, group['id']), {'left': True})

    def test_cleanup_never_removes_personal_spaces(self):
        space = self.personal()
        group = self.chat.create_group(self.session, '空のグループ')
        self.chat.leave(self.session, group['id'])
        old = '2020-01-01T00:00:00+00:00'
        with closing(_connect(self.path)) as db, db:
            db.execute('UPDATE tekne_chat_group SET updated_at=?, created_at=?', (old, old))
            db.execute('UPDATE tekne_chat_member SET joined_at=?', (old,))
            db.execute('UPDATE tekne_chat_member SET left_at=? WHERE left_at IS NOT NULL', (old,))
            # 万一メンバー情報が外れても、個人スペースは自動削除の対象にしない
            db.execute('UPDATE tekne_chat_member SET left_at=? WHERE group_id=?', (old, space['id']))
        cleanup_expired_groups(self.path, self.root / 'chat-files', force=True)
        remaining = {row[0] for row in self.rows('SELECT id FROM tekne_chat_group')}
        self.assertIn(space['id'], remaining)
        self.assertNotIn(group['id'], remaining)

    def test_admin_cannot_remove_owner_from_personal_space(self):
        space = self.personal()
        owner = self.rows('SELECT student_id FROM tekne_chat_member WHERE group_id=?', (space['id'],))[0][0]
        with self.assertRaises(HTTPException) as error:
            self.chat.admin_remove_member('person', space['id'], owner)
        self.assertEqual(error.exception.status_code, 422)
        listed = self.chat.admin_groups()['groups']
        self.assertEqual(listed[0]['kind'], 'personal')

    def test_personal_space_is_not_offered_or_accepted_for_group_pdf_submission(self):
        space = self.personal('個人の研究')
        group = self.chat.create_group(self.session, '共同研究')
        main.seed_school_feature_settings(self.path)
        with patch.object(main, 'tekne_chat_service', self.chat):
            coroutine = main.AddForm(fixtures.request('/addform', self.session))
            try:
                response = coroutine.send(None)
            except StopIteration as result:
                response = result.value
            self.assertEqual([g['id'] for g in response.context['submission_groups']], [group['id']])
            self.assertNotIn('個人の研究', response.body.decode())
            req = fixtures.request('/addform', self.session)
            req._form = FormData({'registration_type': 'pdf', 'name': '研究', 'introduce': '紹介',
                                  'csrf_token': 'csrf', 'submission_token': self.token(),
                                  'submission_kind': 'group', 'chat_group_id': space['id'],
                                  'pdf': UploadFile(io.BytesIO(fixtures.sample_pdf()), filename='a.pdf')})
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.Add(req))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.rows('SELECT id FROM study'), [])

    def insert_version(self, group_id, version=1, kind='manual', day=None):
        with closing(connect_studies(self.path)) as db, db:
            db.execute("""INSERT INTO group_report_version
                (group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at,kind,scheduled_date)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (group_id, 's', 'school1', version, json.dumps({'fields': []}), '{}', 'a',
                 '2026-11-01T00:00:00+00:00', kind, day))

    def test_teacher_report_list_labels_personal_spaces_with_owner(self):
        space = self.personal('植物の研究')
        group = self.chat.create_group(self.session, '共同研究')
        self.insert_version(space['id'])
        self.insert_version(group['id'])
        with closing(connect_studies(self.path)) as db, db:
            db.execute('ALTER TABLE teacher ADD COLUMN role TEXT')
        router = build_group_report_router(self.path, main.templates, self.chat, self.root / 'report-images')
        endpoint = next(r.endpoint for r in router.routes if r.path == '/teacher/reports')
        response = asyncio.run(endpoint(fixtures.request('/teacher/reports', {'teacher_login': True, 'teacher_id': 'teacher1'})))
        groups = response.context['groups']
        self.assertEqual([(g['name'], g['kind'], g['owner']) for g in groups],
                         [('共同研究', 'group', 'A'), ('植物の研究', 'personal', 'A')])
        html = response.body.decode()
        self.assertIn('個人：A（植物の研究）', html)
        self.assertIn('グループ・個人スペースごと', html)

    def test_scheduled_snapshot_includes_personal_spaces(self):
        space = self.personal()
        with closing(connect_studies(self.path)) as db, db:
            school_id = db.execute('SELECT school_id FROM tekne_chat_group WHERE id=?', (space['id'],)).fetchone()[0]
            db.execute("""INSERT INTO group_report_draft(group_id,school_id,school_name,template_id,template_snapshot,report_data,updated_at,revision)
                VALUES (?,?,?,?,?,?,?,0)""", (space['id'], school_id, 'school1', 1, json.dumps({'fields': []}), '{}', 'now'))
            db.execute("INSERT INTO group_report_snapshot_day(school,day,created_by,created_at) VALUES ('school1','2026-11-01','teacher1','now')")
        created = run_scheduled_snapshots(self.path, datetime.datetime(2026, 11, 3, 12, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(created, 1)
        self.assertEqual(self.rows("SELECT group_id,kind FROM group_report_version"), [(space['id'], 'scheduled')])


class ChatListAndIconTests(unittest.TestCase):
    rows = fixtures.StudyTests.rows

    def setUp(self):
        fixtures.StudyTests.setUp(self)
        self.session['tekne_chat_csrf'] = 'chat-csrf'
        self.storage = self.root / 'chat-files'
        self.router, self.chat = build_tekne_chat_router(self.path, main.templates, self.storage)

    def endpoint(self, path, method):
        return next(r.endpoint for r in self.router.routes if r.path == path and method in getattr(r, 'methods', set()))

    def test_list_is_newest_update_first_across_kinds(self):
        first = self.chat.create_group(self.session, '古い個人', 'personal')
        second = self.chat.create_group(self.session, 'グループ')
        third = self.chat.create_group(self.session, '新しい個人', 'personal')
        stamps = {first['id']: '2026-01-01T00:00:00+00:00', second['id']: '2026-01-02T00:00:00+00:00',
                  third['id']: '2026-01-03T00:00:00+00:00'}
        with closing(_connect(self.path)) as db, db:
            for group_id, stamp in stamps.items():
                db.execute('UPDATE tekne_chat_group SET updated_at=? WHERE id=?', (stamp, group_id))
        self.assertEqual([g['id'] for g in self.chat.groups(self.session)], [third['id'], second['id'], first['id']])
        self.chat.create_message(self.session, first['id'], '新しいメモ')
        self.assertEqual(self.chat.groups(self.session)[0]['id'], first['id'])

    def icon_update(self, group_id, **values):
        update = self.endpoint('/tekne-chat/api/groups/{group_id}/icon', 'POST')
        values.setdefault('file', None)
        values.setdefault('remove_icon', False)
        return asyncio.run(update(fixtures.request('/icon', self.session), group_id, 'chat-csrf', **values))

    def test_icon_can_be_changed_and_removed_for_personal_space_and_group(self):
        png = io.BytesIO()
        Image.new('RGB', (40, 40), (200, 30, 30)).save(png, 'PNG')
        for kind in ('personal', 'group'):
            with self.subTest(kind=kind):
                space = self.chat.create_group(self.session, '研究', kind)
                self.icon_update(space['id'], file=UploadFile(io.BytesIO(png.getvalue()), filename='icon.png'))
                first_key = self.chat.group_icon(self.session, space['id'])
                self.assertTrue(first_key)
                self.icon_update(space['id'], file=UploadFile(io.BytesIO(png.getvalue()), filename='icon2.png'))
                second_key = self.chat.group_icon(self.session, space['id'])
                self.assertNotEqual(first_key, second_key)  # キーが変わるので画面の画像URLも変わる
                self.assertFalse((self.storage / 'icons' / f'{first_key}.webp').exists())
                listed = next(g for g in self.chat.groups(self.session) if g['id'] == space['id'])
                self.assertEqual(listed['icon_key'], second_key)
                self.icon_update(space['id'], remove_icon=True)
                self.assertIsNone(self.chat.group_icon(self.session, space['id']))
        with self.assertRaises(HTTPException) as error:
            self.icon_update(space['id'], file=UploadFile(io.BytesIO(b'not image'), filename='bad.png'))
        self.assertEqual(error.exception.status_code, 422)


class FakeWebSocket:
    def __init__(self, session):
        self.headers = {'origin': 'http://test', 'host': 'test'}
        self.url = SimpleNamespace(scheme='ws')
        self.scope = {'session': session}
        self.accepted = False
        self.closed_code = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.closed_code = code

    async def send_json(self, message):
        self.sent.append(message)

    async def receive_json(self):
        raise WebSocketDisconnect()


class PersonalReportTests(unittest.TestCase):
    """個人スペースのレポート記入はWebSocketを使わず、HTTPだけで保存する。"""
    rows = fixtures.StudyTests.rows

    def setUp(self):
        fixtures.StudyTests.setUp(self)
        with closing(connect_studies(self.path)) as db, db:
            db.execute("INSERT INTO school_report_template VALUES('school1', 1, 'now', 'teacher1')")
        self.session['tekne_chat_csrf'] = 'chat-csrf'
        self.router = build_group_report_router(self.path, main.templates, self.chat, self.root / 'report-images')
        self.space = self.chat.create_group(self.session, '個人の研究', 'personal')
        self.group = self.chat.create_group(self.session, '共同研究')

    def endpoint(self, path, method='GET'):
        for route in self.router.routes:
            methods = getattr(route, 'methods', None)
            if route.path == path and (methods is None or method in methods):
                return route.endpoint
        raise AssertionError(path)

    def page(self, group_id):
        return asyncio.run(self.endpoint('/tekne-chat/groups/{group_id}/report')(
            fixtures.request(f'/tekne-chat/groups/{group_id}/report', self.session), group_id))

    def save(self, group_id, body):
        payload = json.dumps({'csrf_token': 'chat-csrf', **body}).encode()
        req = Request({'type': 'http', 'method': 'POST', 'path': '/save', 'root_path': '', 'scheme': 'http',
                       'server': ('test', 80), 'client': ('test', 1), 'query_string': b'',
                       'headers': [(b'content-type', b'application/json')], 'session': self.session})

        async def receive():
            return {'type': 'http.request', 'body': payload, 'more_body': False}
        req._receive = receive
        return asyncio.run(self.endpoint('/api/group-reports/{group_id}/save', 'POST')(req, group_id))

    def draft_row(self, group_id):
        return self.rows('SELECT report_data, revision FROM group_report_draft WHERE group_id=?', (group_id,))

    def test_report_page_marks_personal_space_and_hides_connection_status(self):
        personal = self.page(self.space['id'])
        html = personal.body.decode()
        self.assertIn('data-personal="1"', html)
        self.assertNotIn('id="report-connection"', html)
        self.assertNotIn('id="report-presence"', html)
        self.assertEqual(len(personal.context['state']['template_key']), 16)
        group_html = self.page(self.group['id']).body.decode()
        self.assertNotIn('data-personal', group_html)
        self.assertIn('id="report-connection"', group_html)
        self.assertIn('id="report-presence"', group_html)

    def test_personal_space_saves_over_http_without_websocket(self):
        state = self.page(self.space['id']).context['state']
        key = state['template']['fields'][0]['stable_key']
        result = self.save(self.space['id'], {'data': {key: {'value': '個人で書いた本文', 'images': []}},
                                              'template_key': state['template_key']})
        self.assertTrue(result['ok'])
        saved = json.loads(self.draft_row(self.space['id'])[0][0])
        self.assertEqual(saved[key]['value'], '個人で書いた本文')
        self.assertEqual(self.rows('SELECT * FROM group_report_lock'), [])

    def test_save_with_old_template_is_rejected_without_changes(self):
        state = self.page(self.space['id']).context['state']
        key = state['template']['fields'][0]['stable_key']
        with closing(connect_studies(self.path)) as db, db:
            snapshot = json.loads(db.execute('SELECT template_snapshot FROM group_report_draft WHERE group_id=?',
                                             (self.space['id'],)).fetchone()[0])
            snapshot['fields'][0]['label'] = '先生が変更した見出し'
            db.execute('UPDATE group_report_draft SET template_snapshot=? WHERE group_id=?',
                       (json.dumps(snapshot, ensure_ascii=False), self.space['id']))
        before = self.draft_row(self.space['id'])
        with self.assertRaises(HTTPException) as error:
            self.save(self.space['id'], {'data': {key: {'value': '古い画面の本文', 'images': []}},
                                         'template_key': state['template_key']})
        self.assertEqual(error.exception.status_code, 412)
        self.assertIn('テンプレートを更新', error.exception.detail)
        self.assertEqual(self.draft_row(self.space['id']), before)
        fresh = self.page(self.space['id']).context['state']
        self.assertNotEqual(fresh['template_key'], state['template_key'])
        self.assertTrue(self.save(self.space['id'], {'data': {key: {'value': '新しい画面の本文', 'images': []}},
                                                     'template_key': fresh['template_key']})['ok'])

    def test_group_save_without_template_key_is_unchanged(self):
        state = self.page(self.group['id']).context['state']
        key = state['template']['fields'][0]['stable_key']
        self.assertTrue(self.save(self.group['id'], {'data': {key: {'value': 'グループの本文', 'images': []}}})['ok'])

    def test_report_save_moves_space_to_top_of_list(self):
        old = '2020-01-01T00:00:00+00:00'
        with closing(_connect(self.path)) as db, db:
            db.execute('UPDATE tekne_chat_group SET updated_at=?', (old,))
            db.execute('UPDATE tekne_chat_group SET updated_at=? WHERE id=?', ('2021-01-01T00:00:00+00:00', self.group['id']))
        self.assertEqual(self.chat.groups(self.session)[0]['id'], self.group['id'])
        state = self.page(self.space['id']).context['state']
        key = state['template']['fields'][0]['stable_key']
        self.save(self.space['id'], {'data': {key: {'value': '更新', 'images': []}}, 'template_key': state['template_key']})
        self.assertEqual([g['id'] for g in self.chat.groups(self.session)], [self.space['id'], self.group['id']])

    def test_deleted_group_is_hidden_from_teacher_reports_and_snapshots(self):
        for group_id in (self.space['id'], self.group['id']):
            self.page(group_id)  # 下書きを作る
            with closing(connect_studies(self.path)) as db, db:
                db.execute("""INSERT INTO group_report_version
                    (group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at)
                    VALUES (?, 's', 'school1', 1, ?, '{}', 'a', '2026-11-01T00:00:00+00:00')""",
                    (group_id, json.dumps({'fields': []})))
        version_id = self.rows('SELECT id FROM group_report_version WHERE group_id=?', (self.group['id'],))[0][0]
        with closing(connect_studies(self.path)) as db, db:
            db.execute('ALTER TABLE teacher ADD COLUMN role TEXT')
            db.execute("INSERT OR REPLACE INTO admin VALUES ('person', ?, 'Person', 'admin', NULL, 0)",
                       (bcrypt.hashpw(b'secret', bcrypt.gensalt(4)).decode(),))
        teacher = {'teacher_login': True, 'teacher_id': 'teacher1',
                   'teacher_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        reports = self.endpoint('/teacher/reports')
        pdf = self.endpoint('/teacher/reports/{group_id}/versions/{version_id}.pdf')
        before = asyncio.run(reports(fixtures.request('/teacher/reports', teacher))).context['groups']
        self.assertEqual({g['id'] for g in before}, {self.space['id'], self.group['id']})

        self.chat.admin_delete_group('person', self.group['id'], 'person', 'secret')

        after = asyncio.run(reports(fixtures.request('/teacher/reports', teacher)))
        self.assertEqual([g['id'] for g in after.context['groups']], [self.space['id']])
        self.assertNotIn(self.group['id'], after.body.decode())
        with self.assertRaises(HTTPException) as error:
            asyncio.run(pdf(fixtures.request('/pdf', teacher), self.group['id'], version_id))
        self.assertEqual(error.exception.status_code, 404)
        # 残った下書きから定期保存の版が作られない
        with closing(connect_studies(self.path)) as db, db:
            db.execute("INSERT INTO group_report_snapshot_day(school,day,created_by,created_at) VALUES ('school1','2026-11-01','teacher1','now')")
        run_scheduled_snapshots(self.path, datetime.datetime(2026, 11, 3, 12, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(self.rows("SELECT group_id FROM group_report_version WHERE kind='scheduled'"), [(self.space['id'],)])

    def test_websocket_is_refused_for_personal_space_but_allowed_for_group(self):
        socket = self.endpoint('/ws/group-reports/{group_id}')
        personal = FakeWebSocket(self.session)
        asyncio.run(socket(personal, self.space['id']))
        self.assertEqual((personal.accepted, personal.closed_code), (False, 4403))
        group = FakeWebSocket(self.session)
        asyncio.run(socket(group, self.group['id']))
        self.assertTrue(group.accepted)
        self.assertIsNone(group.closed_code)
        self.assertEqual(group.sent[0]['type'], 'presence')


if __name__ == '__main__':
    unittest.main()
