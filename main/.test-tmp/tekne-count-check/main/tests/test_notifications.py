import asyncio
import datetime
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

import main
from notifications import create_notification, create_reservation_reminders, initialize_notification_tables


SCHEMA = """
CREATE TABLE student (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL, profile_image TEXT);
CREATE TABLE teacher (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL);
CREATE TABLE admin (id TEXT PRIMARY KEY, pwd TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin', deleted_at TEXT, session_version INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT '');
CREATE TABLE reservation (id INTEGER PRIMARY KEY AUTOINCREMENT, userid TEXT NOT NULL, day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, purpose TEXT NOT NULL, people_count INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active', created_at TEXT, cancelled_at TEXT, cancelled_by_type TEXT, cancelled_by_id TEXT);
CREATE TABLE reservation_available_slot (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id TEXT NOT NULL, day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL DEFAULT '10:30', created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '', UNIQUE(admin_id, day, start_time));
CREATE TABLE equipment_reservation (id INTEGER PRIMARY KEY AUTOINCREMENT, userid TEXT NOT NULL, equipment TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL, quantity INTEGER NOT NULL, purpose TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', equipment_id TEXT, returned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE equipment_room_reservation (id INTEGER PRIMARY KEY AUTOINCREMENT, userid TEXT NOT NULL, equipment_id TEXT NOT NULL, equipment TEXT NOT NULL, use_day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, quantity INTEGER NOT NULL, purpose TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', returned INTEGER NOT NULL DEFAULT 0);
"""


def request(path, session, method="GET"):
    return Request({
        "type": "http", "method": method, "path": path, "root_path": "",
        "scheme": "http", "server": ("test", 80), "client": ("test", 1),
        "headers": [], "query_string": b"", "session": session
    })


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript(SCHEMA)
            initialize_notification_tables(db)
            db.executemany("INSERT INTO student (id, pwd, school) VALUES (?, 'x', ?)", [
                ("a", "school-1"), ("b", "school-1"), ("c", "school-2")
            ])
            db.executemany("INSERT INTO teacher (id, pwd, school) VALUES (?, 'x', ?)", [
                ("teacher-1", "school-1"), ("teacher-2", "school-1"),
                ("teacher-3", "school-2")
            ])
            db.executemany("INSERT INTO admin (id, pwd, role) VALUES (?, 'x', ?)", [
                ("super", "super_admin"), ("admin", "admin")
            ])
            db.commit()
        self.patch = patch.object(main, "DATABASE_PATH", self.db_path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def await_result(self, awaitable):
        return asyncio.run(awaitable)

    def send(self, target_type, student_id="", school=""):
        session = {"admin_login": True, "admin_id": "admin", "admin_notification_csrf_token": "token"}
        return self.await_result(main.AdminSendNotification(
            request("/admin/notifications/send", session, "POST"), target_type,
            student_id, school, "お知らせ", "本文", "token"
        ))

    def test_manual_targets_and_batch_history(self):
        self.send("student", " a ")
        self.send("school", school="school-1")
        self.send("all")
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notification_batch").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT notification_count FROM notification_batch ORDER BY id").fetchall(), [(1,), (2,), (3,)])
            self.assertEqual(
                db.execute("SELECT DISTINCT sender_type, sender_name, sender_id FROM notification_batch").fetchall(),
                [("admin", "管理者", "admin")],
            )
            self.assertEqual(db.execute("SELECT recipient_user_id, COUNT(*) FROM notification GROUP BY recipient_user_id ORDER BY recipient_user_id").fetchall(), [("a", 3), ("b", 2), ("c", 1)])

    def test_admin_notification_page_requires_admin_session(self):
        middleware = main.LoginCheckMiddleware(main.app)
        response = self.await_result(middleware.dispatch(
            request("/admin/notifications", {}), lambda _: None
        ))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/login")

    def teacher_send(self, teacher_id, target_type, student_id="", token="token"):
        session = {
            "teacher_login": True,
            "teacher_id": teacher_id,
            "teacher_notification_csrf_token": "token",
        }
        return self.await_result(main.TeacherSendNotification(
            request("/teacher/notifications/send", session, "POST"),
            target_type, student_id, "先生からのお知らせ", "本文", token
        ))

    def test_teacher_individual_and_all_targets_are_school_scoped(self):
        self.teacher_send("teacher-1", "student", "a")
        self.teacher_send("teacher-1", "all")
        rejected = self.teacher_send("teacher-1", "student", "c")
        self.assertEqual(rejected.status_code, 303)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(
                db.execute("SELECT recipient_user_id, COUNT(*) FROM notification GROUP BY recipient_user_id ORDER BY recipient_user_id").fetchall(),
                [("a", 2), ("b", 1)],
            )
            self.assertEqual(
                db.execute("SELECT sender_type, sender_name FROM notification ORDER BY id LIMIT 1").fetchone(),
                ("teacher", "先生: teacher-1"),
            )
            self.assertEqual(
                db.execute("SELECT target_label, notification_count, sender_school, sender_id FROM notification_batch ORDER BY id").fetchall(),
                [("個人: a", 1, "school-1", "teacher-1"), ("自校の全生徒", 2, "school-1", "teacher-1")],
            )

    def test_teacher_history_is_shared_only_with_same_school(self):
        self.teacher_send("teacher-1", "student", "a")
        self.teacher_send("teacher-2", "student", "b")
        self.teacher_send("teacher-3", "student", "c")
        session = {
            "teacher_login": True, "teacher_id": "teacher-1",
            "teacher_notification_csrf_token": "token",
        }
        response = self.await_result(main.TeacherNotifications(
            request("/teacher/notifications", session), 1
        ))
        html = response.body.decode("utf-8")
        self.assertIn("teacher-1", html)
        self.assertIn("teacher-2", html)
        self.assertNotIn("teacher-3", html)

    def test_teacher_send_rejects_invalid_csrf_and_target_type(self):
        with self.assertRaises(main.HTTPException) as error:
            self.teacher_send("teacher-1", "all", token="wrong")
        self.assertEqual(error.exception.status_code, 403)
        self.teacher_send("teacher-1", "school")
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notification").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notification_batch").fetchone()[0], 0)

    def test_notification_schema_migration_is_idempotent(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            initialize_notification_tables(db)
            initialize_notification_tables(db)
            columns = {row[1] for row in db.execute("PRAGMA table_info(notification_batch)")}
        self.assertIn("sender_school", columns)
        self.assertIn("sender_id", columns)

    def test_read_ownership_and_read_all_scope(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            own_id = create_notification(db, "a", "A", "body")
            other_id = create_notification(db, "b", "B", "body")
            db.commit()
        own_request = request(f"/notifications/{own_id}", {"user_login": True, "user_id": "a"})
        self.await_result(main.NotificationDetail(own_request, own_id))
        with self.assertRaises(main.HTTPException) as error:
            self.await_result(main.NotificationDetail(own_request, other_id))
        self.assertEqual(error.exception.status_code, 404)
        own_request.session["notification_csrf_token"] = "token"
        self.await_result(main.ReadAllNotifications(own_request, "token"))
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT is_read FROM notification WHERE id = ?", (own_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT is_read FROM notification WHERE id = ?", (other_id,)).fetchone()[0], 0)

    def test_dashboard_shows_only_current_students_unread_notifications(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            create_notification(db, "a", "表示する未読通知", "body")
            read_id = create_notification(db, "a", "表示しない既読通知", "body")
            create_notification(db, "b", "他の生徒の通知", "body")
            db.execute("UPDATE notification SET is_read = 1 WHERE id = ?", (read_id,))
            db.commit()
        response = self.await_result(main.Dashboard(
            request("/dashboard", {"user_login": True, "user_id": "a"})
        ))
        html = response.body.decode("utf-8")
        self.assertIn("表示する未読通知", html)
        self.assertNotIn("表示しない既読通知", html)
        self.assertNotIn("他の生徒の通知", html)

    def test_tekne_create_and_cancel_notifications(self):
        future = (datetime.datetime.now(main.JST).date() + datetime.timedelta(days=3)).isoformat()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executemany("INSERT INTO reservation_available_slot (admin_id, day, start_time) VALUES ('system', ?, ?)",
                           [(future, value) for value in ("10:00", "10:30", "11:00")])
            db.commit()
        student = request("/reservation/date", {
            "user_login": True, "user_id": "a", "reservation_csrf_token": "token"
        }, "POST")
        response = self.await_result(main.ReservationDate(
            student, future, "10:00", "11:00", "研究", "2", "token"
        ))
        self.assertTrue(response["result"])
        with closing(sqlite3.connect(self.db_path)) as db:
            created = db.execute("SELECT notification_type FROM notification WHERE recipient_user_id = 'a'").fetchall()
            reservation_id = db.execute("SELECT id FROM reservation").fetchone()[0]
        self.assertEqual(created, [("reservation_created",)])
        student.session["mypage_reservation_csrf_token"] = "cancel-token"
        self.await_result(main.CancelReservation(student, reservation_id, "cancel-token"))
        with closing(sqlite3.connect(self.db_path)) as db:
            types = db.execute("SELECT notification_type FROM notification WHERE recipient_user_id = 'a' ORDER BY id").fetchall()
            self.assertEqual(types, [("reservation_created",), ("reservation_cancelled",)])
            self.assertEqual(db.execute("SELECT status FROM reservation").fetchone()[0], "cancelled")

    def test_workshop_reservations_allow_other_students_but_reject_own_overlap(self):
        future = (datetime.datetime.now(main.JST).date() + datetime.timedelta(days=3)).isoformat()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executemany("INSERT INTO reservation_available_slot (admin_id, day, start_time) VALUES ('system', ?, ?)",
                           [(future, value) for value in ("10:00", "10:30", "11:00", "11:30")])
            db.commit()
        def reserve(student, start, end):
            response = self.await_result(main.ReservationDate(
                request("/reservation/date", {"user_login": True, "user_id": student,
                                               "reservation_csrf_token": "token"}, "POST"),
                future, start, end, "研究", "1", "token"
            ))
            return json.loads(response.body) if isinstance(response, main.JSONResponse) else response
        self.assertTrue(reserve("a", "10:00", "11:00")["result"])
        self.assertTrue(reserve("b", "10:00", "11:00")["result"])
        overlap = reserve("a", "10:30", "11:30")
        self.assertFalse(overlap["result"])
        self.assertIn("すでに予約", overlap["message"])
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE reservation SET status='cancelled' WHERE userid='a'")
            db.commit()
        self.assertTrue(reserve("a", "10:30", "11:30")["result"])

    def test_reservation_slot_summary_sums_active_people_for_each_overlapping_slot(self):
        future = (datetime.datetime.now(main.JST).date() + datetime.timedelta(days=3)).isoformat()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executemany(
                "INSERT INTO reservation_available_slot (admin_id, day, start_time) VALUES ('system', ?, ?)",
                [(future, value) for value in ("10:00", "10:30", "11:00")]
            )
            db.executemany("""
                INSERT INTO reservation (userid, day, start_time, end_time, purpose, people_count, status)
                VALUES (?, ?, ?, ?, '研究', ?, ?)
            """, [
                ("a", future, "10:00", "11:00", 2, "active"),
                ("b", future, "10:30", "11:30", 5, "active"),
                ("c", future, "10:00", "11:00", 20, "cancelled"),
            ])
            db.commit()
            summary = {slot["start_time"]: slot for slot in main.reservation_slot_summary(db, future)}
        self.assertEqual(summary["10:00"]["reserved_people"], 2)
        self.assertEqual(summary["10:30"]["reserved_people"], 7)
        self.assertEqual(summary["11:00"]["reserved_people"], 5)
        self.assertEqual(summary["10:30"]["state"], "available")

    def test_workshop_reservation_rejects_invalid_people_count(self):
        session = {"user_login": True, "user_id": "a", "reservation_csrf_token": "token"}
        for invalid_count in ("", "0", "-1", "1.5", "abc"):
            response = self.await_result(main.ReservationDate(
                request("/reservation/date", session.copy(), "POST"),
                "2030-01-02", "10:00", "10:30", "研究", invalid_count, "token"
            ))
            self.assertEqual(response.status_code, 400, invalid_count)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation").fetchone()[0], 0)

    def test_reservation_schedule_requires_super_admin_for_page_api_and_update(self):
        ordinary = request("/admin/reservation-schedule", {"admin_login": True, "admin_id": "admin"})
        with self.assertRaises(main.HTTPException) as page_error:
            self.await_result(main.AdminReservationSchedule(ordinary))
        self.assertEqual(page_error.exception.status_code, 403)
        with self.assertRaises(main.HTTPException) as api_error:
            self.await_result(main.AdminReservationScheduleAvailability(ordinary, "2030-01-02"))
        self.assertEqual(api_error.exception.status_code, 403)
        post = request("/admin/reservation-schedule", {
            "admin_login": True, "admin_id": "admin",
            "admin_reservation_schedule_csrf_token": "token"
        }, "POST")
        from unittest.mock import patch as mock_patch
        with mock_patch.object(main, "valid_reservation_csrf", return_value=True):
            with self.assertRaises(main.HTTPException) as post_error:
                self.await_result(main.UpdateAdminReservationSchedule(
                    post, "2030-01-02", "add", "10:00", "10:30", "token"
                ))
        self.assertEqual(post_error.exception.status_code, 403)

    def test_super_admin_can_add_and_delete_workshop_schedule(self):
        from unittest.mock import patch as mock_patch
        future = (datetime.datetime.now(main.JST).date() + datetime.timedelta(days=3)).isoformat()
        admin_request = request("/admin/reservation-schedule", {
            "admin_login": True, "admin_id": "super", "admin_session_version": 0,
            "admin_reservation_schedule_csrf_token": "token"
        }, "POST")
        with mock_patch.object(main, "valid_reservation_csrf", return_value=True):
            added = self.await_result(main.UpdateAdminReservationSchedule(
                admin_request, future, "add", "10:00", "11:00", "token"
            ))
            self.assertEqual(added.status_code, 303)
            with closing(sqlite3.connect(self.db_path)) as db:
                self.assertEqual(db.execute(
                    "SELECT start_time FROM reservation_available_slot WHERE day=? ORDER BY start_time", (future,)
                ).fetchall(), [("10:00",), ("10:30",)])
            deleted = self.await_result(main.UpdateAdminReservationSchedule(
                admin_request, future, "delete", "10:00", "11:00", "token"
            ))
        self.assertEqual(deleted.status_code, 303)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM reservation_available_slot WHERE day=?", (future,)
            ).fetchone()[0], 0)

    def test_workshop_schedule_migration_merges_duplicate_slots_and_is_idempotent(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("CREATE TABLE schema_migration (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            db.executemany("INSERT INTO reservation_available_slot (admin_id, day, start_time, end_time, created_at, updated_at) VALUES (?, '2030-01-02', '10:00', '10:30', 'old', 'old')",
                           [("admin",), ("super",)])
            db.commit()
        with closing(sqlite3.connect(self.db_path)) as db:
            main.initialize_reservation_schedule(db)
            main.initialize_reservation_schedule(db)
            db.commit()
            self.assertEqual(db.execute("SELECT admin_id,day,start_time FROM reservation_available_slot").fetchall(),
                             [("super", "2030-01-02", "10:00")])

    def test_equipment_create_and_cancel_notifications(self):
        future = (datetime.datetime.now(main.JST).date() + datetime.timedelta(days=3)).isoformat()
        student = request("/equipment-reservation", {"user_login": True, "user_id": "a"}, "POST")
        takeout = {"id": "e1", "name": "顕微鏡", "usage_type": "takeout", "count": 2}
        with patch.object(main, "load_equipment_catalog", return_value=[takeout]), patch.object(
            main, "takeout_availability", return_value={"available": 2}
        ):
            self.await_result(main.CreateEquipmentReservation(student, "e1", "", future, future, 1, "研究", ""))
        with closing(sqlite3.connect(self.db_path)) as db:
            reservation_id = db.execute("SELECT id FROM equipment_reservation").fetchone()[0]
        self.await_result(main.CancelEquipmentReservation(student, reservation_id))

        room = {"id": "e2", "name": "レーザー", "usage_type": "in_room", "count": 1}
        slots = [{"start_time": "12:00", "end_time": "12:30", "closed": False, "available_quantity": 1}]
        with patch.object(main, "load_equipment_catalog", return_value=[room]), patch.object(
            main, "room_slot_availability", return_value=slots
        ):
            self.await_result(main.CreateEquipmentRoomReservation(student, "e2", future, "12:00", "12:30", 1, "研究", ""))
        with closing(sqlite3.connect(self.db_path)) as db:
            room_id = db.execute("SELECT id FROM equipment_room_reservation").fetchone()[0]
        self.await_result(main.CancelEquipmentRoomReservation(student, room_id))
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT notification_type, COUNT(*) FROM notification GROUP BY notification_type ORDER BY notification_type").fetchall(), [("reservation_cancelled", 2), ("reservation_created", 2)])

    def test_reminders_cover_all_types_and_do_not_duplicate(self):
        tomorrow = datetime.date(2030, 1, 2)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO reservation (userid, day, start_time, end_time, purpose) VALUES ('a', ?, '10:00', '11:00', 'x')", (tomorrow.isoformat(),))
            db.execute("INSERT INTO equipment_reservation (userid, equipment, start_day, end_day, quantity, purpose) VALUES ('b', '顕微鏡', ?, ?, 1, 'x')", (tomorrow.isoformat(), tomorrow.isoformat()))
            db.execute("INSERT INTO equipment_room_reservation (userid, equipment_id, equipment, use_day, start_time, end_time, quantity, purpose) VALUES ('c', 'e1', 'レーザー', ?, '12:00', '12:30', 1, 'x')", (tomorrow.isoformat(),))
            db.commit()
        self.assertEqual(create_reservation_reminders(self.db_path, tomorrow), 3)
        self.assertEqual(create_reservation_reminders(self.db_path, tomorrow), 0)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notification WHERE notification_type = 'reservation_reminder'").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
