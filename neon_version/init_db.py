"""Explicitly initialize the dedicated Neon database."""
import getpass
import os


def main():
    if not os.environ.get("DATABASE_URL"):
        database_url = getpass.getpass("Neon DATABASE_URL: ").strip()
        if not database_url:
            print("DATABASE_URL が必要です。Neon の接続文字列を入力してください。")
            return 1
        os.environ["DATABASE_URL"] = database_url

    from main import initialize_schema
    initialize_schema()
    print("Neon database schema initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
