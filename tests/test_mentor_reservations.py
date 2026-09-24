import asyncio
import tempfile
from pathlib import Path
from starlette.requests import Request
from fastapi import HTTPException

import datetime
import sqlite3
import unittest

from mentor_reservations import (
    build_router,
    initialize_mentor_tables,
    mentor_reservations_for_student,
    slot_range,
    validate_range,
)


class MentorReservationUnitTests(unittest.TestCase):
    def test_schema_is_idempotent_and_preserves_rows(self):
        db = sqlite3.connect(":memory:")
        initialize_mentor_tables(db)
        now = datetime.datetime.now().isoformat()
        db.execute("INSERT INTO mentor_profile(admin_id,is_published,display_name,created_at,updated_at) VALUES('a',1,'A',?,?)", (now, now))
        initialize_mentor_tables(db)
        self.assertEqual(db.execute("SELECT display_name FROM mentor_profile WHERE admin_id='a'").fetchone()[0], "A")

    def test_slot_range_uses_half_open_30_minute_slots(self):
        self.assertEqual(slot_range("13:00", "14:30"), ["13:00", "13:30", "14:00"])

    def test_today_and_invalid_duration_are_rejected(self):
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()
        with self.assertRaises(ValueError):
            validate_range(today, "13:00", "13:30")
        future = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        with self.assertRaises(ValueError):
            validate_range(future, "13:10", "14:00")
        with self.assertRaises(ValueError):
            validate_range(future, "13:00", "16:30", max_minutes=180)

    def test_student_history_includes_cancelled_rows(self):
        db = sqlite3.connect(":memory:")
        initialize_mentor_tables(db)
        now = datetime.datetime.now().isoformat()
        db.execute("INSERT INTO mentor_profile(admin_id,is_published,display_name,created_at,updated_at) VALUES('a',1,'メンターA',?,?)", (now, now))
        db.execute("""INSERT INTO mentor_reservation(student_id,mentor_admin_id,day,start_time,end_time,meeting_type,consultation,status,created_at)
            VALUES('s','a','2099-01-01','13:00','13:30','online','相談','cancelled',?)""", (now,))
        rows = mentor_reservations_for_student(db, "s")
        self.assertEqual(rows[0]["mentor_name"], "メンターA")
        self.assertEqual(rows[0]["status"], "cancelled")


class MentorAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        self.day = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date() + datetime.timedelta(days=2)).isoformat()
        with sqlite3.connect(self.path) as db:
            initialize_mentor_tables(db)
            db.execute("INSERT INTO mentor_profile VALUES ('a',1,'A',NULL,'now','now')")
            db.executemany("INSERT INTO mentor_available_slot(admin_id,day,start_time,online_available,offline_available,created_at,updated_at) VALUES('a',?,?,?,?,'now','now')",
                           [(self.day, '13:00', 1, 0), (self.day, '13:30', 1, 1), (self.day, '14:00', 0, 1)])
        db.close()
        self.routes = {r.path: r.endpoint for r in build_router(self.path, None).routes if 'GET' in r.methods}
        self.request = Request({'type': 'http', 'session': {'user_id': 's'}})

    def call(self, suffix, **kwargs):
        return asyncio.run(self.routes['/mentor-reservation/{admin_id}/' + suffix](self.request, 'a', **kwargs))

    def test_format_and_calendar(self):
        slots = self.call('availability', day=self.day, meeting_type='online')['slots']
        self.assertEqual(len(slots), 26)
        self.assertEqual([s['start_time'] for s in slots if s['state'] == 'available'], ['13:00', '13:30'])
        self.assertEqual(slots[-1]['end_time'], '22:00')
        self.assertEqual(self.call('available-days', month=self.day[:7], meeting_type='offline')['available_days'], [self.day])

    def test_other_mentor_student_conflict_and_cancelled(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO mentor_reservation(student_id,mentor_admin_id,day,start_time,end_time,meeting_type,consultation,status,created_at) VALUES('s','b',?,'13:00','14:00','offline','test','active','now')", (self.day,))
        db.close()
        self.assertEqual(self.call('available-days', month=self.day[:7], meeting_type='online')['available_days'], [])
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE mentor_reservation SET status='cancelled'")
        db.close()
        self.assertEqual(self.call('available-days', month=self.day[:7], meeting_type='online')['available_days'], [self.day])

    def test_invalid_day_format_and_hidden_mentor(self):
        for kwargs in ({'day': 'bad', 'meeting_type': 'online'}, {'day': self.day, 'meeting_type': 'bad'},
                       {'day': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat(), 'meeting_type': 'online'}):
            with self.assertRaises(HTTPException) as error:
                self.call('availability', **kwargs)
            self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(HTTPException):
            self.call('available-days', month='bad', meeting_type='online')
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE mentor_profile SET is_published=0")
        db.close()
        with self.assertRaises(HTTPException) as error:
            self.call('available-days', month=self.day[:7], meeting_type='online')
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
