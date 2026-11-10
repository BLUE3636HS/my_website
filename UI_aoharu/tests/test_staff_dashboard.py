import datetime
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from staff_dashboard import JST, admin_dashboard_summary, login_status, teacher_dashboard_summary


def make_db():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE student (id TEXT, pwd TEXT, school TEXT, name TEXT, last_access_at TEXT);
        CREATE TABLE study (id INTEGER PRIMARY KEY, name TEXT, introduce TEXT, filename TEXT, pdfpath TEXT,
            userid TEXT, time TEXT, submission_kind TEXT DEFAULT 'individual', chat_group_id TEXT, chat_group_name TEXT);
        CREATE TABLE study_submission_member (study_id INTEGER, student_id_snapshot TEXT, position INTEGER);
        CREATE TABLE mentor_profile (admin_id TEXT, display_name TEXT);
        CREATE TABLE mentor_reservation (id INTEGER PRIMARY KEY, student_id TEXT, mentor_admin_id TEXT, day TEXT,
            start_time TEXT, end_time TEXT, meeting_type TEXT, consultation TEXT, status TEXT);
        CREATE TABLE reservation (id INTEGER PRIMARY KEY, userid TEXT, day TEXT, start_time TEXT, end_time TEXT,
            purpose TEXT, status TEXT);
        CREATE TABLE equipment_reservation (id INTEGER PRIMARY KEY, userid TEXT, equipment TEXT, start_day TEXT,
            end_day TEXT, quantity INTEGER, purpose TEXT, returned INTEGER DEFAULT 0);
        CREATE TABLE equipment_room_reservation (id INTEGER PRIMARY KEY, userid TEXT, equipment TEXT, use_day TEXT,
            start_time TEXT, end_time TEXT, quantity INTEGER, purpose TEXT);
    """)
    return db


class TeacherDashboardSummaryTests(unittest.TestCase):
    def test_counts_inactive_students_and_lists_recent_studies_for_own_school_only(self):
        db = make_db()
        now = datetime.datetime(2026, 11, 9, 12, 0, 0)
        db.executemany("INSERT INTO student(id, school, name, last_access_at) VALUES (?, ?, ?, ?)", [
            ("never", "A", None, None),
            ("blank", "A", None, ""),
            ("stale", "A", None, "2026-09-01 10:00:00"),
            ("active", "A", None, "2026-11-08 10:00:00"),
            ("other", "B", None, None),
        ])
        for index in range(7):
            db.execute("INSERT INTO study(id, name, userid, time) VALUES (?, ?, ?, ?)",
                       (index + 1, f"study-{index + 1}", "active", f"2026-11-0{index + 1} 09:00:00"))
        db.execute("INSERT INTO study(id, name, userid, time) VALUES (99, 'other-school', 'other', '2026-11-09 09:00:00')")
        db.execute("UPDATE study SET submission_kind='group', chat_group_name='G1' WHERE id=7")
        db.executemany("INSERT INTO study_submission_member VALUES (7, ?, ?)", [("active", 0), ("stale", 1)])

        summary = teacher_dashboard_summary(db, "A", now)

        self.assertEqual(summary["student_count"], 4)
        self.assertEqual(summary["inactive_never"], 2)
        self.assertEqual(summary["inactive_stale"], 1)
        self.assertEqual(summary["inactive_total"], 3)
        titles = [study["title"] for study in summary["recent_studies"]]
        self.assertEqual(titles, ["study-7", "study-6", "study-5", "study-4", "study-3"])
        self.assertEqual(summary["recent_studies"][0]["group_name"], "G1")
        self.assertEqual(summary["recent_studies"][0]["submitter"], "active、stale")


class LoginStatusTests(unittest.TestCase):
    def test_classifies_never_stale_and_active_with_30_day_boundary(self):
        now = datetime.datetime(2026, 11, 9, 12, 0, 0)
        self.assertEqual(login_status(None, now)["state"], "never")
        self.assertEqual(login_status("", now)["state"], "never")
        stale = login_status("2026-10-09 11:59:59", now)
        self.assertEqual((stale["state"], stale["days"]), ("stale", 31))
        self.assertEqual(login_status("2026-10-10 12:00:00", now)["state"], "active")
        self.assertEqual(login_status("2026-11-08 10:00:00", now)["last_access_at"], "2026-11-08 10:00")
        self.assertEqual(login_status("broken", now)["state"], "active")


class AdminDashboardSummaryTests(unittest.TestCase):
    def test_counts_today_upcoming_week_and_overdue_takeouts_excluding_cancelled_and_returned(self):
        db = make_db()
        now = datetime.datetime(2026, 11, 9, 9, 0, tzinfo=JST)
        db.execute("INSERT INTO student(id, school) VALUES ('s1', 'A')")
        db.executemany("INSERT INTO reservation(userid, day, start_time, end_time, purpose, status) VALUES (?, ?, ?, ?, ?, ?)", [
            ("s1", "2026-11-09", "10:00", "11:00", "p", "active"),
            ("s1", "2026-11-09", "12:00", "13:00", "p", "cancelled"),
            ("s1", "2026-11-12", "10:00", "11:00", "p", "active"),
            ("s1", "2026-11-17", "10:00", "11:00", "p", "active"),  # 8日後は対象外
        ])
        db.executemany("INSERT INTO mentor_reservation(student_id, mentor_admin_id, day, start_time, end_time, meeting_type, consultation, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ("s1", "m1", "2026-11-09", "15:00", "16:00", "online", "c", "active"),
            ("s1", "m1", "2026-11-10", "15:00", "16:00", "online", "c", "active"),
        ])
        db.executemany("INSERT INTO equipment_reservation(userid, equipment, start_day, end_day, quantity, purpose, returned) VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("s1", "顕微鏡", "2026-11-05", "2026-11-12", 1, "p", 0),   # 今日にかかる
            ("s1", "ビーカー", "2026-11-01", "2026-11-05", 2, "p", 0),  # 未返却（4日超過）
            ("s1", "温度計", "2026-11-01", "2026-11-05", 1, "p", 1),    # 返却済み
            ("s1", "電源", "2026-11-08", "2026-11-10", 1, "p", 1),      # 返却済みは今日に数えない
        ])
        db.execute("INSERT INTO equipment_room_reservation(userid, equipment, use_day, start_time, end_time, quantity, purpose) VALUES ('s1', 'はんだごて', '2026-11-11', '10:00', '11:00', 1, 'p')")

        summary = admin_dashboard_summary(db, now)

        self.assertEqual(summary["today_counts"], {"room": 1, "mentor": 1, "equipment": 1})
        self.assertEqual(summary["today_total"], 3)
        self.assertEqual(summary["upcoming_counts"], {"room": 1, "mentor": 1, "equipment": 1})
        self.assertEqual(summary["upcoming_total"], 3)
        self.assertEqual(summary["overdue_total"], 1)
        self.assertEqual(summary["overdue"][0]["equipment"], "ビーカー")
        self.assertEqual(summary["overdue"][0]["days_late"], 4)
        self.assertEqual([item["time"] for item in summary["today_list"]], ["10:00〜11:00", "15:00〜16:00", "持ち出し期間中"])


if __name__ == "__main__":
    unittest.main()
