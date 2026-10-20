import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

import main


def request(session):
    return Request({
        "type": "http", "method": "GET", "path": "/teacher/accounts", "root_path": "",
        "scheme": "http", "server": ("test", 80), "client": ("test", 1),
        "headers": [], "query_string": b"", "session": session,
    })


class TeacherAccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "test.db"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript("""
                CREATE TABLE student (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL);
                CREATE TABLE teacher (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL);
                CREATE TABLE study (id INTEGER PRIMARY KEY, name TEXT NOT NULL, time TEXT NOT NULL, submission_kind TEXT NOT NULL, chat_group_name TEXT, userid TEXT NOT NULL);
                CREATE TABLE study_submission_member (study_id INTEGER NOT NULL, student_id_snapshot TEXT NOT NULL, position INTEGER NOT NULL);
                INSERT INTO teacher VALUES ('teacher-1', 'secret', 'school-1');
                INSERT INTO student VALUES ('student-a', 'hash-a', 'school-1');
                INSERT INTO student VALUES ('student-b', 'hash-b', 'school-1');
                INSERT INTO student VALUES ('student-c', 'hash-c', 'school-2');
            """)
            main.initialize_custom_fields(db)
            db.execute("INSERT INTO student_custom_field(school,label,field_type,created_at) VALUES ('school-1','Grade','number','2026-10-20')")
            db.execute("INSERT INTO student_custom_field(school,label,field_type,created_at) VALUES ('school-1','Name','text','2026-10-20')")
            db.execute("INSERT INTO student_custom_field(school,label,field_type,options_json,created_at) VALUES ('school-1','Class','select','[\"1\",\"2\"]','2026-10-20')")
            db.commit()
        p = patch.object(main, "DATABASE_PATH", self.db_path)
        p.start()
        self.addCleanup(p.stop)

    def render(self, session):
        coroutine = main.TeacherAccountsPage(request(session))
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        self.fail("TeacherAccountsPage unexpectedly awaited an async operation")

    def test_lists_only_logged_in_teachers_school_student_ids(self):
        response = self.render({"teacher_login": True, "teacher_id": "teacher-1"})
        html = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("student-a", html)
        self.assertIn("student-b", html)
        self.assertNotIn("student-c", html)
        self.assertNotIn("hash-a", html)
        self.assertNotIn("secret", html)
        self.assertIn('class="teacher-number-sort" data-column-index="1"', html)
        self.assertIn(">Name</th>", html)
        self.assertNotIn('class="teacher-number-sort" data-column-index="2"', html)
        self.assertIn('class="teacher-number-sort" data-column-index="3"', html)

    def test_missing_teacher_account_redirects_to_login(self):
        response = self.render({"teacher_login": True, "teacher_id": "deleted-teacher"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_empty_school_shows_empty_state(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE teacher SET school='school-empty' WHERE id='teacher-1'")
            db.commit()
        response = self.render({"teacher_login": True, "teacher_id": "teacher-1"})
        self.assertIn("登録中の生徒はいません。", response.body.decode())

    def test_submission_dialog_lists_individual_and_snapshot_group_submissions_newest_first(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript("""
                INSERT INTO study VALUES (1, 'Individual report', '2026-10-01 10:00:00', 'individual', NULL, 'student-a');
                INSERT INTO study VALUES (2, 'Group report', '2026-10-02 10:00:00', 'group', 'Group Alpha', 'student-b');
                INSERT INTO study VALUES (3, 'Other school report', '2026-10-03 10:00:00', 'individual', NULL, 'student-c');
                INSERT INTO study_submission_member VALUES (2, 'student-a', 0);
                INSERT INTO study_submission_member VALUES (2, 'student-b', 1);
                INSERT INTO study_submission_member VALUES (3, 'student-c', 0);
            """)
            db.commit()
        html = self.render({"teacher_login": True, "teacher_id": "teacher-1"}).body.decode()
        self.assertIn("提出物(2)", html)
        self.assertIn("Individual report", html)
        self.assertIn("Group report", html)
        self.assertIn("Group Alpha", html)
        self.assertNotIn("Other school report", html)
        self.assertLess(html.index("Group report"), html.index("Individual report"))


if __name__ == "__main__":
    unittest.main()
