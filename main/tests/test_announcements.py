import asyncio
import datetime
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import announcements
from announcements import (
    JST,
    active_announcement,
    active_announcements,
    announcement_status,
    build_announcement_router,
    format_period,
    initialize_announcements,
    linkify,
    validate_announcement,
)

BASE_DIR = Path(__file__).resolve().parent.parent


def make_request(path, session, method="GET", query=b""):
    return Request({
        "type": "http", "method": method, "path": path, "root_path": "",
        "scheme": "http", "server": ("test", 80), "client": ("test", 1),
        "headers": [], "query_string": query, "session": session,
    })


def valid(**overrides):
    data = {"title": "アンケート", "summary": "", "body": "", "link_url": "", "link_label": "",
            "kind": "info", "starts_at": "", "ends_at": ""}
    data.update(overrides)
    return data


class ValidationTests(unittest.TestCase):
    def test_minimal_announcement_has_no_period(self):
        data = validate_announcement(**valid())
        self.assertIsNone(data["starts_at"])
        self.assertIsNone(data["ends_at"])

    def test_rejects_bad_input(self):
        cases = [
            valid(title="  "),
            valid(title="x" * 81),
            valid(link_url="javascript:alert(1)"),
            valid(link_url="ftp://example.com"),
            valid(link_label="回答する"),
            valid(kind="black"),
            valid(starts_at="2026/11/14"),
            valid(starts_at="2026-11-20T10:00", ends_at="2026-11-20T10:00"),
            valid(starts_at="2026-11-20T10:00", ends_at="2026-11-19T10:00"),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_announcement(**case)

    def test_accepts_forms_url_and_period(self):
        data = validate_announcement(**valid(
            link_url="https://forms.office.com/r/abc?x=1", link_label="回答する",
            starts_at="2026-11-14T09:00", ends_at="2026-11-30T23:59", body="a\r\nb"))
        self.assertEqual(data["starts_at"], "2026-11-14T09:00")
        self.assertEqual(data["body"], "a\nb")

    def test_kind_decides_color_and_icon(self):
        self.assertEqual((validate_announcement(**valid(kind="important"))["tone"],
                          validate_announcement(**valid(kind="important"))["icon"]), ("rose", "alert"))
        data = validate_announcement(**valid(kind="info"))
        self.assertEqual((data["tone"], data["icon"]), ("blue", "bell"))

    def test_legacy_rows_are_normalized_to_a_kind(self):
        db = sqlite3.connect(":memory:")
        initialize_announcements(db)
        db.execute("INSERT INTO dashboard_announcement(title,tone,icon,created_at,updated_at) VALUES ('a','teal','chat','x','x')")
        db.execute("INSERT INTO dashboard_announcement(title,tone,icon,created_at,updated_at) VALUES ('b','blue','alert','x','x')")
        initialize_announcements(db)
        self.assertEqual(db.execute("SELECT tone, icon FROM dashboard_announcement ORDER BY id").fetchall(),
                         [("blue", "bell"), ("rose", "alert")])
        db.close()

    def test_linkify_escapes_html_and_links_urls(self):
        html = str(linkify('<script>x</script> https://forms.office.com/r/a?b=1&c=2 end'))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="https://forms.office.com/r/a?b=1&amp;c=2"', html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_format_period(self):
        self.assertEqual(format_period("2026-11-14T09:00"), "2026/11/14 09:00")
        self.assertEqual(format_period(None), "")


class ActiveWindowTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        initialize_announcements(self.db)
        rows = [
            ("無期限", None, None),
            ("開始前", "2026-11-20T00:00", None),
            ("終了済み", None, "2026-11-14T12:00"),
            ("期間内", "2026-11-14T00:00", "2026-11-15T00:00"),
        ]
        for title, start, end in rows:
            self.db.execute(
                "INSERT INTO dashboard_announcement(title,starts_at,ends_at,created_at,updated_at) VALUES (?,?,?,?,?)",
                (title, start, end, "2026-11-01T00:00:00+09:00", "2026-11-01T00:00:00+09:00"))
        self.db.commit()
        self.now = datetime.datetime(2026, 11, 14, 12, 0, tzinfo=JST)

    def test_only_items_inside_window_are_active(self):
        titles = {item["title"] for item in active_announcements(self.db, self.now)}
        self.assertEqual(titles, {"無期限", "期間内"})

    def test_utc_now_is_converted_to_jst(self):
        utc_now = datetime.datetime(2026, 11, 14, 2, 59, tzinfo=datetime.timezone.utc)  # 11:59 JST
        titles = {item["title"] for item in active_announcements(self.db, utc_now)}
        self.assertIn("終了済み", titles)

    def test_single_lookup_respects_window(self):
        ids = {row[1]: row[0] for row in self.db.execute("SELECT id,title FROM dashboard_announcement")}
        self.assertIsNotNone(active_announcement(self.db, ids["無期限"], self.now))
        self.assertIsNone(active_announcement(self.db, ids["開始前"], self.now))
        self.assertIsNone(active_announcement(self.db, ids["終了済み"], self.now))

    def test_status_labels(self):
        items = {i["title"]: i for i in [dict(zip(announcements.COLUMNS, r)) for r in self.db.execute(
            f"SELECT {announcements.SELECT_COLUMNS} FROM dashboard_announcement")]}
        self.assertEqual(announcement_status(items["開始前"], self.now), "scheduled")
        self.assertEqual(announcement_status(items["終了済み"], self.now), "ended")
        self.assertEqual(announcement_status(items["無期限"], self.now), "active")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "test.db"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("CREATE TABLE admin (id TEXT PRIMARY KEY, pwd TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin', "
                       "deleted_at TEXT, session_version INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT '')")
            db.execute("INSERT INTO admin(id,pwd,role) VALUES ('A1','x','admin')")
            initialize_announcements(db)
        self.templates = Jinja2Templates(
            directory=str(BASE_DIR / "templates"),
            context_processors=[lambda request: {"student_features": {}, "unread_notification_count": 0,
                                                 "is_super_admin": False}])
        self.templates.env.filters["linkify"] = linkify
        self.templates.env.filters["announcement_period"] = format_period
        router = build_announcement_router(self.db_path, self.templates)
        self.endpoints = {(route.path, list(route.methods)[0]): route.endpoint for route in router.routes}
        self.admin_session = {"admin_login": True, "admin_id": "A1", "admin_session_version": 0,
                              "announcement_csrf": "token"}

    def call(self, path, method, request, **kwargs):
        return asyncio.run(self.endpoints[(path, method)](request, **kwargs))

    def create(self, **overrides):
        form = valid(**overrides)
        return self.call("/admin/announcements", "POST",
                         make_request("/admin/announcements", self.admin_session, "POST"),
                         csrf_token="token", **form)

    def test_admin_create_and_list(self):
        response = self.create(title="活動アンケート", link_url="https://forms.office.com/r/x")
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.db_path)) as db:
            row = db.execute("SELECT title, created_by, starts_at, ends_at FROM dashboard_announcement").fetchone()
        self.assertEqual(row, ("活動アンケート", "A1", None, None))
        page = self.call("/admin/announcements", "GET", make_request("/admin/announcements", self.admin_session))
        body = page.body.decode()
        self.assertIn("活動アンケート", body)
        self.assertIn("無期限", body)
        self.assertIn("表示中", body)
        self.assertIn('name="kind" value="important"', body)

    def test_invalid_input_rerenders_with_error(self):
        response = self.create(title="", body="残す本文")
        self.assertEqual(response.status_code, 400)
        self.assertIn("題名を入力してください", response.body.decode())
        self.assertIn("残す本文", response.body.decode())

    def test_csrf_and_auth_required(self):
        with self.assertRaises(HTTPException) as ctx:
            self.call("/admin/announcements", "POST",
                      make_request("/admin/announcements", self.admin_session, "POST"),
                      csrf_token="wrong", **valid())
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as ctx:
            self.call("/admin/announcements", "GET",
                      make_request("/admin/announcements", {"user_login": True, "user_id": "s"}))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_update_end_and_delete(self):
        self.create(title="旧題名")
        with closing(sqlite3.connect(self.db_path)) as db:
            item_id = db.execute("SELECT id FROM dashboard_announcement").fetchone()[0]
        path = "/admin/announcements/{announcement_id}"
        self.call(path, "POST", make_request(f"/admin/announcements/{item_id}", self.admin_session, "POST"),
                  announcement_id=item_id, csrf_token="token", **valid(title="新題名", ends_at="2099-01-01T00:00"))
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT title, ends_at FROM dashboard_announcement").fetchone(),
                             ("新題名", "2099-01-01T00:00"))
            self.assertEqual(len(active_announcements(db)), 1)
        self.call(path + "/end", "POST", make_request("/x", self.admin_session, "POST"),
                  announcement_id=item_id, csrf_token="token")
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(active_announcements(db), [])
        self.call(path + "/delete", "POST", make_request("/x", self.admin_session, "POST"),
                  announcement_id=item_id, csrf_token="token")
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM dashboard_announcement").fetchone()[0], 0)

    def test_end_scheduled_item_hides_it(self):
        self.create(title="予定", starts_at="2099-01-01T00:00")
        with closing(sqlite3.connect(self.db_path)) as db:
            item_id = db.execute("SELECT id FROM dashboard_announcement").fetchone()[0]
        self.call("/admin/announcements/{announcement_id}/end", "POST", make_request("/x", self.admin_session, "POST"),
                  announcement_id=item_id, csrf_token="token")
        with closing(sqlite3.connect(self.db_path)) as db:
            row = db.execute(f"SELECT {announcements.SELECT_COLUMNS} FROM dashboard_announcement").fetchone()
            self.assertEqual(announcement_status(dict(zip(announcements.COLUMNS, row))), "ended")

    def test_student_detail_page(self):
        self.create(title="回答のお願い", body="詳細は https://example.com/a を参照", link_url="https://forms.office.com/r/x",
                    link_label="回答する", kind="survey")
        with closing(sqlite3.connect(self.db_path)) as db:
            item_id = db.execute("SELECT id FROM dashboard_announcement").fetchone()[0]
        student = {"user_login": True, "user_id": "S1"}
        path = "/announcements/{announcement_id}"
        response = self.call(path, "GET", make_request(f"/announcements/{item_id}", student), announcement_id=item_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template.name, "announcement_detail.html")
        html = response.body.decode()
        self.assertIn("回答のお願い", html)
        self.assertIn('href="https://forms.office.com/r/x"', html)
        self.assertIn(">回答する<", html)
        self.assertIn('<a href="https://example.com/a"', html)
        # Not visible to logged-out users
        redirect = self.call(path, "GET", make_request("/announcements/1", {}), announcement_id=item_id)
        self.assertEqual(redirect.status_code, 303)
        # Hidden once the period is over
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE dashboard_announcement SET ends_at='2000-01-01T00:00'")
            db.commit()
        with self.assertRaises(HTTPException) as ctx:
            self.call(path, "GET", make_request("/announcements/1", student), announcement_id=item_id)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
