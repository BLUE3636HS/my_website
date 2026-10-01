import asyncio
import io
import tempfile
from pathlib import Path
from starlette.requests import Request
from starlette.datastructures import Headers, UploadFile
from fastapi import HTTPException

import datetime
import sqlite3
import unittest
from contextlib import closing

from mentor_reservations import (
    build_router,
    initialize_mentor_tables,
    mentor_reservations_for_student,
    mentor_image_url,
    process_mentor_image,
    slot_range,
    validate_range,
)


class MentorReservationUnitTests(unittest.TestCase):
    def test_schema_is_idempotent_and_preserves_rows(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        initialize_mentor_tables(db)
        now = datetime.datetime.now().isoformat()
        db.execute("INSERT INTO mentor_profile(admin_id,is_published,display_name,created_at,updated_at) VALUES('a',1,'A',?,?)", (now, now))
        initialize_mentor_tables(db)
        self.assertEqual(db.execute("SELECT display_name FROM mentor_profile WHERE admin_id='a'").fetchone()[0], "A")
        self.assertIn("profile_image", {row[1] for row in db.execute("PRAGMA table_info(mentor_profile)")})

    def test_mentor_image_is_normalized_to_square_webp(self):
        from PIL import Image

        source = io.BytesIO()
        Image.new("RGB", (800, 400), "#336699").save(source, format="PNG")
        processed, error = process_mentor_image(source.getvalue())
        self.assertIsNone(error)
        with Image.open(io.BytesIO(processed)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (512, 512))

    def test_invalid_mentor_image_and_filename_use_safe_defaults(self):
        processed, error = process_mentor_image(b"not an image")
        self.assertIsNone(processed)
        self.assertTrue(error)
        self.assertEqual(mentor_image_url("../unsafe.webp"), "/static/images/default_profile.svg")
        self.assertEqual(mentor_image_url(None), "/static/images/default_profile.svg")

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
        self.addCleanup(db.close)
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
            db.execute("INSERT INTO mentor_profile(admin_id,is_published,display_name,description,created_at,updated_at) VALUES ('a',1,'A',NULL,'now','now')")
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


class MentorProfileUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = root / "test.db"
        self.uploads = root / "uploads"
        with closing(sqlite3.connect(self.path)) as db:
            initialize_mentor_tables(db)
            db.commit()
        routes = build_router(self.path, None, self.uploads).routes
        self.update = next(r.endpoint for r in routes if r.path == "/admin/mentor-profile" and "POST" in r.methods)
        self.delete = next(r.endpoint for r in routes if r.path == "/admin/mentor-profile/image/delete")
        self.request = Request({"type": "http", "session": {"admin_id": "a", "mentor_profile_csrf": "token"}})

    def image_upload(self):
        from PIL import Image

        source = io.BytesIO()
        Image.new("RGB", (640, 480), "#669933").save(source, format="PNG")
        return UploadFile(file=io.BytesIO(source.getvalue()), filename="mentor.png",
                          headers=Headers({"content-type": "image/png"}))

    def test_profile_description_image_preservation_and_delete(self):
        response = asyncio.run(self.update(
            self.request, display_name=" Mentor A ", description="Line 1\nLine 2",
            is_published="1", profile_image=self.image_upload(), csrf="token"))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT display_name,description,is_published,profile_image FROM mentor_profile WHERE admin_id='a'").fetchone()
        self.assertEqual(row[:3], ("Mentor A", "Line 1\nLine 2", 1))
        image_path = self.uploads / row[3]
        self.assertTrue(image_path.is_file())

        next_token = self.request.session["mentor_profile_csrf"]
        asyncio.run(self.update(
            self.request, display_name="Mentor A", description="Updated", is_published="1",
            profile_image=None, csrf=next_token))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT profile_image FROM mentor_profile WHERE admin_id='a'").fetchone()[0], row[3])

        delete_token = self.request.session["mentor_profile_csrf"]
        response = asyncio.run(self.delete(self.request, csrf=delete_token))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT profile_image FROM mentor_profile WHERE admin_id='a'").fetchone()[0])
        self.assertFalse(image_path.exists())

    def test_description_over_limit_is_rejected(self):
        response = asyncio.run(self.update(
            self.request, display_name="Mentor A", description="x" * 501,
            is_published="1", profile_image=None, csrf="token"))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM mentor_profile WHERE admin_id='a'").fetchone())


if __name__ == "__main__":
    unittest.main()
