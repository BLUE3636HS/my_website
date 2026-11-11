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

from group_reports import JST, run_scheduled_snapshots
from studies import MANUAL_REPORT_VERSION_LIMIT, initialize_studies, prune_manual_report_versions
from template_management import SnapshotDays, create_template_router, snapshot_today


def insert_version(db, group_id, number, kind='manual', day=None):
    db.execute("""INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at,kind,scheduled_date)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", (group_id, 's1', '学校A', number, '{}', '{}', 'u', 'now', kind, day))


class ReportSnapshotTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_studies(db)
            for group_id, school in (('g1', '学校A'), ('g2', '学校A'), ('g3', '学校B')):
                db.execute("INSERT INTO group_report_draft VALUES(?,?,?,1,?,?,'now',0)",
                           (group_id, 's1', school, json.dumps({'id': 1, 'fields': []}), json.dumps({'k': {'value': group_id, 'images': []}})))

    def tearDown(self):
        os.remove(self.path)

    def db(self):
        return closing(sqlite3.connect(self.path))

    def add_day(self, day, school='学校A'):
        with self.db() as db, db:
            db.execute("INSERT INTO group_report_snapshot_day(school,day,created_by,created_at) VALUES(?,?,'t','now')", (school, day))

    def at(self, day, hour=0, minute=1):
        return datetime.datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=JST)

    def test_manual_versions_keep_latest_ten_and_never_scheduled(self):
        with self.db() as db, db:
            insert_version(db, 'g1', 1, 'scheduled', '2026-01-01')
            for number in range(2, 15):
                insert_version(db, 'g1', number)
            insert_version(db, 'g2', 1)
            removed = prune_manual_report_versions(db, 'g1')
            rows = db.execute("SELECT version_number,kind FROM group_report_version WHERE group_id='g1' ORDER BY version_number").fetchall()
        self.assertEqual(removed, 3)
        self.assertEqual(rows[0], (1, 'scheduled'))
        self.assertEqual([r[0] for r in rows if r[1] == 'manual'], list(range(5, 15)))
        self.assertEqual(MANUAL_REPORT_VERSION_LIMIT, 10)

    def test_initialize_prunes_existing_history_and_adds_columns_to_old_table(self):
        with self.db() as db, db:
            db.execute('DROP TABLE group_report_version')
            db.execute("""CREATE TABLE group_report_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL, school_id TEXT NOT NULL,
                school_name TEXT NOT NULL, version_number INTEGER NOT NULL, template_snapshot TEXT NOT NULL,
                report_snapshot TEXT NOT NULL, saved_by TEXT NOT NULL, saved_at TEXT NOT NULL,
                UNIQUE(group_id,version_number))""")
            db.executemany("INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at) VALUES('g1','s1','学校A',?,'{}','{}','u','now')",
                           [(n,) for n in range(1, 13)])
        with self.db() as db, db:
            initialize_studies(db)
            rows = db.execute("SELECT version_number,kind FROM group_report_version ORDER BY version_number").fetchall()
        self.assertEqual([r[0] for r in rows], list(range(3, 13)))
        self.assertTrue(all(r[1] == 'manual' for r in rows))

    def test_scheduled_snapshot_saves_each_school_group_once(self):
        self.add_day('2026-11-18')
        self.assertEqual(run_scheduled_snapshots(self.path, self.at('2026-11-18', 23, 59)), 0)
        self.assertEqual(run_scheduled_snapshots(self.path, self.at('2026-11-19')), 2)
        self.assertEqual(run_scheduled_snapshots(self.path, self.at('2026-11-19', 0, 6)), 0)
        with self.db() as db:
            rows = db.execute("SELECT group_id,kind,scheduled_date,saved_by,report_snapshot FROM group_report_version ORDER BY group_id").fetchall()
            processed = db.execute("SELECT processed_at FROM group_report_snapshot_day").fetchone()[0]
        self.assertEqual([r[0] for r in rows], ['g1', 'g2'])
        self.assertTrue(all(r[1] == 'scheduled' and r[2] == '2026-11-18' and r[3] == '自動保存' for r in rows))
        self.assertIn('g1', rows[0][4])
        self.assertIsNotNone(processed)

    def test_missed_days_are_caught_up_and_survive_manual_pruning(self):
        self.add_day('2026-11-04')
        self.add_day('2026-11-11')
        self.assertEqual(run_scheduled_snapshots(self.path, self.at('2026-11-20')), 4)
        with self.db() as db, db:
            for number in range(3, 20):
                insert_version(db, 'g1', number)
            prune_manual_report_versions(db, 'g1')
            scheduled = db.execute("SELECT scheduled_date,version_number FROM group_report_version WHERE group_id='g1' AND kind='scheduled' ORDER BY scheduled_date").fetchall()
        self.assertEqual(scheduled, [('2026-11-04', 1), ('2026-11-11', 2)])

    def test_unchanged_draft_is_still_saved_every_day(self):
        self.add_day('2026-11-04')
        self.add_day('2026-11-11')
        run_scheduled_snapshots(self.path, self.at('2026-11-05'))
        run_scheduled_snapshots(self.path, self.at('2026-11-12'))
        with self.db() as db:
            count = db.execute("SELECT COUNT(*) FROM group_report_version WHERE group_id='g1' AND kind='scheduled'").fetchone()[0]
        self.assertEqual(count, 2)


class SnapshotDaySettingsTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_studies(db)
            db.execute('CREATE TABLE teacher(id TEXT, pwd TEXT, school TEXT, role TEXT)')
            db.execute("INSERT INTO teacher VALUES('boss','x','学校A','super_teacher'),('t','x','学校A','teacher')")
        templates = SimpleNamespace(TemplateResponse=lambda **kwargs: kwargs)
        routes = create_template_router(lambda: self.path, templates).routes
        self.save = next(r.endpoint for r in routes if r.path == '/teacher/school-report/snapshot-days' and 'POST' in r.methods)
        self.page = next(r.endpoint for r in routes if r.path == '/teacher/school-report/snapshot-days' and 'GET' in r.methods)

    def tearDown(self):
        os.remove(self.path)

    def request(self, teacher='boss'):
        return SimpleNamespace(session={'teacher_login': True, 'teacher_id': teacher, 'school_report_template_csrf': 'token'})

    def call(self, days, teacher='boss', token='token'):
        return asyncio.run(self.save(self.request(teacher), SnapshotDays(csrf_token=token, days=days)))

    def future(self, offset):
        return (snapshot_today() + datetime.timedelta(days=offset)).isoformat()

    def test_super_teacher_replaces_only_future_days(self):
        past = (snapshot_today() - datetime.timedelta(days=7)).isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO group_report_snapshot_day VALUES('学校A',?,'boss','now','now')", (past,))
            db.execute("INSERT INTO group_report_snapshot_day VALUES('学校A',?,'boss','now',NULL)", (self.future(3),))
            db.execute("INSERT INTO group_report_snapshot_day VALUES('学校B',?,'x','now',NULL)", (self.future(3),))
        result = self.call([self.future(7), self.future(14)])
        self.assertEqual([d['day'] for d in result['days']], [past, self.future(7), self.future(14)])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM group_report_snapshot_day WHERE school='学校B'").fetchone()[0], 1)
        page = asyncio.run(self.page(self.request()))
        self.assertEqual(page['name'], 'teacher/report_snapshot_days.html')
        self.assertEqual(len(page['context']['days']), 3)

    def test_rejects_today_past_bad_format_and_far_future(self):
        for value in (snapshot_today().isoformat(), self.future(-1), '2026/12/01', '2026-2-1', self.future(800)):
            with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                self.call([value])
            self.assertEqual(caught.exception.status_code, 422)

    def test_hub_shows_summary_and_editor_moved_to_child_page(self):
        routes = create_template_router(lambda: self.path, SimpleNamespace(TemplateResponse=lambda **kwargs: kwargs)).routes
        hub = next(r.endpoint for r in routes if r.path == '/teacher/school-report' and 'GET' in r.methods)
        editor = next(r.endpoint for r in routes if r.path == '/teacher/school-report/template' and 'GET' in r.methods)
        with closing(sqlite3.connect(self.path)) as db, db:
            template_id = db.execute('SELECT id FROM study_template LIMIT 1').fetchone()[0]
            db.execute("INSERT INTO school_report_template VALUES('学校A',?,'now','boss')", (template_id,))
            db.execute("INSERT INTO group_report_snapshot_day VALUES('学校A',?,'boss','now',NULL)", (self.future(14),))
            db.execute("INSERT INTO group_report_snapshot_day VALUES('学校A',?,'boss','now',NULL)", (self.future(7),))
            db.execute("INSERT INTO group_report_snapshot_day VALUES('学校A','2020-01-01','boss','now','now')")
        page = asyncio.run(hub(self.request()))
        self.assertEqual(page['name'], 'teacher/school_report.html')
        context = page['context']
        self.assertEqual(context['field_count'], 9)
        self.assertEqual((context['upcoming_count'], context['done_count']), (2, 1))
        day = snapshot_today() + datetime.timedelta(days=7)
        self.assertTrue(context['next_day'].startswith(f'{day.month}/{day.day}（'))
        redirect = asyncio.run(hub(self.request(), source_template_id=template_id))
        self.assertEqual(redirect.status_code, 303)
        self.assertEqual(redirect.headers['location'], f'/teacher/school-report/template?source_template_id={template_id}')
        edit_page = asyncio.run(editor(self.request(), source_template_id=template_id))
        self.assertEqual(edit_page['name'], 'admin/school_report_template.html')
        self.assertEqual(edit_page['context']['source_action'], '/teacher/school-report/template')
        self.assertEqual(edit_page['context']['source_template_name'], '研究レポート（基本）')
        with self.assertRaises(HTTPException):
            asyncio.run(hub(self.request('t')))

    def test_only_super_teacher_with_csrf_can_save(self):
        with self.assertRaises(HTTPException) as caught:
            self.call([self.future(7)], teacher='t')
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            self.call([self.future(7)], token='wrong')
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException):
            asyncio.run(self.page(self.request('t')))


if __name__ == '__main__':
    unittest.main()
