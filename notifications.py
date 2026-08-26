import datetime
import sqlite3
from contextlib import closing


JST = datetime.timezone(datetime.timedelta(hours=9))


def notification_now():
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


def initialize_notification_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS notification_batch (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_value TEXT,
            target_label TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            sender_type TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            notification_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            sender_type TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            read_at TEXT,
            notification_type TEXT NOT NULL,
            related_reservation_type TEXT,
            related_reservation_id INTEGER,
            batch_id INTEGER,
            FOREIGN KEY (batch_id) REFERENCES notification_batch(id)
        );
        CREATE INDEX IF NOT EXISTS idx_notification_recipient_created
            ON notification(recipient_user_id, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_recipient_unread
            ON notification(recipient_user_id, is_read);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_unique_reminder
            ON notification(notification_type, related_reservation_type,
                            related_reservation_id, recipient_user_id)
            WHERE notification_type = 'reservation_reminder';
    """)


def create_notification(db, recipient_user_id, title, body,
                        notification_type="manual", related_reservation_type=None,
                        related_reservation_id=None, batch_id=None):
    return db.execute("""
        INSERT INTO notification (
            recipient_user_id, title, body, sender_type, sender_name,
            is_read, created_at, notification_type,
            related_reservation_type, related_reservation_id, batch_id
        ) VALUES (?, ?, ?, 'admin', '管理者', 0, ?, ?, ?, ?, ?)
    """, (
        recipient_user_id, title, body, notification_now(), notification_type,
        related_reservation_type, related_reservation_id, batch_id
    )).lastrowid


def reservation_body(reservation_type, row):
    if reservation_type == "tekne":
        return f"利用日: {row['day']}\n利用時間: {row['start_time']} ～ {row['end_time']}\n使用目的: {row['purpose']}"
    if reservation_type == "equipment_takeout":
        return (f"器具名: {row['equipment']}\n利用区分: 持ち出し\n"
                f"利用期間: {row['start_day']} ～ {row['end_day']}\n数量: {row['quantity']}")
    return (f"器具名: {row['equipment']}\n利用区分: 工作室内\n利用日: {row['use_day']}\n"
            f"利用時間: {row['start_time']} ～ {row['end_time']}\n数量: {row['quantity']}")


def create_reservation_reminders(database_path, target_date=None):
    tomorrow = target_date or (datetime.datetime.now(JST).date() + datetime.timedelta(days=1))
    day = tomorrow.isoformat()
    created = 0
    with closing(sqlite3.connect(database_path)) as db:
        db.row_factory = sqlite3.Row
        initialize_notification_tables(db)
        sources = [
            ("tekne", "reservation", "day", "明日はTEKNE工作室の予約日です"),
            ("equipment_takeout", "equipment_reservation", "start_day", "明日は実験器具の利用予定日です"),
            ("equipment_in_room", "equipment_room_reservation", "use_day", "明日は実験器具の利用予定日です"),
        ]
        for reservation_type, table, date_column, title in sources:
            rows = db.execute(f"SELECT * FROM {table} WHERE {date_column} = ?", (day,)).fetchall()
            for row in rows:
                try:
                    create_notification(
                        db, row["userid"], title, reservation_body(reservation_type, row),
                        "reservation_reminder", reservation_type, row["id"]
                    )
                    created += 1
                except sqlite3.IntegrityError:
                    pass
        db.commit()
    return created
