import ast
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import bcrypt
import school_registration
from school_registration import (
    initialize_school_registration,
    issue_school_id,
    register_school_student,
    verify_school_id,
)


MAIN_DIR = Path(__file__).resolve().parents[1]


class SchoolRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "main-test.db"
        self.pepper_patch = patch.object(
            school_registration,
            "SCHOOL_ID_PEPPER_PATH",
            Path(self.temp.name) / ".school_id_pepper",
        )
        self.pepper_patch.start()
        with closing(sqlite3.connect(self.db_path)) as db:
            with db:
                db.execute("CREATE TABLE student (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL)")
                db.execute("CREATE TABLE admin (id TEXT PRIMARY KEY, pwd TEXT NOT NULL)")
                db.execute("INSERT INTO admin VALUES ('admin', 'keep-this-hash')")
                initialize_school_registration(db)

    def tearDown(self):
        self.pepper_patch.stop()
        self.temp.cleanup()

    def issue(self, name="Example School", reissue=False):
        with closing(sqlite3.connect(self.db_path)) as db:
            with db:
                value = issue_school_id(db, name, reissue=reissue)
        return value

    def test_school_id_hash_shared_registration_and_duplicate_id(self):
        school_id = self.issue()
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(verify_school_id(db, school_id), "Example School")
            self.assertRegex(school_id, r"^[A-HJ-NP-Z2-9]{6}$")
            stored = db.execute("SELECT school_id_lookup_hash, name, school_id_hash FROM registered_school").fetchone()
            self.assertNotIn(school_id, "".join(stored))
            self.assertNotIn(school_id, stored[2])

        self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", school_id, "192.0.2.1"), "created")
        self.assertEqual(register_school_student(self.db_path, "student_0002", "password2", school_id, "192.0.2.2"), "created")
        self.assertEqual(register_school_student(self.db_path, "student_0001", "password3", school_id, "192.0.2.3"), "duplicate")
        with closing(sqlite3.connect(self.db_path)) as db:
            rows = db.execute("SELECT id, pwd, school FROM student ORDER BY id").fetchall()
            self.assertEqual([(row[0], row[2]) for row in rows], [
                ("student_0001", "Example School"), ("student_0002", "Example School")
            ])
            self.assertTrue(bcrypt.checkpw(b"password1", rows[0][1].encode()))
            self.assertEqual(db.execute("SELECT id, pwd FROM admin").fetchall(), [("admin", "keep-this-hash")])

    def test_invalid_id_reissue_revokes_old_id_and_keeps_students(self):
        old_id = self.issue()
        self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", old_id, "192.0.2.1"), "created")
        new_id = self.issue(reissue=True)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertIsNone(verify_school_id(db, old_id))
            self.assertEqual(verify_school_id(db, new_id), "Example School")
        self.assertEqual(register_school_student(self.db_path, "student_0002", "password2", old_id, "192.0.2.2"), "invalid")
        self.assertEqual(register_school_student(self.db_path, "student_0002", "password2", new_id, "192.0.2.2"), "created")
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student").fetchone()[0], 2)

    def test_bad_school_id_validation_and_rate_limit(self):
        school_id = self.issue()
        self.assertEqual(register_school_student(self.db_path, "short", "password1", school_id, "192.0.2.1"), "invalid")
        for _ in range(10):
            self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", "wrong", "192.0.2.1"), "invalid")
        self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", school_id, "192.0.2.1"), "rate_limited")

    def test_additive_upgrade_of_previous_school_table(self):
        legacy_path = Path(self.temp.name) / "legacy-main.db"
        legacy_id = "oldselector.old-secret-value"
        with closing(sqlite3.connect(legacy_path)) as db:
            db.execute("""
                CREATE TABLE registered_school (
                    selector TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL UNIQUE,
                    school_id_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute(
                "INSERT INTO registered_school VALUES (?, ?, ?, ?, ?)",
                ("oldselector", "Old School", bcrypt.hashpw(b"old-secret-value", bcrypt.gensalt()).decode(), "created", "updated"),
            )
            initialize_school_registration(db)
            self.assertIsNone(verify_school_id(db, legacy_id))
            db.execute("BEGIN IMMEDIATE")
            replacement = issue_school_id(db, "Old School", reissue=True)
            db.commit()
            self.assertRegex(replacement, r"^[A-HJ-NP-Z2-9]{6}$")
            self.assertIsNone(verify_school_id(db, legacy_id))
            self.assertEqual(verify_school_id(db, replacement), "Old School")

    def test_general_registration_post_is_explicitly_unimplemented(self):
        source = (MAIN_DIR / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        route = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "Registration"
            and any(isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "post" for d in node.decorator_list)
        )

        class FakeApp:
            def post(self, *_args, **_kwargs):
                return lambda fn: fn

        namespace = {"app": FakeApp(), "Form": lambda default=...: default, "Request": object}
        exec(compile(ast.Module(body=[route], type_ignores=[]), "main.py", "exec"), namespace)
        response = asyncio.run(namespace["Registration"](object(), "student"))
        self.assertEqual(response, {"result": 3, "message": "未実装の機能です"})
        page = (MAIN_DIR / "templates" / "registration.html").read_text(encoding="utf-8")
        self.assertIn('type="email" id="student_email"', page)
        self.assertIn('href="/school-registration"', page)
        self.assertNotIn('id="student_school"', page)


if __name__ == "__main__":
    unittest.main()
