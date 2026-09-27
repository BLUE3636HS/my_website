import ast
import asyncio
import datetime
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from starlette.requests import Request
import test_mypage_calendar as student_tests
from mypage_calendar import admin_calendar_reservations, JST

ROOT = Path(__file__).resolve().parents[1]


class AdminCalendarTests(unittest.TestCase):
    def setUp(self):
        self.fixture = student_tests.CalendarTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.now = self.fixture.now

    def test_all_students_and_missing_student_with_four_queries(self):
        self.assertEqual(admin_calendar_reservations(self.db, self.now), [])
        self.fixture.seed()
        queries = []
        self.db.set_trace_callback(queries.append)
        events = admin_calendar_reservations(self.db, self.now)
        self.db.set_trace_callback(None)
        self.assertEqual(len(queries), 4)
        self.assertEqual(len(events), 8)
        self.assertEqual(len({e['key'] for e in events}), 8)
        self.assertEqual({e['studentId'] for e in events}, {'s', 'other'})
        for event in events:
            self.assertFalse(event['can_cancel'])
            self.assertNotIn('cancel_url', event)
            self.assertEqual(dict(event['details'])['在籍校'], 'School' if event['studentId'] == 's' else '不明')
            self.assertTrue(event['management_url'].startswith('/admin/'))
        # Normalized reservation content and muted rules agree with the student page.
        student = self.fixture.events()
        own = [e for e in events if e['studentId'] == 's']
        for a, b in zip(student, own):
            for key in ['key', 'title', 'start', 'end', 'startTime', 'endTime', 'isMuted']:
                self.assertEqual(a[key], b[key])
            self.assertEqual(a['details'], b['details'][2:])

    def test_history_and_mentor_name_fallback(self):
        self.fixture.seed()
        self.db.execute("UPDATE reservation SET status='cancelled'")
        self.db.execute("UPDATE mentor_reservation SET status='cancelled' WHERE student_id='other'")
        self.db.execute("DELETE FROM mentor_profile")
        self.db.execute("UPDATE equipment_reservation SET end_day='2026-09-26', returned=(userid='s')")
        self.db.execute("UPDATE equipment_room_reservation SET end_time='12:00'")
        events = admin_calendar_reservations(self.db, self.now)
        self.assertEqual(len(events), 5)
        self.assertEqual(next(e['title'] for e in events if e['kind'] == 'mentor'), 'a')
        for event in events:
            if event['kind'] == 'takeout':
                self.assertEqual(event['isMuted'], event['studentId'] == 's')
            elif event['kind'] == 'equipment-room':
                self.assertTrue(event['isMuted'])

    def test_route_auth_profile_isolation_and_safe_template(self):
        self.fixture.seed()
        unsafe = '</script><img src=x onerror=alert(1)>'
        self.db.execute('UPDATE equipment_reservation SET purpose=?', (unsafe,))
        self.db.execute("UPDATE mentor_profile SET description='Own description'")
        self.db.execute("INSERT INTO mentor_profile VALUES ('b', 0, 'Other mentor', 'PRIVATE OTHER DESCRIPTION', 'now', 'now')")
        self.db.commit()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.db'
            with closing(sqlite3.connect(path)) as target:
                self.db.backup(target)
            tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
            route = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'AdminMypage')
            route.decorator_list = []
            namespace = dict(Request=Request, RedirectResponse=RedirectResponse, datetime=datetime, JST=JST,
                closing=closing, sqlite3=sqlite3, DATABASE_PATH=path,
                admin_calendar_reservations=admin_calendar_reservations,
                templates=Jinja2Templates(directory=str(ROOT / 'templates')))
            exec(compile(ast.Module(body=[route], type_ignores=[]), 'main.py', 'exec'), namespace)

            def request(session):
                return Request({'type': 'http', 'method': 'GET', 'scheme': 'http', 'path': '/admin/mypage',
                    'server': ('test', 80), 'headers': [], 'query_string': b'', 'session': session})

            for session in [{}, {'user_login': True}, {'teacher_login': True}, {'admin_login': False}]:
                response = asyncio.run(namespace['AdminMypage'](request(session)))
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers['location'], '/admin/login')
            response = asyncio.run(namespace['AdminMypage'](request({'admin_login': True, 'admin_id': 'a'})))
            html = response.body.decode()
            self.assertEqual(response.status_code, 200)
            self.assertIn('Own description', html)
            self.assertNotIn('PRIVATE OTHER DESCRIPTION', html)
            self.assertNotIn(unsafe, html)
            self.assertNotIn('reservation-cancel-form', html)
            self.assertIn('href="/admin/mypage" aria-current="page"', html)
            self.assertLess(html.index('class="nav-link admin-mypage-link"'), html.index('class="logout-link"'))
            payload = re.search(r'<script id="mypage-calendar-data" type="application/json">(.*?)</script>', html, re.S)
            events = json.loads(payload.group(1))
            self.assertEqual(len(events), 8)
            self.assertTrue(any(unsafe == value for e in events for _, value in e['details']))
            self.assertIn('/static/mypage.js', html)
            for url in ['/admin/reservation', '/admin/equipment-reservation', '/admin/mentor-reservations?scope=all']:
                self.assertIn('href="' + url + '"', html)
            response = asyncio.run(namespace['AdminMypage'](request({'admin_login': True, 'admin_id': 'unregistered'})))
            self.assertIn('未登録', response.body.decode())
            self.assertNotIn('Own description', response.body.decode())


if __name__ == '__main__':
    unittest.main()

