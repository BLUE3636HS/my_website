import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import main
from starlette.requests import Request


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Account page unexpectedly yielded")


class AdminAccountsPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "accounts.db"
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""CREATE TABLE registered_school(name TEXT NOT NULL);
                CREATE TABLE student(id TEXT, school TEXT, name TEXT, furigana TEXT);
                CREATE TABLE teacher(id TEXT, school TEXT, role TEXT);
                INSERT INTO registered_school VALUES('Registered School');
                INSERT INTO student VALUES('student-1','Legacy School','Student','');
                INSERT INTO teacher VALUES('teacher-1','Teacher School','teacher');""")
            db.commit()
        self.database_patch = patch.object(main, "DATABASE_PATH", self.path)
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)

    def test_school_filter_values_come_from_database_and_accounts_page_renders(self):
        self.assertEqual(main.load_schools(), ["Legacy School", "Registered School", "Teacher School"])
        scope = {"type": "http", "method": "GET", "scheme": "http", "path": "/admin/accounts",
                 "server": ("test", 80), "headers": [], "query_string": b"", "session": {"admin_login": True,
                 "admin_id": "admin-1"}}

        async def receive():
            return {"type": "http.disconnect"}

        response = run_immediate(main.AdminAccountsPage(Request(scope, receive=receive)))
        self.assertEqual(response.status_code, 200)
        html = response.body.decode("utf-8")
        self.assertIn("アカウント一覧", html)
        self.assertIn("Legacy School", html)
        self.assertIn("Registered School", html)
        self.assertIn("Student", html)


if __name__ == "__main__":
    unittest.main()
