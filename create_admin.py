"""管理者アカウントをサーバー側から登録するためのスクリプト。"""

import getpass
import sqlite3

import bcrypt


DATABASE_PATH = "database/database.db"


def main():
    admin_id = input("Admin ID: ").strip()
    password = getpass.getpass("Password: ")

    if not admin_id or len(admin_id) > 128:
        print("管理者IDは1〜128文字で入力してください。")
        return
    if len(password) < 12:
        print("パスワードは12文字以上で入力してください。")
        return

    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin (
                id TEXT PRIMARY KEY NOT NULL,
                pwd TEXT NOT NULL
            )
        """)

        existing = conn.execute(
            "SELECT 1 FROM admin WHERE id = ?", (admin_id,)
        ).fetchone()
        if existing is not None:
            print("その管理者IDはすでに登録されています。")
            return

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admin (id, pwd) VALUES (?, ?)",
            (admin_id, password_hash)
        )

    print("管理者アカウントを登録しました。")


if __name__ == "__main__":
    main()
