import json
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import main
from student_custom_fields import initialize_custom_fields, validate_definition, validate_value
from test_teacher_accounts import request


class CustomFieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "fields.db"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript("""
                CREATE TABLE student(id TEXT, pwd TEXT, school TEXT, profile_image TEXT, name TEXT, furigana TEXT);
                CREATE TABLE teacher(id TEXT, pwd TEXT, school TEXT);
                INSERT INTO teacher VALUES('t1','hash','school-a'),('t2','hash','school-b');
                INSERT INTO student VALUES('s1','secret-hash','school-a',NULL,'佐藤花子','サトウハナコ'),('s2','hash','school-b',NULL,NULL,NULL);
            """)
            initialize_custom_fields(db)
        patcher = patch.object(main, "DATABASE_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_schema_is_idempotent_and_validation_rejects_invalid_options(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            initialize_custom_fields(db)
            definition = validate_definition("選択", "select", "1", "A\nB")
            field = {"field_type": definition[1], "required": definition[2], "options": definition[3]}
            self.assertEqual(validate_value(field, "A"), "A")
            with self.assertRaises(ValueError): validate_value(field, "C")
            with self.assertRaises(ValueError): validate_definition("選択", "select", "0", "A\nA")

    def test_teacher_listing_school_isolation_and_soft_delete_recreate(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO student_custom_field(school,label,field_type,required,options_json,created_at) VALUES('school-a','趣味','text',0,'[]','now')")
            field_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO student_custom_field_value VALUES('s1',?,?, 'now')", (field_id, json.dumps("読書")))
            db.commit()
        response = asyncio.run(main.TeacherAccountsPage(request({"teacher_login": True, "teacher_id": "t1"})))
        html = response.body.decode()
        self.assertIn("読書", html)
        self.assertNotIn("secret-hash", html)
        self.assertNotIn("s2", html)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE student_custom_field SET deleted_at='now' WHERE id=?", (field_id,))
            db.execute("INSERT INTO student_custom_field(school,label,field_type,required,options_json,created_at) VALUES('school-a','趣味','text',0,'[]','later')")
            initialize_custom_fields(db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_custom_field_value WHERE field_id=?", (field_id,)).fetchone()[0], 0)
            self.assertNotEqual(db.execute("SELECT id FROM student_custom_field WHERE label='趣味' AND deleted_at IS NULL").fetchone()[0], field_id)

    def test_teacher_field_delete_removes_saved_student_values(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO student_custom_field(school,label,field_type,required,options_json,created_at) VALUES('school-a','趣味','text',0,'[]','now')")
            field_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO student_custom_field_value VALUES('s1',?,?, 'now')", (field_id, json.dumps("読書")))
            db.commit()

        session = {"teacher_login": True, "teacher_id": "t1", "teacher_custom_fields_csrf": "token"}
        response = asyncio.run(main.DeleteStudentCustomField(
            request(session), field_id=field_id, csrf_token="token"
        ))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertIsNotNone(db.execute("SELECT deleted_at FROM student_custom_field WHERE id=?", (field_id,)).fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_custom_field_value WHERE field_id=?", (field_id,)).fetchone()[0], 0)


if __name__ == "__main__": unittest.main()
