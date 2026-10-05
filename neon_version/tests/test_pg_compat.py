import unittest
from unittest.mock import patch

import pg_compat
from mypage_calendar import admin_calendar_reservations


class FakeCursor:
    closed = False

    def __init__(self):
        self.calls = []
        self.rowcount = 1
        self.description = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self.rowcount = 0 if "ON CONFLICT DO NOTHING" in sql else 1
        return self

    def fetchone(self):
        return (41,)

    def fetchall(self):
        return []


class FakeConnection:
    closed = False

    def __init__(self):
        self.cursor_instance = FakeCursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class PostgreSQLAdapterTests(unittest.TestCase):
    def setUp(self):
        self.raw = FakeConnection()
        self.connect_patch = patch.object(pg_compat.psycopg, "connect", return_value=self.raw)
        self.connect_patch.start()
        self.addCleanup(self.connect_patch.stop)
        self.env_patch = patch.dict("os.environ", {"DATABASE_URL": "postgresql://example.invalid/db"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_insert_returns_id_and_converts_parameters(self):
        db = pg_compat.connect()
        result = db.execute("INSERT INTO reservation(day, purpose) VALUES (?, ?)", ("2026-10-06", "test"))
        self.assertEqual(result.lastrowid, 41)
        self.assertEqual(self.raw.cursor_instance.calls[-1],
                         ("INSERT INTO reservation(day, purpose) VALUES (%s, %s) RETURNING id",
                          ("2026-10-06", "test")))

    def test_immediate_transaction_uses_database_lock(self):
        db = pg_compat.connect()
        db.execute("BEGIN IMMEDIATE")
        self.assertEqual(self.raw.cursor_instance.calls[-1][0],
                         "SELECT pg_advisory_xact_lock(72493612)")

    def test_ignore_and_schema_introspection_translation(self):
        db = pg_compat.connect()
        db.execute("INSERT OR IGNORE INTO study_template(seed_key, name) VALUES (?, ?)", ("x", "y"))
        sql = self.raw.cursor_instance.calls[-1][0]
        self.assertIn("ON CONFLICT DO NOTHING RETURNING id", sql)
        db.execute("PRAGMA table_info(student)")
        self.assertIn("information_schema.columns", self.raw.cursor_instance.calls[-1][0])

    def test_quoted_question_marks_stay_literals(self):
        self.assertEqual(pg_compat._translate("SELECT '?' AS marker WHERE id = ?"),
                         "SELECT '?' AS marker WHERE id = %s")

    def test_row_supports_index_and_name_access(self):
        row = pg_compat.Row((7, "A"), ("id", "name"))
        self.assertEqual((row[0], row["name"], dict(row)),
                         (7, "A", {"id": 7, "name": "A"}))

    def test_cursor_exposes_description(self):
        db = pg_compat.connect()
        cursor = db.execute("SELECT id FROM student")
        self.assertEqual(cursor.description, self.raw.cursor_instance.description)

    def test_admin_calendar_reads_column_metadata(self):
        db = pg_compat.connect()
        self.assertEqual(admin_calendar_reservations(db), [])


if __name__ == "__main__":
    unittest.main()
