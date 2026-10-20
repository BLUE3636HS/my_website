import asyncio
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
                INSERT INTO teacher VALUES ('teacher-1', 'secret', 'school-1');
                INSERT INTO student VALUES ('student-a', 'hash-a', 'school-1');
                INSERT INTO student VALUES ('student-b', 'hash-b', 'school-1');
                INSERT INTO student VALUES ('student-c', 'hash-c', 'school-2');
            """)
        p = patch.object(main, "DATABASE_PATH", self.db_path)
        p.start()
        self.addCleanup(p.stop)

    def render(self, session):
        return asyncio.run(main.TeacherAccountsPage(request(session)))

    def test_lists_only_logged_in_teachers_school_student_ids(self):
        response = self.render({"teacher_login": True, "teacher_id": "teacher-1"})
        html = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("student-a", html)
        self.assertIn("student-b", html)
        self.assertNotIn("student-c", html)
        self.assertNotIn("hash-a", html)
        self.assertNotIn("secret", html)

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


if __name__ == "__main__":
    unittest.main()
