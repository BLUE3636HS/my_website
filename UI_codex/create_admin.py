"""管理者アカウントをサーバー側から登録するためのスクリプト。"""

import getpass
import sqlite3
from contextlib import closing
from pathlib import Path

import bcrypt
from admin_management import migrate_admin_database, validate_admin


DATABASE_PATH = Path(__file__).resolve().parent / "database" / "database.db"


def main():
    admin_id = input("Admin ID: ").strip()
    password = getpass.getpass("Password: ")

    name = ''  # The account owner sets their profile after login.
    try:
        validate_admin(admin_id, name, password)
    except ValueError as exc:
        print(str(exc))
        return

    migrate_admin_database(DATABASE_PATH)
    with closing(sqlite3.connect(DATABASE_PATH)) as conn, conn:
        existing = conn.execute(
            "SELECT 1 FROM admin WHERE id = ?", (admin_id,)
        ).fetchone()
        if existing is not None:
            print("その管理者IDはすでに登録されています。")
            return

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admin (id, pwd, name, role) VALUES (?, ?, ?, 'super_admin')",
            (admin_id, password_hash, name)
        )

    print("管理者アカウントを登録しました。")


if __name__ == "__main__":
    main()
