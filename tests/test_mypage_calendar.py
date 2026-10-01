import ast
import asyncio
import datetime
import json
import re
import sqlite3
import secrets
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from fastapi.templating import Jinja2Templates
from fastapi import Form
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from mentor_reservations import initialize_mentor_tables, build_router
from mypage_calendar import JST, calendar_reservations
from notifications import create_notification, initialize_notification_tables, reservation_body

ROOT = Path(__file__).resolve().parents[1]


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        initialize_mentor_tables(self.db)
        self.db.executescript('''
            CREATE TABLE reservation (id INTEGER PRIMARY KEY, userid TEXT, day TEXT, start_time TEXT, end_time TEXT, purpose TEXT, status TEXT);
            CREATE TABLE equipment_reservation (id INTEGER PRIMARY KEY, userid TEXT, equipment TEXT, start_day TEXT, end_day TEXT, quantity INTEGER, purpose TEXT, returned INTEGER);
            CREATE TABLE equipment_room_reservation (id INTEGER PRIMARY KEY, userid TEXT, equipment TEXT, use_day TEXT, start_time TEXT, end_time TEXT, quantity INTEGER, purpose TEXT);
            CREATE TABLE student (id TEXT, pwd TEXT, school TEXT, profile_image TEXT);
            INSERT INTO student VALUES ('s', 'secret', 'School', NULL);
            INSERT INTO mentor_profile VALUES ('a', 1, 'Mentor A', NULL, 'now', 'now');
        ''')
        self.now = datetime.datetime(2026, 9, 27, 12, 0, tzinfo=JST)

    def seed(self):
        for user in ['s', 'other']:
            self.db.execute("INSERT INTO mentor_reservation(student_id, mentor_admin_id, day, start_time, end_time, meeting_type, consultation, status, created_at) VALUES (?, 'a', '2026-09-27', '13:00', '14:00', 'online', 'Advice', 'active', 'now')", (user,))
            self.db.execute("INSERT INTO reservation(userid, day, start_time, end_time, purpose, status) VALUES (?, '2026-09-27', '14:00', '15:00', 'Work', 'active')", (user,))
            self.db.execute("INSERT INTO equipment_reservation(userid, equipment, start_day, end_day, quantity, purpose, returned) VALUES (?, 'Microscope', '2026-09-25', '2026-10-02', 2, 'Study', 0)", (user,))
            self.db.execute("INSERT INTO equipment_room_reservation(userid, equipment, use_day, start_time, end_time, quantity, purpose) VALUES (?, 'Meter', '2026-09-27', '15:00', '16:00', 1, 'Measure')", (user,))

    def events(self):
        return calendar_reservations(self.db, 's', self.now)

    def test_empty_and_student_isolation_all_sources(self):
        self.assertEqual(self.events(), [])
        self.seed()
        events = self.events()
        self.assertEqual(len(events), 4)
        self.assertEqual({e['kind'] for e in events}, {'mentor', 'room', 'takeout', 'equipment-room'})
        self.assertEqual(len({e['key'] for e in events}), 4)
        self.assertTrue(all(e['can_cancel'] for e in events))
        self.assertFalse(any(e['isMuted'] for e in events))
        self.assertEqual(next(e for e in events if e['kind'] == 'takeout')['end'], '2026-10-02')
        self.assertEqual(dict(next(e for e in events if e['kind'] == 'takeout')['details'])['数量'], 2)

    def test_history_cancelled_returned_and_end_time(self):
        self.seed()
        self.db.execute("UPDATE reservation SET day='2024-02-29'")
        self.db.execute("UPDATE mentor_reservation SET status='cancelled'")
        self.db.execute("UPDATE equipment_reservation SET returned=1")
        self.db.execute("UPDATE equipment_room_reservation SET end_time='12:00', start_time='11:00'")
        events = self.events()
        self.assertEqual(len(events), 3)
        self.assertFalse(any(e['can_cancel'] for e in events))
        self.assertTrue(all(e['isMuted'] for e in events))
        self.assertEqual(dict(next(e for e in events if e['kind'] == 'takeout')['details'])['返却状態'], '返却済み')
        self.db.execute("UPDATE reservation SET status='cancelled'")
        self.assertEqual(len(self.events()), 2)

    def test_overdue_unreturned_takeout_keeps_its_category_color(self):
        self.db.execute("INSERT INTO equipment_reservation(userid, equipment, start_day, end_day, quantity, purpose, returned) VALUES ('s', 'Microscope', '2026-09-01', '2026-09-20', 1, 'Study', 0)")
        event = self.events()[0]
        self.assertFalse(event['isMuted'])
        self.assertFalse(event['can_cancel'])

    def test_template_and_route_without_importing_mutating_main(self):
        self.seed()
        unsafe = '</script><img src=x onerror=alert(1)>'
        self.db.execute('UPDATE equipment_reservation SET purpose=?', (unsafe,))
        # Load only the route AST: importing main would initialize the real database.
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'Mypage')
        route.decorator_list = []
        events = self.events()
        namespace = dict(Request=Request, cursor=self.db.cursor(), datetime=datetime, JST=JST,
            closing=closing, sqlite3=SimpleNamespace(connect=lambda _: sqlite3.connect(':memory:')),
            DATABASE_PATH='unused', calendar_reservations=lambda db, user, now: events if user == 's' else [],
            templates=Jinja2Templates(directory=str(ROOT / 'templates')),
            profile_image_url=lambda _: '/static/default.png',
            mentor_csrf_token=lambda *args: 'mentor-token', reservation_csrf_token=lambda *args: 'room-token')
        exec(compile(ast.Module(body=[route], type_ignores=[]), 'main.py', 'exec'), namespace)
        request = Request({'type': 'http', 'method': 'GET', 'scheme': 'http', 'path': '/mypage',
            'server': ('test', 80), 'headers': [], 'query_string': b'',
            'session': {'user_id': 's', 'user_login': True, 'mypage_reservation_notice': {'type': 'success', 'message': 'Cancelled'}}})
        response = asyncio.run(namespace['Mypage'](request))
        html = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="csrf" value="mentor-token"', html)
        self.assertIn('name="csrf_token" value="room-token"', html)
        for event in events:
            self.assertIn(event['cancel_url'], html)
        self.assertNotIn(unsafe, html)
        payload = re.search(r'<script id="mypage-calendar-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(len(json.loads(payload)), 4)
        self.assertEqual(dict(next(e for e in json.loads(payload) if e['kind'] == 'takeout')['details'])['使用目的'], unsafe)
        self.assertLess(html.index('id="mypage-calendar"'), html.index('プロフィール情報</h2>'))
        self.assertEqual(html.count('class="reservation-expanded-details"'), len(events))
        self.assertEqual(html.count('<summary>詳細を表示</summary>'), len(events))
        self.assertIn('class="mypage-detail-scroll"', html)
        self.assertIn('class="student-panel student-mypage-profile"', html)
        self.assertNotIn('mypage_reservation_notice', request.session)
        self.assertNotIn('secret', html)

    def test_past_and_returned_cards_have_no_cancel_controls(self):
        self.seed()
        self.db.execute("UPDATE reservation SET day='2020-01-01'")
        self.db.execute("UPDATE equipment_reservation SET returned=1")
        env = Jinja2Templates(directory=str(ROOT / 'templates')).env
        html = env.get_template('mypage.html').render(request=SimpleNamespace(url=SimpleNamespace(path='/mypage')),
            calendar_events=self.events(), calendar_today='2026-09-27')
        for event in self.events():
            card = re.search(r'<article id="detail-' + event['key'] + r'".*?</article>', html, re.S).group(0)
            self.assertEqual('class="cancel-reservation"' in card, event['can_cancel'])

    def test_existing_cancel_routes_ownership_csrf_and_notifications(self):
        self.seed()
        for column in ['cancelled_at', 'cancelled_by_type', 'cancelled_by_id']:
            self.db.execute(f'ALTER TABLE reservation ADD COLUMN {column} TEXT')
        initialize_notification_tables(self.db)
        self.db.commit()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'calendar.db'
            with closing(sqlite3.connect(path)) as target:
                self.db.backup(target)
            names = {'CancelReservation', 'CancelEquipmentReservation', 'CancelEquipmentRoomReservation', 'valid_reservation_csrf'}
            nodes = [node for node in ast.parse((ROOT / 'main.py').read_text(encoding='utf-8')).body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
            for node in nodes:
                node.decorator_list = []
            namespace = dict(Request=Request, Form=Form, RedirectResponse=RedirectResponse,
                sqlite3=sqlite3, closing=closing, DATABASE_PATH=path, datetime=datetime, JST=JST,
                secrets=secrets, create_notification=create_notification, reservation_body=reservation_body)
            exec(compile(ast.Module(body=nodes, type_ignores=[]), 'main.py', 'exec'), namespace)
            request = Request({'type': 'http', 'session': {'user_id': 's', 'mypage_reservation_csrf_token': 'token', 'mypage_mentor_csrf': 'token'}})
            mentor_cancel = next(route.endpoint for route in build_router(path, None).routes
                                 if route.path == '/mypage/mentor-reservation/{reservation_id}/cancel')
            # Bad CSRF cannot cancel room or mentor bookings.
            asyncio.run(namespace['CancelReservation'](request, 1, 'bad'))
            asyncio.run(mentor_cancel(request, 1, 'bad'))
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT status FROM reservation WHERE id=1').fetchone()[0], 'active')
                self.assertEqual(db.execute('SELECT status FROM mentor_reservation WHERE id=1').fetchone()[0], 'active')
            # Another student's IDs must remain unchanged for every cancellation route.
            for name in ['CancelEquipmentReservation', 'CancelEquipmentRoomReservation']:
                asyncio.run(namespace[name](request, 2))
            asyncio.run(namespace['CancelReservation'](request, 2, 'token'))
            asyncio.run(mentor_cancel(request, 2, 'token'))
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(len(calendar_reservations(db, 'other', self.now)), 4)
            for name in ['CancelEquipmentReservation', 'CancelEquipmentRoomReservation']:
                self.assertEqual(asyncio.run(namespace[name](request, 1)).status_code, 303)
            asyncio.run(namespace['CancelReservation'](request, 1, request.session['mypage_reservation_csrf_token']))
            asyncio.run(mentor_cancel(request, 1, request.session['mypage_mentor_csrf']))
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(calendar_reservations(db, 's', self.now), [])
                self.assertEqual(db.execute('SELECT count(*) FROM notification').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
