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
    register_school_teacher,
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
                db.execute("CREATE TABLE teacher (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL)")
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

        self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", school_id, "192.0.2.1", "山田太郎", "ヤマダタロウ"), "created")
        self.assertEqual(register_school_student(self.db_path, "student_0002", "password2", school_id, "192.0.2.2", "佐藤花子", "サトウハナコ"), "created")
        self.assertEqual(register_school_student(self.db_path, "student_0001", "password3", school_id, "192.0.2.3", "山田太郎", "ヤマダタロウ"), "duplicate")
        with closing(sqlite3.connect(self.db_path)) as db:
            rows = db.execute("SELECT id, pwd, school FROM student ORDER BY id").fetchall()
            self.assertEqual([(row[0], row[2]) for row in rows], [
                ("student_0001", "Example School"), ("student_0002", "Example School")
            ])
            self.assertTrue(bcrypt.checkpw(b"password1", rows[0][1].encode()))
            self.assertEqual(db.execute("SELECT name, furigana FROM student WHERE id='student_0001'").fetchone(), ("山田太郎", "ヤマダタロウ"))
            self.assertEqual(db.execute("SELECT id, pwd FROM admin").fetchall(), [("admin", "keep-this-hash")])

    def test_invalid_id_reissue_revokes_old_id_and_keeps_students(self):
        old_id = self.issue()
        self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", old_id, "192.0.2.1", "山田太郎", "ヤマダタロウ"), "created")
        new_id = self.issue(reissue=True)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertIsNone(verify_school_id(db, old_id))
            self.assertEqual(verify_school_id(db, new_id), "Example School")
        self.assertEqual(register_school_student(self.db_path, "student_0002", "password2", old_id, "192.0.2.2", "佐藤花子", "サトウハナコ"), "invalid")
        self.assertEqual(register_school_student(self.db_path, "student_0002", "password2", new_id, "192.0.2.2", "佐藤花子", "サトウハナコ"), "created")
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student").fetchone()[0], 2)

    def test_bad_school_id_validation_and_rate_limit(self):
        school_id = self.issue()
        self.assertEqual(register_school_student(self.db_path, "short", "password1", school_id, "192.0.2.1", "山田太郎", "ヤマダタロウ"), "invalid")
        for _ in range(10):
            self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", "wrong", "192.0.2.1", "山田太郎", "ヤマダタロウ"), "invalid")
        self.assertEqual(register_school_student(self.db_path, "student_0001", "password1", school_id, "192.0.2.1", "山田太郎", "ヤマダタロウ"), "rate_limited")

    def test_school_teacher_registration_requires_valid_code_and_uses_school_name(self):
        school_id = self.issue()
        self.assertEqual(register_school_teacher(self.db_path, "teacher_0001", "password1", "wrong", "192.0.2.1"), "invalid")
        self.assertEqual(register_school_teacher(self.db_path, "teacher_0001", "password1", school_id, "192.0.2.2"), "created")
        with closing(sqlite3.connect(self.db_path)) as db:
            row = db.execute("SELECT id, pwd, school FROM teacher").fetchone()
            self.assertEqual((row[0], row[2]), ("teacher_0001", "Example School"))
            self.assertTrue(bcrypt.checkpw(b"password1", row[1].encode()))
            self.assertEqual(db.execute("SELECT role FROM teacher").fetchone()[0], "teacher")
        self.assertEqual(register_school_student(self.db_path, "teacher_0001", "password2", school_id, "192.0.2.3", "山田太郎", "ヤマダタロウ"), "duplicate")

    def test_student_name_validation_and_existing_account_migration(self):
        school_id = self.issue()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO student (id,pwd,school) VALUES ('legacy_student','hash','Example School')")
            db.commit()
        for name, furigana in (
            ("", "ヤマダタロウ"),
            ("山田太郎", ""),
            ("山田 太郎", "ヤマダタロウ"),
            ("山田　太郎", "ヤマダタロウ"),
            ("山田太郎", "ヤマダ タロウ"),
            ("山田太郎", "ヤマダ　タロウ"),
            ("John", "ジョン"),
            ("山田太郎", "YAMADA"),
            ("山" * 81, "ヤマダ"),
            ("山田太郎", "ア" * 81),
        ):
            self.assertEqual(register_school_student(
                self.db_path, "student_0001", "password1", school_id, "192.0.2.1", name, furigana
            ), "invalid")
        self.assertEqual(register_school_student(
            self.db_path, "student_0001", "password1", school_id, "192.0.2.1", "山田太郎", "ヤマダタロウ"
        ), "created")
        self.assertEqual(register_school_student(
            self.db_path, "student_0002", "password2", school_id, "192.0.2.2", "山" * 80, "ア" * 80
        ), "created")
        with closing(sqlite3.connect(self.db_path)) as db:
            from school_registration import initialize_student_account_fields
            initialize_student_account_fields(db)
            self.assertEqual(db.execute("SELECT name,furigana FROM student WHERE id='legacy_student'").fetchone(), (None, None))

    def test_legacy_teacher_accounts_receive_teacher_rank(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO teacher (id, pwd, school) VALUES ('legacy_teacher', 'hash', 'Example School')")
            school_id = issue_school_id(db, "Example School")
            db.commit()
        self.assertEqual(register_school_teacher(self.db_path, "teacher_0001", "password1", school_id, "192.0.2.1"), "created")
        with closing(sqlite3.connect(self.db_path)) as db:
            roles = db.execute("SELECT role FROM teacher ORDER BY id").fetchall()
            self.assertEqual(roles, [("teacher",), ("teacher",)])

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
        self.assertIn('id="school_registration_form"', page)
        self.assertIn('id="school_student_name"', page)
        self.assertIn('id="school_student_furigana"', page)
        self.assertIn('id="school_id"', page)
        self.assertIn('src="/static/school_registration.js"', page)
        self.assertNotIn('type="email"', page)
        self.assertNotIn('href="/school-registration"', page)
        self.assertIn('id="teacher_registration_form"', page)
        self.assertIn('id="teacher_school_id"', page)
        self.assertNotIn('id="teacher_school"', page)
        self.assertNotIn('id="student_school"', page)


if __name__ == "__main__":
    unittest.main()
