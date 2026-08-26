import asyncio
import datetime
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
CREATE TABLE reservation (id INTEGER PRIMARY KEY AUTOINCREMENT, userid TEXT NOT NULL, day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, purpose TEXT NOT NULL);
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
            self.assertEqual(db.execute("SELECT recipient_user_id, COUNT(*) FROM notification GROUP BY recipient_user_id ORDER BY recipient_user_id").fetchall(), [("a", 3), ("b", 2), ("c", 1)])

    def test_admin_notification_page_requires_admin_session(self):
        middleware = main.LoginCheckMiddleware(main.app)
        response = self.await_result(middleware.dispatch(
            request("/admin/notifications", {}), lambda _: None
        ))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/login")

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

    def test_tekne_create_and_cancel_notifications(self):
        future = (datetime.datetime.now(main.JST).date() + datetime.timedelta(days=3)).isoformat()
        student = request("/reservation/date", {"user_login": True, "user_id": "a"}, "POST")
        response = self.await_result(main.ReservationDate(student, future, "10:00", "11:00", "研究"))
        self.assertTrue(response["result"])
        with closing(sqlite3.connect(self.db_path)) as db:
            created = db.execute("SELECT notification_type FROM notification WHERE recipient_user_id = 'a'").fetchall()
        self.assertEqual(created, [("reservation_created",)])
        self.await_result(main.DelReservation(student, future, "10:00"))
        with closing(sqlite3.connect(self.db_path)) as db:
            types = db.execute("SELECT notification_type FROM notification WHERE recipient_user_id = 'a' ORDER BY id").fetchall()
            self.assertEqual(types, [("reservation_created",), ("reservation_cancelled",)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation").fetchone()[0], 0)

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
