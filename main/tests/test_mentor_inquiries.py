import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from admin_management import initialize_admin_roles
from mentor_inquiries import (
    admin_unread_inquiry_count,
    build_router,
    delete_mentor_inquiries_for_admin,
    initialize_mentor_inquiry_tables,
    student_thread_messages,
    student_unread_counts,
)
from mentor_reservations import build_router as build_mentor_router, initialize_mentor_tables
from notifications import initialize_notification_tables


class _AllFeatures(dict):
    def __getattr__(self, name):
        return True


TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates"),
    context_processors=[lambda request: {"student_features": _AllFeatures(), "unread_notification_count": 0,
                                         "is_super_admin": False, "mentor_inquiry_unread_count": 0}])


def make_request(session, path="/"):
    return Request({"type": "http", "session": session, "method": "GET", "path": path,
                    "headers": [], "query_string": b"", "scheme": "http",
                    "server": ("test", 80), "root_path": ""})


class MentorInquiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        with closing(sqlite3.connect(self.path)) as db:
            initialize_mentor_tables(db)
            initialize_admin_roles(db)
            initialize_notification_tables(db)
            initialize_mentor_inquiry_tables(db)
            initialize_mentor_inquiry_tables(db)  # idempotent
            db.execute("CREATE TABLE IF NOT EXISTS student(id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL)")
            db.execute("INSERT INTO student VALUES('s','x','school')")
            db.execute("INSERT INTO admin(id,pwd,name,role) VALUES('a','x','A','admin')")
            db.execute("INSERT INTO admin(id,pwd,name,role) VALUES('b','x','B','admin')")
            # Published mentor without any available slots.
            db.execute("INSERT INTO mentor_profile(admin_id,is_published,display_name,created_at,updated_at) VALUES('a',1,'Mentor A','now','now')")
            db.commit()
        routes = build_router(self.path, TEMPLATES).routes
        self.endpoints = {(r.path, tuple(sorted(r.methods))): r.endpoint for r in routes}

    def endpoint(self, path, method):
        return self.endpoints[(path, (method,))]

    def student(self):
        return make_request({"user_id": "s", "user_login": True, "mentor_inquiry_csrf": "tok"})

    def admin(self, admin_id="a"):
        return make_request({"admin_id": admin_id, "admin_login": True, "admin_mentor_inquiry_csrf": "atok"})

    def send(self, body="質問です", request=None, mentor="a"):
        request = request or self.student()
        response = asyncio.run(self.endpoint("/mentor-reservation/{admin_id}/inquiry", "POST")(
            request, mentor, body, request.session.get("mentor_inquiry_csrf", "")))
        return request, response

    def reply(self, thread_id, body="返信です", admin_id="a"):
        request = self.admin(admin_id)
        response = asyncio.run(self.endpoint("/admin/mentor-inquiries/{thread_id}/reply", "POST")(
            request, thread_id, body, "atok"))
        return request, response

    def thread_id(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT id FROM mentor_inquiry_thread").fetchone()[0]

    def test_student_can_inquire_mentor_without_slots_and_admin_is_notified(self):
        request, response = self.send()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/mentor-reservation/a/inquiry#inquiry-form")
        self.assertEqual(request.session["mentor_inquiry_notice"]["type"], "success")
        self.send("2通目")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mentor_inquiry_thread").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mentor_inquiry_message WHERE sender_type='student'").fetchone()[0], 2)
            notices = db.execute("SELECT recipient_admin_id,notification_type,body FROM admin_notification").fetchall()
            self.assertEqual(len(notices), 2)
            self.assertEqual(notices[0][:2], ("a", "mentor_inquiry_received"))
            self.assertIn("質問です", notices[0][2])
            self.assertEqual(admin_unread_inquiry_count(db, "a"), 2)
            self.assertEqual(admin_unread_inquiry_count(db, "b"), 0)

    def test_validation_csrf_and_unpublished_mentor(self):
        bad = make_request({"user_id": "s", "mentor_inquiry_csrf": "tok"})
        asyncio.run(self.endpoint("/mentor-reservation/{admin_id}/inquiry", "POST")(bad, "a", "hi", "wrong"))
        self.assertEqual(bad.session["mentor_inquiry_notice"]["type"], "error")
        for body in ("   ", "x" * 2001):
            request, _ = self.send(body)
            self.assertEqual(request.session["mentor_inquiry_notice"]["type"], "error")
        with self.assertRaises(HTTPException) as error:
            self.send(mentor="b")  # no published profile
        self.assertEqual(error.exception.status_code, 404)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE admin SET deleted_at='now' WHERE id='a'")
            db.commit()
        with self.assertRaises(HTTPException):
            self.send()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mentor_inquiry_message").fetchone()[0], 0)

    def test_reply_notifies_student_and_tracks_read_state(self):
        self.send()
        thread_id = self.thread_id()
        request, response = self.reply(thread_id)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(request.session["admin_mentor_inquiry_notice"]["type"], "success")
        with closing(sqlite3.connect(self.path)) as db:
            notice = db.execute("SELECT recipient_user_id,notification_type,related_reservation_type,sender_name,body FROM notification").fetchone()
            self.assertEqual(notice[:4], ("s", "mentor_inquiry_reply", "mentor_inquiry", "Mentor A"))
            self.assertIn("返信です", notice[4])
            # Replying marks the student's messages read for the mentor.
            self.assertEqual(admin_unread_inquiry_count(db, "a"), 0)
            self.assertEqual(student_unread_counts(db, "s"), {"a": 1})
            messages = student_thread_messages(db, "s", "a")
            self.assertEqual([m["sender_type"] for m in messages], ["student", "admin"])
            self.assertEqual(student_unread_counts(db, "s"), {})

    def test_other_mentor_cannot_view_or_reply(self):
        self.send()
        thread_id = self.thread_id()
        with self.assertRaises(HTTPException) as error:
            asyncio.run(self.endpoint("/admin/mentor-inquiries/{thread_id}", "GET")(self.admin("b"), thread_id))
        self.assertEqual(error.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            self.reply(thread_id, admin_id="b")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mentor_inquiry_message WHERE sender_type='admin'").fetchone()[0], 0)

    def test_admin_pages_render_and_mark_read(self):
        self.send("改行\nあり")
        thread_id = self.thread_id()
        listing = asyncio.run(self.endpoint("/admin/mentor-inquiries", "GET")(self.admin()))
        self.assertIn("未読 1", listing.body.decode())
        page = asyncio.run(self.endpoint("/admin/mentor-inquiries/{thread_id}", "GET")(self.admin(), thread_id))
        self.assertIn("改行\nあり", page.body.decode())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(admin_unread_inquiry_count(db, "a"), 0)
        other = asyncio.run(self.endpoint("/admin/mentor-inquiries", "GET")(self.admin("b")))
        self.assertIn("問い合わせはありません", other.body.decode())

    def test_student_pages_show_thread_and_unread_badge(self):
        self.send()
        self.reply(self.thread_id())
        routes = {r.path: r.endpoint for r in build_mentor_router(self.path, TEMPLATES, Path(self.temp.name) / "u").routes
                  if "GET" in r.methods}
        listing = asyncio.run(routes["/mentor-reservation"](self.student())).body.decode()
        self.assertIn("問い合わせる", listing)
        self.assertIn("mentor-unread-badge", listing)
        self.assertIn('href="/mentor-reservation/a/inquiry"', listing)
        # The booking page links to the inquiry page but no longer embeds the thread
        # (and viewing it must not mark replies as read).
        book = asyncio.run(routes["/mentor-reservation/{admin_id}"](self.student(), "a")).body.decode()
        self.assertNotIn("返信です", book)
        self.assertIn('href="/mentor-reservation/a/inquiry"', book)
        listing = asyncio.run(routes["/mentor-reservation"](self.student())).body.decode()
        self.assertIn("mentor-unread-badge", listing)
        page = asyncio.run(self.endpoint("/mentor-reservation/{admin_id}/inquiry", "GET")(self.student(), "a")).body.decode()
        self.assertIn("返信です", page)
        self.assertIn('action="/mentor-reservation/a/inquiry"', page)
        with self.assertRaises(HTTPException):
            asyncio.run(self.endpoint("/mentor-reservation/{admin_id}/inquiry", "GET")(self.student(), "b"))
        listing = asyncio.run(routes["/mentor-reservation"](self.student())).body.decode()
        self.assertNotIn("mentor-unread-badge", listing)

    def test_delete_admin_removes_threads_and_reply_notifications(self):
        self.send()
        self.reply(self.thread_id())
        with closing(sqlite3.connect(self.path)) as db:
            delete_mentor_inquiries_for_admin(db, "a")
            db.commit()
            for table in ("mentor_inquiry_thread", "mentor_inquiry_message", "notification"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
