import datetime
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import main

TABLES = ("equipment_catalog", "equipment_reservation", "equipment_room_reservation",
          "notification", "notification_batch")


def request(session, path="/equipment-reservation"):
    scope = {"type": "http", "method": "POST", "scheme": "http", "path": path,
             "server": ("test", 80), "headers": [], "query_string": b"", "session": session}

    async def receive():
        return {"type": "http.disconnect"}
    return Request(scope, receive=receive)


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Test coroutine unexpectedly yielded")


class EquipmentBulkReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "bulk.db"
        # Copy the live schema so the test tables never drift from the real database.
        with closing(sqlite3.connect(main.DATABASE_PATH)) as source, closing(sqlite3.connect(self.path)) as db:
            for (sql,) in source.execute(
                f"SELECT sql FROM sqlite_master WHERE type='table' AND name IN ({','.join('?' * len(TABLES))})",
                TABLES
            ).fetchall():
                db.execute(sql)
            db.executemany("INSERT INTO equipment_catalog(id,name,image,content,count,usage_type) VALUES (?,?,?,?,?,?)", [
                ("T1", "顕微鏡", "/i.png", "", 3, "takeout"),
                ("T2", "テスター", "/i.png", "", 2, "takeout"),
                ("R1", "レーザー加工機", "/i.png", "", 1, "in_room"),
                ("R2", "3Dプリンター", "/i.png", "", 2, "in_room"),
            ])
            db.commit()
        db_patch = patch.object(main, "DATABASE_PATH", self.path)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.session = {"user_login": True, "user_id": "student1"}
        self.day = (datetime.datetime.now(main.JST).date() + datetime.timedelta(days=3)).isoformat()

    def query(self, sql, params=()):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql, params).fetchall()

    def takeout(self, ids, quantities, start=None, end=None, user="student1"):
        session = {"user_login": True, "user_id": user}
        return run_immediate(main.CreateEquipmentReservation(
            request(session), ids, "", start or self.day, end or self.day, quantities, "実験", "備考"))

    def room(self, ids, quantities, start="10:00", end="11:00", user="student1"):
        session = {"user_login": True, "user_id": user}
        return run_immediate(main.CreateEquipmentRoomReservation(
            request(session, "/equipment-room-reservation"), ids, self.day, start, end, quantities, "実験", ""))

    def assert_http(self, status, func, *args, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            func(*args, **kwargs)
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception.detail

    # ---------- 持ち出し ----------
    def test_takeout_bulk_creates_rows_and_one_notification(self):
        result = self.takeout(["T1", "T2"], [2, 1])
        self.assertEqual(len(result["reservation_ids"]), 2)
        self.assertEqual(self.query(
            "SELECT equipment_id,equipment,quantity,start_day,end_day,purpose,note,userid FROM equipment_reservation ORDER BY id"), [
            ("T1", "顕微鏡", 2, self.day, self.day, "実験", "備考", "student1"),
            ("T2", "テスター", 1, self.day, self.day, "実験", "備考", "student1"),
        ])
        notifications = self.query(
            "SELECT title,body,notification_type,related_reservation_type,related_reservation_id FROM notification")
        self.assertEqual(len(notifications), 1)
        title, body, ntype, rtype, rid = notifications[0]
        self.assertEqual((title, ntype, rtype, rid),
                         ("実験器具の予約を受け付けました", "reservation_created", "equipment_takeout", result["reservation_ids"][0]))
        self.assertIn("・顕微鏡 × 2", body)
        self.assertIn("・テスター × 1", body)
        self.assertIn("利用区分: 持ち出し", body)

    def test_takeout_single_keeps_legacy_body_and_scalar_args(self):
        self.takeout("T1", 1)
        body = self.query("SELECT body FROM notification")[0][0]
        self.assertTrue(body.startswith("器具名: 顕微鏡\n利用区分: 持ち出し"))

    def test_takeout_legacy_name_lookup_still_works(self):
        run_immediate(main.CreateEquipmentReservation(
            request(self.session), [], "テスター", self.day, self.day, [1], "実験", ""))
        self.assertEqual(self.query("SELECT equipment_id FROM equipment_reservation"), [("T2",)])

    def test_takeout_shortage_on_one_item_rejects_everything(self):
        self.takeout(["T2"], [2], user="other")  # テスター is now fully booked
        detail = self.assert_http(409, self.takeout, ["T1", "T2"], [1, 1])
        self.assertIn("テスター（利用可能数: 0）", detail)
        self.assertNotIn("顕微鏡", detail)
        self.assertEqual(self.query("SELECT COUNT(*) FROM equipment_reservation WHERE userid='student1'"), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM notification WHERE recipient_user_id='student1'"), [(0,)])

    def test_takeout_availability_is_per_item_and_per_day(self):
        start = datetime.date.fromisoformat(self.day)
        next_day = (start + datetime.timedelta(days=1)).isoformat()
        self.takeout(["T1"], [2], user="other")
        # Overlapping period: 顕微鏡 has 1 left, テスター still 2.
        self.takeout(["T1", "T2"], [1, 2], start=self.day, end=next_day)
        # 顕微鏡 is full on the first day (2 + 1 of 3), but still has 2 on the next day.
        detail = self.assert_http(409, self.takeout, ["T1"], [1], start=self.day, end=next_day)
        self.assertIn("顕微鏡（利用可能数: 0）", detail)
        self.takeout(["T1"], [2], start=next_day, end=next_day)
        self.assertEqual(self.query("SELECT COUNT(*) FROM equipment_reservation"), [(4,)])

    def test_takeout_rejects_room_only_items(self):
        detail = self.assert_http(400, self.takeout, ["T1", "R1"], [1, 1])
        self.assertIn("工作室内のみの器具は持ち出し予約できません: レーザー加工機", detail)
        self.assertEqual(self.query("SELECT COUNT(*) FROM equipment_reservation"), [(0,)])

    def test_takeout_rejects_in_room_only_selection(self):
        self.assert_http(400, self.takeout, ["R1"], [1])

    def test_validation_errors(self):
        self.assert_http(400, self.takeout, ["T1", "T1"], [1, 1])      # duplicate
        self.assert_http(400, self.takeout, ["T1", "T2"], [1])         # missing quantity
        self.assert_http(400, self.takeout, ["T1", "T2"], [1, 0])      # zero quantity
        self.assert_http(400, self.takeout, ["T1", "NOPE"], [1, 1])    # unknown item
        self.assert_http(400, self.takeout, [], [])                    # nothing selected
        self.assertEqual(self.query("SELECT COUNT(*) FROM equipment_reservation"), [(0,)])

    # ---------- 工作室内 ----------
    def test_room_bulk_creates_rows_and_one_notification(self):
        result = self.room(["R1", "R2"], [1, 2])
        self.assertEqual(self.query(
            "SELECT equipment_id,quantity,use_day,start_time,end_time FROM equipment_room_reservation ORDER BY id"), [
            ("R1", 1, self.day, "10:00", "11:00"),
            ("R2", 2, self.day, "10:00", "11:00"),
        ])
        rows = self.query("SELECT body,related_reservation_type,related_reservation_id FROM notification")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], ("equipment_in_room", result["reservation_ids"][0]))
        self.assertIn("利用時間: 10:00 ～ 11:00", rows[0][0])
        self.assertIn("・3Dプリンター × 2", rows[0][0])

    def test_room_shortage_on_one_item_rejects_everything(self):
        self.room(["R1"], [1], start="10:30", end="11:00", user="other")
        detail = self.assert_http(409, self.room, ["R1", "R2"], [1, 1])
        self.assertIn("レーザー加工機（利用可能数: 0）", detail)
        self.assertEqual(self.query("SELECT COUNT(*) FROM equipment_room_reservation WHERE userid='student1'"), [(0,)])
        # Non-overlapping time works for both items.
        self.room(["R1", "R2"], [1, 1], start="11:00", end="12:00")
        self.assertEqual(self.query("SELECT COUNT(*) FROM equipment_room_reservation WHERE userid='student1'"), [(2,)])

    def test_room_accepts_takeout_items_and_requires_login(self):
        ids = self.room(["R1", "T1"], [1, 2])["reservation_ids"]
        self.assertEqual(self.query("SELECT equipment_id,quantity FROM equipment_room_reservation ORDER BY id"),
                         [("R1", 1), ("T1", 2)])
        self.assertEqual(self.query("SELECT related_reservation_type,related_reservation_id FROM notification"),
                         [("equipment_in_room", ids[0])])
        with self.assertRaises(HTTPException) as caught:
            run_immediate(main.CreateEquipmentRoomReservation(
                request({}), ["R1"], self.day, "10:00", "11:00", [1], "実験", ""))
        self.assertEqual(caught.exception.status_code, 401)

    # ---------- 持ち出しと室内利用の在庫共有 ----------
    def test_takeout_reduces_room_availability_for_the_whole_day(self):
        self.takeout(["T2"], [1], user="other")          # テスター 2台中1台を持ち出し
        detail = self.assert_http(409, self.room, ["T2"], [2])
        self.assertIn("テスター（利用可能数: 1）", detail)
        self.room(["T2"], [1], start="15:00", end="16:00")
        self.assert_http(409, self.room, ["T2"], [1], start="15:30", end="16:30")

    def test_room_use_reduces_takeout_availability_by_daily_peak(self):
        # 顕微鏡(3台): 10-11時に2台、11-12時に1台 → 同時使用のピークは2台
        self.room(["T1"], [2], start="10:00", end="11:00", user="other")
        self.room(["T1"], [1], start="11:00", end="12:00", user="other")
        detail = self.assert_http(409, self.takeout, ["T1"], [2])
        self.assertIn("顕微鏡（利用可能数: 1）", detail)
        self.takeout(["T1"], [1])
        # 翌日には影響しない
        next_day = (datetime.date.fromisoformat(self.day) + datetime.timedelta(days=1)).isoformat()
        self.takeout(["T1"], [2], start=next_day, end=next_day)

    def test_room_peak_counts_only_truly_overlapping_bookings(self):
        # 10-11, 10:30-12, 11:30-12:30 で各1台 → 同時使用のピークは2台（3台ではない）
        for start, end in (("10:00", "11:00"), ("10:30", "12:00"), ("11:30", "12:30")):
            self.room(["T1"], [1], start=start, end=end, user="other")
        self.takeout(["T1"], [1])
        self.assert_http(409, self.takeout, ["T1"], [1])

    def test_cancel_one_item_of_bulk_keeps_others(self):
        ids = self.room(["R1", "R2"], [1, 1])["reservation_ids"]
        run_immediate(main.CancelEquipmentRoomReservation(request(self.session), ids[0]))
        self.assertEqual(self.query("SELECT id FROM equipment_room_reservation"), [(ids[1],)])
        takeout_ids = self.takeout(["T1", "T2"], [1, 1])["reservation_ids"]
        run_immediate(main.CancelEquipmentReservation(request(self.session), takeout_ids[1]))
        self.assertEqual(self.query("SELECT id FROM equipment_reservation"), [(takeout_ids[0],)])
        self.assertEqual(self.query(
            "SELECT notification_type,COUNT(*) FROM notification GROUP BY notification_type ORDER BY notification_type"),
            [("reservation_cancelled", 2), ("reservation_created", 2)])


if __name__ == "__main__":
    unittest.main()
