"""Issue or reissue a school registration ID in main's local SQLite database."""

import sqlite3
from pathlib import Path

from school_registration import initialize_school_registration, issue_school_id


DATABASE_PATH = Path(__file__).resolve().parent / "database" / "database.db"


def main():
    if not DATABASE_PATH.is_file():
        print("main用SQLiteデータベースが見つかりません。アプリを初期化してから実行してください。")
        return

    school_name = input("学校名: ").strip()
    if not school_name or len(school_name) > 200:
        print("学校名は1〜200文字で入力してください。")
        return

    try:
        with sqlite3.connect(DATABASE_PATH, timeout=10) as db:
            initialize_school_registration(db)
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT 1 FROM registered_school WHERE name = ?", (school_name,)
            ).fetchone()
            reissue = False
            if existing:
                answer = input("この学校は登録済みです。学校IDを再発行しますか？ [y/N]: ").strip().lower()
                if answer != "y":
                    print("変更は行いませんでした。")
                    return
                reissue = True
            school_id = issue_school_id(db, school_name, reissue=reissue)
            db.commit()
    except (sqlite3.Error, ValueError) as error:
        print(f"学校IDを発行できませんでした: {error}")
        return

    print("学校ID（この表示を保存してください。再表示できません）:")
    print(school_id)


if __name__ == "__main__":
    main()
