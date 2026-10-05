"""Read the Neon schema and exercise reversible database operations.

This command deliberately rolls back all test records.
"""
from contextlib import closing
import getpass
import os
from uuid import uuid4

import pg_compat as dbapi


def main():
    if not os.environ.get("DATABASE_URL"):
        database_url = getpass.getpass("Neon DATABASE_URL: ").strip()
        if not database_url:
            print("DATABASE_URL が必要です。Neon の接続文字列を入力してください。")
            return 1
        os.environ["DATABASE_URL"] = database_url

    with closing(dbapi.connect()) as db:
        tables = {row[0] for row in db.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = current_schema()"
        )}
        required = {"student", "teacher", "admin", "reservation", "mentor_reservation",
                    "equipment_reservation", "study", "notification"}
        missing = required - tables
        if missing:
            raise RuntimeError(f"Missing Neon tables: {', '.join(sorted(missing))}")

        account_id = f"codex-check-{uuid4().hex}"
        try:
            db.execute("INSERT INTO student(id, pwd, school) VALUES (?, ?, ?)",
                       (account_id, "temporary", "test"))
            row = db.execute("SELECT pwd, rowid FROM student WHERE id = ?", (account_id,)).fetchone()
            assert row is not None and row[0] == "temporary" and isinstance(row[1], int), "student INSERT/SELECT failed"
            db.execute("UPDATE student SET pwd = ? WHERE id = ?", ("updated", account_id))
            row = db.execute("SELECT pwd FROM student WHERE id = ?", (account_id,)).fetchone()
            assert row == ("updated",), "student UPDATE failed"
            db.execute("DELETE FROM student WHERE id = ?", (account_id,))
            assert db.execute("SELECT 1 FROM student WHERE id = ?", (account_id,)).fetchone() is None

            # The same lock is acquired before reservation conflict rechecks.
            db.execute("BEGIN IMMEDIATE")
            assert db.execute("SELECT 1").fetchone() == (1,)
            with closing(dbapi.connect()) as competing_db:
                competing_db.execute("SET LOCAL lock_timeout = '250ms'")
                try:
                    competing_db.execute("BEGIN IMMEDIATE")
                except dbapi.Error:
                    competing_db.rollback()
                else:
                    competing_db.rollback()
                    raise AssertionError("concurrent reservation lock was not exclusive")
        finally:
            db.rollback()

    print("Neon schema, INSERT, SELECT, UPDATE, DELETE, and reservation lock: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
