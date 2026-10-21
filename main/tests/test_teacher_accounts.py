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
                CREATE TABLE student (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL, name TEXT, furigana TEXT);
                CREATE TABLE teacher (id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'teacher');
                CREATE TABLE study (id INTEGER PRIMARY KEY, name TEXT NOT NULL, time TEXT NOT NULL, submission_kind TEXT NOT NULL, chat_group_name TEXT, userid TEXT NOT NULL);
                CREATE TABLE study_submission_member (study_id INTEGER NOT NULL, student_id_snapshot TEXT NOT NULL, position INTEGER NOT NULL);
                INSERT INTO teacher (id,pwd,school) VALUES ('teacher-1', 'secret', 'school-1');
                INSERT INTO student VALUES ('student-a', 'hash-a', 'school-1', '山田太郎', 'ヤマダタロウ');
                INSERT INTO student VALUES ('student-b', 'hash-b', 'school-1', NULL, NULL);
                INSERT INTO student VALUES ('student-c', 'hash-c', 'school-2', '佐藤花子', 'サトウハナコ');
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
        self.assertIn("山田太郎", html)
        self.assertIn("ヤマダタロウ", html)
        self.assertIn("未登録", html)
        self.assertNotIn("student-c", html)
        self.assertNotIn("hash-a", html)
        self.assertNotIn("secret", html)
        self.assertIn('class="teacher-number-sort" data-column-index="3"', html)
        self.assertIn(">Name</th>", html)
        self.assertNotIn('class="teacher-number-sort" data-column-index="4"', html)
        self.assertIn('class="teacher-number-sort" data-column-index="5"', html)

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

    def test_super_teacher_can_manage_only_own_school_feature_settings(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE teacher SET role='super_teacher' WHERE id='teacher-1'")
            db.execute("CREATE TABLE registered_school(name TEXT NOT NULL UNIQUE)")
            db.execute("INSERT INTO registered_school VALUES ('school-1'), ('school-2')")
            db.commit()
        from school_features import initialize_school_feature_settings, seed_school_feature_settings
        seed_school_feature_settings(self.db_path)
        session = {"teacher_login": True, "teacher_id": "teacher-1"}
        get_request = request(session)
        get_request.scope["path"] = "/teacher/school-features"
        with patch.object(main, "DATABASE_PATH", self.db_path):
            response = run_coroutine(main.TeacherSchoolFeaturesPage(get_request))
        self.assertEqual(response.status_code, 200)
        self.assertIn("school-1", response.body.decode())
        self.assertNotIn("school-2", response.body.decode())

        token = session["teacher_school_features_csrf"]
        body = f"csrf_token={token}&enabled%3Adashboard=1&enabled%3Acommunity=1&enabled%3Astudies_add=1&enabled%3Astudies_list=1&enabled%3Aworkshop_reservation=1&enabled%3Amentor_reservation=1&enabled%3Aequipment_reservation=1"
        post_scope = request(session).scope
        post_scope.update({"method": "POST", "path": "/teacher/school-features",
                           "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                           "query_string": b""})
        post_request = Request(post_scope)
        async def receive():
            return {"type": "http.request", "body": body.encode(), "more_body": False}
        post_request._receive = receive
        from starlette.datastructures import FormData
        async def parsed_form():
            return FormData({
                "csrf_token": token,
                "enabled:dashboard": "1",
                "enabled:community": "1",
                "enabled:studies_add": "1",
                "enabled:studies_list": "1",
                "enabled:workshop_reservation": "1",
                "enabled:mentor_reservation": "1",
                "enabled:equipment_reservation": "1",
            })
        post_request.form = parsed_form
        with patch.object(main, "DATABASE_PATH", self.db_path):
            response = run_coroutine(main.TeacherUpdateSchoolFeatures(post_request, token))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.db_path)) as db:
            flags = db.execute("SELECT school, enabled FROM school_feature_setting WHERE feature_key='community' ORDER BY school").fetchall()
            self.assertEqual(flags, [("school-1", 1), ("school-2", 1)])
            dashboard = db.execute("SELECT enabled FROM school_feature_setting WHERE school='school-1' AND feature_key='dashboard'").fetchone()[0]
            self.assertEqual(dashboard, 1)
            from school_features import enabled_for_student
            self.assertTrue(enabled_for_student(self.db_path, "student-a", "dashboard"))

    def test_regular_teacher_cannot_open_school_feature_settings(self):
        get_request = request({"teacher_login": True, "teacher_id": "teacher-1"})
        get_request.scope["path"] = "/teacher/school-features"
        with patch.object(main, "DATABASE_PATH", self.db_path):
            with self.assertRaises(main.HTTPException) as error:
                run_coroutine(main.TeacherSchoolFeaturesPage(get_request))
        self.assertEqual(error.exception.status_code, 403)


def run_coroutine(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("coroutine unexpectedly awaited an asynchronous operation")


if __name__ == "__main__":
    unittest.main()
