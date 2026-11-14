"""Temporary announcements that administrators pin to the student dashboard."""

import datetime
import re
import secrets
import sqlite3
from contextlib import closing

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from markupsafe import Markup, escape


JST = datetime.timezone(datetime.timedelta(hours=9))

# 種類ごとに色とアイコンを固定する: (種類キー, 表示名, 色トーン, アイコン)
KINDS = (
    ("info", "お知らせ", "blue", "bell"),
    ("important", "重要", "rose", "alert"),
    ("survey", "アンケート", "green", "report"),
    ("event", "イベント", "amber", "calendar"),
    ("guide", "案内", "violet", "send"),
)
KIND_STYLES = {key: (tone, icon) for key, _, tone, icon in KINDS}
KIND_BY_ICON = {icon: key for key, _, _, icon in KINDS}
DEFAULT_KIND = "info"


def kind_for(item):
    """Return the kind key for a stored announcement (derived from its icon)."""
    return KIND_BY_ICON.get((item or {}).get("icon"), DEFAULT_KIND)

TITLE_MAX = 80
SUMMARY_MAX = 120
BODY_MAX = 5000
URL_MAX = 2000
LINK_LABEL_MAX = 30
DATETIME_FORMAT = "%Y-%m-%dT%H:%M"

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


def initialize_announcements(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_announcement (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            link_url TEXT NOT NULL DEFAULT '',
            link_label TEXT NOT NULL DEFAULT '',
            tone TEXT NOT NULL DEFAULT 'blue',
            icon TEXT NOT NULL DEFAULT 'bell',
            starts_at TEXT,
            ends_at TEXT,
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    for key, _, tone, icon in KINDS:
        db.execute("UPDATE dashboard_announcement SET tone=? WHERE icon=? AND tone<>?", (tone, icon, tone))
    default_tone, default_icon = KIND_STYLES[DEFAULT_KIND]
    placeholders = ",".join("?" for _ in KINDS)
    db.execute(f"UPDATE dashboard_announcement SET tone=?, icon=? WHERE icon NOT IN ({placeholders})",
               (default_tone, default_icon, *[icon for *_, icon in KINDS]))
    db.commit()


def now_jst_text(now=None):
    now = now or datetime.datetime.now(JST)
    if now.tzinfo is not None:
        now = now.astimezone(JST)
    return now.strftime(DATETIME_FORMAT)


def _normalize_datetime(value, label):
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.datetime.strptime(value[:16], DATETIME_FORMAT)
    except ValueError:
        raise ValueError(f"{label}の日時の形式が正しくありません。")
    return parsed.strftime(DATETIME_FORMAT)


def validate_announcement(title, summary, body, link_url, link_label, kind, starts_at, ends_at):
    title = (title or "").strip()
    summary = (summary or "").strip()
    body = (body or "").replace("\r\n", "\n").strip()
    link_url = (link_url or "").strip()
    link_label = (link_label or "").strip()
    if not title:
        raise ValueError("題名を入力してください。")
    if len(title) > TITLE_MAX:
        raise ValueError(f"題名は{TITLE_MAX}文字以内で入力してください。")
    if len(summary) > SUMMARY_MAX:
        raise ValueError(f"概要は{SUMMARY_MAX}文字以内で入力してください。")
    if len(body) > BODY_MAX:
        raise ValueError(f"本文は{BODY_MAX}文字以内で入力してください。")
    if link_url:
        if len(link_url) > URL_MAX or not re.fullmatch(r"https?://[^\s<>\"']+", link_url):
            raise ValueError("リンクURLは http:// または https:// で始まる正しいURLを入力してください。")
    elif link_label:
        raise ValueError("ボタンの文字を設定する場合はリンクURLも入力してください。")
    if len(link_label) > LINK_LABEL_MAX:
        raise ValueError(f"ボタンの文字は{LINK_LABEL_MAX}文字以内で入力してください。")
    if kind not in KIND_STYLES:
        raise ValueError("種類の選択が正しくありません。")
    tone, icon = KIND_STYLES[kind]
    starts_at = _normalize_datetime(starts_at, "表示開始")
    ends_at = _normalize_datetime(ends_at, "表示終了")
    if starts_at and ends_at and ends_at <= starts_at:
        raise ValueError("表示終了は表示開始より後の日時にしてください。")
    return {
        "title": title, "summary": summary, "body": body, "link_url": link_url,
        "link_label": link_label, "tone": tone, "icon": icon,
        "starts_at": starts_at, "ends_at": ends_at,
    }


COLUMNS = ("id", "title", "summary", "body", "link_url", "link_label", "tone", "icon",
           "starts_at", "ends_at", "created_by", "created_at", "updated_at")
SELECT_COLUMNS = ", ".join(COLUMNS)
ACTIVE_CONDITION = "(starts_at IS NULL OR starts_at <= ?) AND (ends_at IS NULL OR ends_at > ?)"


def _rows_to_dicts(rows):
    return [dict(zip(COLUMNS, row)) for row in rows]


def active_announcements(db, now=None):
    current = now_jst_text(now)
    rows = db.execute(
        f"SELECT {SELECT_COLUMNS} FROM dashboard_announcement WHERE {ACTIVE_CONDITION} "
        "ORDER BY COALESCE(starts_at, created_at) DESC, id DESC",
        (current, current),
    ).fetchall()
    return _rows_to_dicts(rows)


def active_announcement(db, announcement_id, now=None):
    current = now_jst_text(now)
    row = db.execute(
        f"SELECT {SELECT_COLUMNS} FROM dashboard_announcement WHERE id=? AND {ACTIVE_CONDITION}",
        (announcement_id, current, current),
    ).fetchone()
    return _rows_to_dicts([row])[0] if row else None


def announcement_status(item, now=None):
    current = now_jst_text(now)
    if item["starts_at"] and item["starts_at"] > current:
        return "scheduled"
    if item["ends_at"] and item["ends_at"] <= current:
        return "ended"
    return "active"


def linkify(text):
    """Escape text and turn bare http(s) URLs into links that open in a new tab."""
    escaped = str(escape(text or ""))
    def replace(match):
        url = match.group(0)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
    return Markup(URL_PATTERN.sub(replace, escaped))


def format_period(value):
    if not value:
        return ""
    return value.replace("-", "/").replace("T", " ")


def build_announcement_router(database_path, templates):
    router = APIRouter()

    def require_admin(db, request):
        from admin_management import active_admin
        admin = active_admin(db, request)
        if admin is None:
            raise HTTPException(403, "管理者としてログインしてください。")
        return admin

    def check_csrf(request, token):
        expected = request.session.get("announcement_csrf", "")
        if not expected or not secrets.compare_digest(expected, token or ""):
            raise HTTPException(403, "CSRFトークンが不正です。")

    def render(request, form=None, editing_id=None, error=None, status_code=200):
        with closing(sqlite3.connect(database_path)) as db:
            require_admin(db, request)
            items = _rows_to_dicts(db.execute(
                f"SELECT {SELECT_COLUMNS} FROM dashboard_announcement ORDER BY id DESC"
            ).fetchall())
        for item in items:
            item["status"] = announcement_status(item)
        token = request.session.setdefault("announcement_csrf", secrets.token_urlsafe(32))
        return templates.TemplateResponse(
            request=request,
            name="admin/announcements.html",
            context={
                "request": request, "items": items, "kinds": KINDS,
                "form": form or {"kind": DEFAULT_KIND},
                "editing_id": editing_id, "csrf_token": token, "error": error,
                "notice": request.session.pop("announcement_notice", None),
                "admin_id": request.session.get("admin_id"),
                "limits": {"title": TITLE_MAX, "summary": SUMMARY_MAX, "body": BODY_MAX,
                           "url": URL_MAX, "link_label": LINK_LABEL_MAX},
            },
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/admin/announcements")
    async def announcements_page(request: Request, edit: int | None = None):
        form = None
        if edit is not None:
            with closing(sqlite3.connect(database_path)) as db:
                require_admin(db, request)
                row = db.execute(f"SELECT {SELECT_COLUMNS} FROM dashboard_announcement WHERE id=?",
                                 (edit,)).fetchone()
            if row is None:
                raise HTTPException(404, "掲示が見つかりません。")
            form = _rows_to_dicts([row])[0]
            form["kind"] = kind_for(form)
        return render(request, form=form, editing_id=edit if form else None)

    async def save(request, announcement_id, csrf_token, title, summary, body, link_url,
                   link_label, kind, starts_at, ends_at):
        raw = {"title": title, "summary": summary, "body": body, "link_url": link_url,
               "link_label": link_label, "kind": kind,
               "starts_at": starts_at, "ends_at": ends_at}
        check_csrf(request, csrf_token)
        try:
            data = validate_announcement(**raw)
        except ValueError as exc:
            return render(request, form=raw, editing_id=announcement_id, error=str(exc), status_code=400)
        now = datetime.datetime.now(JST).isoformat(timespec="seconds")
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            admin = require_admin(db, request)
            values = (data["title"], data["summary"], data["body"], data["link_url"], data["link_label"],
                      data["tone"], data["icon"], data["starts_at"], data["ends_at"])
            if announcement_id is None:
                db.execute(
                    "INSERT INTO dashboard_announcement(title,summary,body,link_url,link_label,tone,icon,"
                    "starts_at,ends_at,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    values + (admin[0], now, now),
                )
                notice = "掲示を作成しました。"
            else:
                cursor = db.execute(
                    "UPDATE dashboard_announcement SET title=?,summary=?,body=?,link_url=?,link_label=?,"
                    "tone=?,icon=?,starts_at=?,ends_at=?,updated_at=? WHERE id=?",
                    values + (now, announcement_id),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(404, "掲示が見つかりません。")
                notice = "掲示を更新しました。"
            db.commit()
        request.session["announcement_notice"] = notice
        return RedirectResponse("/admin/announcements", status_code=303)

    @router.post("/admin/announcements")
    async def create_announcement(request: Request, csrf_token: str = Form(""), title: str = Form(""),
                                  summary: str = Form(""), body: str = Form(""), link_url: str = Form(""),
                                  link_label: str = Form(""), kind: str = Form(DEFAULT_KIND),
                                  starts_at: str = Form(""), ends_at: str = Form("")):
        return await save(request, None, csrf_token, title, summary, body, link_url, link_label,
                          kind, starts_at, ends_at)

    @router.post("/admin/announcements/{announcement_id}")
    async def update_announcement(request: Request, announcement_id: int, csrf_token: str = Form(""),
                                  title: str = Form(""), summary: str = Form(""), body: str = Form(""),
                                  link_url: str = Form(""), link_label: str = Form(""),
                                  kind: str = Form(DEFAULT_KIND),
                                  starts_at: str = Form(""), ends_at: str = Form("")):
        return await save(request, announcement_id, csrf_token, title, summary, body, link_url, link_label,
                          kind, starts_at, ends_at)

    @router.post("/admin/announcements/{announcement_id}/end")
    async def end_announcement(request: Request, announcement_id: int, csrf_token: str = Form("")):
        check_csrf(request, csrf_token)
        current = now_jst_text()
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            require_admin(db, request)
            db.execute(
                "UPDATE dashboard_announcement SET ends_at=?, "
                "starts_at=CASE WHEN starts_at IS NOT NULL AND starts_at>=? THEN NULL ELSE starts_at END, "
                "updated_at=? WHERE id=?",
                (current, current, datetime.datetime.now(JST).isoformat(timespec="seconds"), announcement_id),
            )
            db.commit()
        request.session["announcement_notice"] = "掲示の表示を終了しました。"
        return RedirectResponse("/admin/announcements", status_code=303)

    @router.post("/admin/announcements/{announcement_id}/delete")
    async def delete_announcement(request: Request, announcement_id: int, csrf_token: str = Form("")):
        check_csrf(request, csrf_token)
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            require_admin(db, request)
            db.execute("DELETE FROM dashboard_announcement WHERE id=?", (announcement_id,))
            db.commit()
        request.session["announcement_notice"] = "掲示を削除しました。"
        return RedirectResponse("/admin/announcements", status_code=303)

    @router.get("/announcements/{announcement_id}")
    async def announcement_detail(request: Request, announcement_id: int):
        if request.session.get("user_login") is not True or not request.session.get("user_id"):
            return RedirectResponse("/login", status_code=303)
        with closing(sqlite3.connect(database_path)) as db:
            item = active_announcement(db, announcement_id)
        if item is None:
            raise HTTPException(404, "この掲示は表示期間外か、削除されました。")
        return templates.TemplateResponse(
            request=request,
            name="announcement_detail.html",
            context={"request": request, "item": item,
                     "user_login": True, "user_id": request.session.get("user_id")},
        )

    return router
