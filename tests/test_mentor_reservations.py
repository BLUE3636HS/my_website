import datetime
import sqlite3
import unittest

from mentor_reservations import (
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


if __name__ == "__main__":
    unittest.main()
