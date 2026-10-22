import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from school_features import (
    FEATURES,
    enabled_for_student,
    feature_for_student_path,
    school_feature_flags,
    seed_school_feature_settings,
)


class SchoolFeatureSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "test.db"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE registered_school(name TEXT NOT NULL UNIQUE)")
            db.execute("CREATE TABLE student(id TEXT NOT NULL, school TEXT NOT NULL)")
            db.execute("INSERT INTO registered_school VALUES('学校A')")
            db.execute("INSERT INTO registered_school VALUES('学校B')")
            db.execute("INSERT INTO registered_school VALUES('生徒なしの学校')")
            db.execute("INSERT INTO student VALUES('student-a', '学校A')")
            db.execute("INSERT INTO student VALUES('student-b', '学校B')")
            db.execute("INSERT INTO student VALUES('student-c', '学校A')")
            db.commit()

    def test_distinct_database_schools_are_seeded_enabled_by_default(self):
        seed_school_feature_settings(self.database)
        with closing(sqlite3.connect(self.database)) as db:
            for school in ("学校A", "学校B", "生徒なしの学校"):
                self.assertEqual(school_feature_flags(db, school),
                                 {key: True for key, _ in FEATURES})
            rows = db.execute(
                "SELECT COUNT(*) FROM school_feature_setting WHERE school=?",
                ("生徒なしの学校",),
            ).fetchone()[0]
            self.assertEqual(rows, len(FEATURES))

    def test_off_setting_survives_reseeding(self):
        seed_school_feature_settings(self.database)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE school_feature_setting SET enabled=0 WHERE school=? AND feature_key=?",
                       ("学校A", "community"))
            db.commit()

        seed_school_feature_settings(self.database)
        with closing(sqlite3.connect(self.database)) as db:
            flags = school_feature_flags(db, "学校A")
            self.assertFalse(flags["community"])
            self.assertTrue(flags["dashboard"])
            self.assertTrue(school_feature_flags(db, "学校B")["community"])

    def test_student_page_and_api_paths_share_feature_control(self):
        paths = {
            "/dashboard": "dashboard",
            "/addform": "studies_add",
            "/studylist": "studies_list",
            "/community/posts": "community",
            "/reservation/availability": "workshop_reservation",
            "/reservation/date": "workshop_reservation",
            "/mentor-reservation/mentor-a/availability": "mentor_reservation",
            "/mentor-reservation/mentor-a": "mentor_reservation",
            "/equipment-reservation": "equipment_reservation",
            "/equipment-availability": "equipment_reservation",
            "/equipment-room-reservation": "equipment_reservation",
        }
        for path, feature in paths.items():
            with self.subTest(path=path):
                self.assertEqual(feature_for_student_path(path), feature)
        self.assertIsNone(feature_for_student_path("/mypage"))

    def test_student_access_uses_current_school_setting(self):
        seed_school_feature_settings(self.database)
        self.assertTrue(enabled_for_student(self.database, "student-a", "community"))
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE school_feature_setting SET enabled=0 WHERE school=? AND feature_key=?",
                       ("学校A", "community"))
            db.commit()
        self.assertFalse(enabled_for_student(self.database, "student-a", "community"))
        self.assertTrue(enabled_for_student(self.database, "student-a", "dashboard"))
        self.assertFalse(enabled_for_student(self.database, "missing", "dashboard"))


if __name__ == "__main__":
    unittest.main()
