import asyncio
import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace

from fastapi import HTTPException

from group_reports import JST, load_version, pdf_sections, run_scheduled_snapshots
from report_header import DEFAULT_HEADER_ITEMS, group_context_snapshot, header_options, header_text
from studies import get_templates, initialize_studies
from study_pdf import render_study_pdf
from template_management import SchoolReportTemplate, create_template_router


def create_people(db):
    """チャット・生徒・追加項目の最小限のテーブルと、学校Aのグループg1（2人在籍、1人退出）。"""
    db.executescript("""
        CREATE TABLE teacher(id TEXT, pwd TEXT, school TEXT, role TEXT);
        CREATE TABLE student(id TEXT PRIMARY KEY, pwd TEXT, school TEXT, profile_image TEXT, name TEXT, furigana TEXT, last_access_at TEXT);
        CREATE TABLE tekne_student_identity(id TEXT PRIMARY KEY, login_id TEXT, school_id TEXT, created_at TEXT, deleted_at TEXT);
        CREATE TABLE tekne_chat_group(id TEXT PRIMARY KEY, school_id TEXT, name TEXT, icon_key TEXT, created_by TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE tekne_chat_member(group_id TEXT, school_id TEXT, student_id TEXT, joined_at TEXT, left_at TEXT);
        CREATE TABLE student_custom_field(id INTEGER PRIMARY KEY, school TEXT, label TEXT, field_type TEXT, required INTEGER, options_json TEXT, deleted_at TEXT, created_at TEXT);
        CREATE TABLE student_custom_field_value(student_id TEXT, field_id INTEGER, value_json TEXT, updated_at TEXT);
        INSERT INTO teacher VALUES('boss','x','学校A','super_teacher');
        INSERT INTO student VALUES('u1','x','学校A',NULL,'山田 花子','やまだ はなこ',NULL),('u2','x','学校A',NULL,'佐藤 太郎','さとう たろう',NULL),('u3','x','学校A',NULL,'退出 者','',NULL);
        INSERT INTO tekne_student_identity VALUES('i1','u1','s1','now',NULL),('i2','u2','s1','now',NULL),('i3','u3','s1','now',NULL);
        INSERT INTO tekne_chat_group VALUES('g1','s1','1班',NULL,'u1','now','now');
        INSERT INTO tekne_chat_member VALUES('g1','s1','i1','2026-01-01',NULL),('g1','s1','i2','2026-01-02',NULL),('g1','s1','i3','2026-01-03','2026-02-01');
        INSERT INTO student_custom_field VALUES(1,'学校A','学年','select',1,'[]',NULL,'now'),(2,'学校A','削除済み','text',0,'[]','gone','now'),(3,'学校B','他校','text',0,'[]',NULL,'now');
        INSERT INTO student_custom_field_value VALUES('u1',1,'"1"','now'),('u2',1,'"2"','now');
    """)


class ReportHeaderTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_studies(db)
            create_people(db)

    def tearDown(self):
        os.remove(self.path)

    def db(self):
        return closing(sqlite3.connect(self.path))

    def test_options_include_only_this_schools_active_custom_fields(self):
        with self.db() as db:
            keys = [item['key'] for item in header_options(db, '学校A')]
        self.assertIn('saved_at', keys)
        self.assertIn('member_name', keys)
        self.assertIn('custom:1', keys)
        self.assertNotIn('custom:2', keys)
        self.assertNotIn('custom:3', keys)

    def test_context_snapshot_has_current_members_with_custom_values(self):
        with self.db() as db:
            context = group_context_snapshot(db, 'g1', '学校A')
        self.assertEqual(context['group_name'], '1班')
        self.assertEqual([m['id'] for m in context['members']], ['u1', 'u2'])
        self.assertEqual(context['members'][0]['name'], '山田 花子')
        self.assertEqual(context['members'][1]['custom'], {'1': '2'})
        self.assertEqual(context['custom_labels'], {'1': '学年'})

    def test_header_text_orders_items_and_groups_member_info(self):
        with self.db() as db:
            context = group_context_snapshot(db, 'g1', '学校A')
        version = {'saved_at': '2026-11-18T15:00:00+00:00', 'school_name': '学校A', 'version_number': 3,
                   'saved_by': '自動保存', 'kind': 'scheduled', 'scheduled_date': '2026-11-18'}
        text = header_text(['saved_at', 'member_name', 'school_name', 'custom:1', 'save_kind'], version, context)
        self.assertEqual(text.split('\n'), [
            '保存日時：2026/11/19 00:00', 'メンバー：', '・山田 花子／学年 1', '・佐藤 太郎／学年 2',
            '学校名：学校A', '保存の種類：定期保存（2026/11/18）'])
        self.assertEqual(header_text([], version, context), '')
        self.assertIn('・（メンバーなし）', header_text(['member_id'], version, {'members': []}))

    def test_pdf_without_header_items_has_no_header_section(self):
        with self.db() as db:
            template = get_templates(db)[0]
            values = {f['stable_key']: {'value': 'x', 'images': []} for f in template['fields']}
            sections = pdf_sections(db, '.', template, values, '')
            self.assertEqual(sections[0][0], template['fields'][0]['label'])
            with_header = pdf_sections(db, '.', template, values, '学校名：学校A')
        self.assertEqual(with_header[0][:5], ('保存情報', '学校名：学校A', 11, 11, True))
        self.assertTrue(render_study_pdf(with_header).startswith(b'%PDF'))

    def test_scheduled_version_keeps_members_at_save_time_and_old_rows_use_default(self):
        snapshot = json.dumps({'id': 1, 'fields': [], 'header_items': ['group_name', 'member_id']})
        with self.db() as db, db:
            db.execute("INSERT INTO group_report_draft VALUES('g1','s1','学校A',1,?,'{}','now',0)", (snapshot,))
            db.execute("INSERT INTO group_report_snapshot_day(school,day,created_by,created_at) VALUES('学校A','2026-11-18','boss','now')")
            # 以前の形式の履歴（メンバー情報なし・テンプレートに設定なし）
            db.execute("""INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at)
                VALUES('g1','s1','学校A',1,'{"fields":[]}','{}','u1','2026-11-01T00:00:00+00:00')""")
        run_scheduled_snapshots(self.path, datetime.datetime(2026, 11, 19, 0, 1, tzinfo=JST))
        with self.db() as db, db:
            db.execute("UPDATE tekne_chat_member SET left_at='later' WHERE student_id='i2'")
            ids = [r[0] for r in db.execute("SELECT id FROM group_report_version WHERE group_id='g1' ORDER BY version_number")]
            old, scheduled = load_version(db, 'g1', ids[0]), load_version(db, 'g1', ids[1])
        self.assertEqual(scheduled['header'], 'グループ名：1班\nメンバー：\n・u1\n・u2')
        self.assertEqual(old['header'].split('\n'), ['学校名：学校A', 'グループ名：1班', 'バージョン：1', '保存日時：2026/11/01 09:00', '保存者：u1'])
        self.assertEqual(DEFAULT_HEADER_ITEMS, ['school_name', 'group_name', 'version', 'saved_at', 'saved_by'])


class HeaderSettingApplyTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_studies(db)
            create_people(db)
            fields = get_templates(db)[0]['fields']
            db.execute('''INSERT INTO group_report_draft VALUES('g1','s1','学校A',1,?,'{}','now',0)''',
                       (json.dumps({'id': 1, 'name': '基本', 'fields': fields}, ensure_ascii=False),))
        routes = create_template_router(lambda: self.path, SimpleNamespace(TemplateResponse=lambda **kwargs: kwargs)).routes
        self.apply = next(r.endpoint for r in routes if r.path == '/teacher/school-report/apply' and 'POST' in r.methods)
        self.editor = next(r.endpoint for r in routes if r.path == '/teacher/school-report/template' and 'GET' in r.methods)

    def tearDown(self):
        os.remove(self.path)

    def request(self):
        return SimpleNamespace(session={'teacher_login': True, 'teacher_id': 'boss', 'school_report_template_csrf': 'token'})

    def call(self, header_items):
        data = SchoolReportTemplate(csrf_token='token', fields=[{'label': '項目'}], header_items=header_items)
        return asyncio.run(self.apply(self.request(), data))

    def test_apply_saves_header_items_and_puts_them_in_draft_snapshot(self):
        page = asyncio.run(self.editor(self.request()))['context']
        self.assertEqual(page['header_items'], DEFAULT_HEADER_ITEMS)
        self.assertIn({'key': 'custom:1', 'label': 'メンバーの学年'}, page['header_options'])
        self.call(['saved_at', 'member_name', 'custom:1'])
        with closing(sqlite3.connect(self.path)) as db:
            snapshot = json.loads(db.execute("SELECT template_snapshot FROM group_report_draft WHERE group_id='g1'").fetchone()[0])
        self.assertEqual(snapshot['header_items'], ['saved_at', 'member_name', 'custom:1'])
        self.assertEqual(asyncio.run(self.editor(self.request()))['context']['header_items'], ['saved_at', 'member_name', 'custom:1'])
        self.call([])
        self.assertEqual(asyncio.run(self.editor(self.request()))['context']['header_items'], [])

    def test_apply_saves_header_style_and_pdf_uses_it(self):
        page = asyncio.run(self.editor(self.request()))['context']
        self.assertEqual(page['header_style'], {'font_size': 11, 'alignment': 'left', 'bold': False})
        data = SchoolReportTemplate(csrf_token='token', fields=[{'label': '項目'}], header_items=['school_name'],
                                    header_style={'font_size': 14, 'alignment': 'center', 'bold': True})
        asyncio.run(self.apply(self.request(), data))
        style = {'font_size': 14, 'alignment': 'center', 'bold': True}
        self.assertEqual(asyncio.run(self.editor(self.request()))['context']['header_style'], style)
        with closing(sqlite3.connect(self.path)) as db, db:
            snapshot = db.execute("SELECT template_snapshot FROM group_report_draft WHERE group_id='g1'").fetchone()[0]
            self.assertEqual(json.loads(snapshot)['header_style'], style)
            db.execute("""INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at)
                VALUES('g1','s1','学校A',1,?,'{}','u1','2026-11-01T00:00:00+00:00')""", (snapshot,))
            row = load_version(db, 'g1', db.execute('SELECT id FROM group_report_version').fetchone()[0])
            sections = pdf_sections(db, '.', json.loads(snapshot), {}, row['header'], row['header_style'])
        self.assertEqual(row['header'], '学校名：学校A')
        self.assertEqual(sections[0][:9], ('保存情報', '学校名：学校A', 11, 14, True, 'left', 'center', False, True))
        self.assertTrue(render_study_pdf(sections).startswith(b'%PDF'))
        # 文字設定を送らない適用（古い画面）では、保存済みの文字設定を使い続ける
        self.call(['group_name'])
        self.assertEqual(asyncio.run(self.editor(self.request()))['context']['header_style'], style)

    def test_header_style_rejects_out_of_range_values(self):
        from pydantic import ValidationError
        for style in ({'font_size': 5}, {'font_size': 37}, {'alignment': 'top'}, {'bold': 'yes'}):
            with self.subTest(style=style), self.assertRaises(ValidationError):
                SchoolReportTemplate(csrf_token='token', fields=[{'label': '項目'}], header_items=[], header_style=style)

    def test_apply_rejects_unknown_duplicate_and_other_school_items(self):
        for items in (['nope'], ['saved_at', 'saved_at'], ['custom:3'], ['custom:2']):
            with self.subTest(items=items), self.assertRaises(HTTPException) as caught:
                self.call(items)
            self.assertEqual(caught.exception.status_code, 422)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM school_report_header').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM study_template WHERE owner_school='学校A'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
