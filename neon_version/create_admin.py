
import getpass
import os
import pg_compat as dbapi

import bcrypt
import psycopg


def main():
    if not os.environ.get("DATABASE_URL"):
        database_url = getpass.getpass("Neon DATABASE_URL: ").strip()
        if not database_url:
            print("DATABASE_URL が必要です。Neon の接続文字列を入力してください。")
            return 1
        os.environ["DATABASE_URL"] = database_url

    admin_id = input("Admin ID: ").strip()
    password = getpass.getpass("Password: ")

    if not admin_id or len(admin_id) > 128:
        print("管理者IDは1〜128文字で入力してください。")
        return 1
    if len(password) < 12:
        print("パスワードは12文字以上で入力してください。")
        return 1

    try:
        with dbapi.connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM admin WHERE id = ?", (admin_id,)
            ).fetchone()
            if existing is not None:
                print("その管理者IDはすでに登録されています。")
                return 1

            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            conn.execute(
                "INSERT INTO admin (id, pwd) VALUES (?, ?)",
                (admin_id, password_hash)
            )
    except psycopg.errors.UndefinedTable:
        print("admin テーブルがありません。先に neon_version/init_db.py を実行してください。")
        return 1
    except psycopg.OperationalError:
        print("Neon に接続できませんでした。DATABASE_URL とネットワーク接続を確認してください。")
        return 1

    print("管理者アカウントを登録しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
