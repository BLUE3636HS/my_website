"""Message threads between students and university-student mentors.

One thread exists per (student, mentor) pair. Students can write to any
published mentor, including mentors who have not registered available slots.
"""

import datetime
import secrets
import sqlite3
from contextlib import closing

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from admin_management import active_admin
from notifications import create_admin_notification, create_notification


JST = datetime.timezone(datetime.timedelta(hours=9))
MAX_INQUIRY_LENGTH = 2000
STUDENT_CSRF_KEY = "mentor_inquiry_csrf"
STUDENT_NOTICE_KEY = "mentor_inquiry_notice"
ADMIN_CSRF_KEY = "admin_mentor_inquiry_csrf"
ADMIN_NOTICE_KEY = "admin_mentor_inquiry_notice"
PUBLISHED_MENTOR_SQL = (
    "SELECT admin_id, display_name, description, profile_image FROM mentor_profile WHERE admin_id=? AND is_published=1 "
    "AND admin_id NOT IN (SELECT id FROM admin WHERE deleted_at IS NOT NULL)"
)


def initialize_mentor_inquiry_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS mentor_inquiry_thread (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            mentor_admin_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_message_at TEXT NOT NULL,
            UNIQUE (student_id, mentor_admin_id)
        );
        CREATE TABLE IF NOT EXISTS mentor_inquiry_message (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL REFERENCES mentor_inquiry_thread(id) ON DELETE CASCADE,
            sender_type TEXT NOT NULL CHECK (sender_type IN ('student', 'admin')),
            sender_id TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mentor_inquiry_thread_mentor
            ON mentor_inquiry_thread(mentor_admin_id, last_message_at DESC);
        CREATE INDEX IF NOT EXISTS idx_mentor_inquiry_message_thread
            ON mentor_inquiry_message(thread_id, created_at, id);
        CREATE INDEX IF NOT EXISTS idx_mentor_inquiry_message_unread
            ON mentor_inquiry_message(thread_id, sender_type, read_at);
    """)


def delete_mentor_inquiries_for_admin(db, admin_id):
    """Remove all threads, messages and reply notifications of a mentor."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mentor_inquiry_thread'").fetchone():
        return
    thread_ids = [row[0] for row in db.execute(
        "SELECT id FROM mentor_inquiry_thread WHERE mentor_admin_id=?", (admin_id,))]
    if not thread_ids:
        return
    placeholders = ",".join("?" for _ in thread_ids)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification'").fetchone():
        db.execute(f"""DELETE FROM notification WHERE related_reservation_type='mentor_inquiry'
                       AND related_reservation_id IN ({placeholders})""", thread_ids)
    db.execute(f"DELETE FROM mentor_inquiry_message WHERE thread_id IN ({placeholders})", thread_ids)
    db.execute(f"DELETE FROM mentor_inquiry_thread WHERE id IN ({placeholders})", thread_ids)


def _has_inquiry_tables(db):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mentor_inquiry_message'").fetchone() is not None


def _now():
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


def format_inquiry_time(value):
    return (value or "")[:16].replace("T", " ")


def _csrf_token(request, key):
    token = request.session.get(key)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[key] = token
    return token


def _valid_csrf(request, token, key):
    expected = request.session.get(key, "")
    return bool(expected and token and secrets.compare_digest(expected, token))


def _clean_body(body):
    text = (body or "").replace("\r\n", "\n").strip()
    if not text or len(text) > MAX_INQUIRY_LENGTH:
        raise ValueError(f"メッセージは1〜{MAX_INQUIRY_LENGTH}文字で入力してください。")
    return text


def _messages(db, thread_id):
    return [{
        "id": row[0], "sender_type": row[1], "body": row[2],
        "created_at": format_inquiry_time(row[3]),
    } for row in db.execute(
        """SELECT id, sender_type, body, created_at FROM mentor_inquiry_message
           WHERE thread_id=? ORDER BY created_at, id""", (thread_id,))]


def student_thread_messages(db, student_id, mentor_admin_id, mark_read=True):
    """Return the student's messages with a mentor and mark mentor replies read."""
    if not student_id or not _has_inquiry_tables(db):
        return []
    row = db.execute("SELECT id FROM mentor_inquiry_thread WHERE student_id=? AND mentor_admin_id=?",
                     (student_id, mentor_admin_id)).fetchone()
    if row is None:
        return []
    messages = _messages(db, row[0])
    if mark_read:
        db.execute("""UPDATE mentor_inquiry_message SET read_at=?
                      WHERE thread_id=? AND sender_type='admin' AND read_at IS NULL""", (_now(), row[0]))
        db.commit()
    return messages


def student_unread_counts(db, student_id):
    if not student_id or not _has_inquiry_tables(db):
        return {}
    return {row[0]: row[1] for row in db.execute(
        """SELECT t.mentor_admin_id, COUNT(*) FROM mentor_inquiry_message m
           JOIN mentor_inquiry_thread t ON t.id = m.thread_id
           WHERE t.student_id=? AND m.sender_type='admin' AND m.read_at IS NULL
           GROUP BY t.mentor_admin_id""", (student_id,))}


def admin_unread_inquiry_count(db, admin_id):
    if not admin_id or not _has_inquiry_tables(db):
        return 0
    return db.execute(
        """SELECT COUNT(*) FROM mentor_inquiry_message m
           JOIN mentor_inquiry_thread t ON t.id = m.thread_id
           WHERE t.mentor_admin_id=? AND m.sender_type='student' AND m.read_at IS NULL""",
        (admin_id,)).fetchone()[0]


def student_inquiry_context(request, db, mentor_admin_id):
    """Template values for the student inquiry page."""
    return {
        "inquiry_messages": student_thread_messages(db, request.session.get("user_id"), mentor_admin_id),
        "inquiry_csrf_token": _csrf_token(request, STUDENT_CSRF_KEY),
        "inquiry_notice": request.session.pop(STUDENT_NOTICE_KEY, None),
        "inquiry_max_length": MAX_INQUIRY_LENGTH,
    }


def build_router(database_path, templates):
    router = APIRouter()

    def require_admin(db, request):
        account = active_admin(db, request)
        if account is None:
            raise HTTPException(403, "管理者としてログインしてください。")
        return account[0]

    @router.get("/mentor-reservation/{admin_id}/inquiry", response_class=HTMLResponse)
    async def student_inquiry_page(request: Request, admin_id: str):
        from mentor_reservations import mentor_image_url

        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            mentor = db.execute(PUBLISHED_MENTOR_SQL, (admin_id,)).fetchone()
            if mentor is None:
                raise HTTPException(404, "メンターが見つかりません。")
            context = student_inquiry_context(request, db, admin_id)
        return templates.TemplateResponse(request=request, name="mentor/inquiry.html", context={
            "request": request, "user_id": request.session.get("user_id"), "mentor": mentor,
            "mentor_image_url": mentor_image_url(mentor["profile_image"]), **context})

    @router.post("/mentor-reservation/{admin_id}/inquiry")
    async def send_student_inquiry(request: Request, admin_id: str, body: str = Form(""), csrf: str = Form("")):
        redirect = RedirectResponse(f"/mentor-reservation/{admin_id}/inquiry#inquiry-form", 303)
        if not _valid_csrf(request, csrf, STUDENT_CSRF_KEY):
            request.session[STUDENT_NOTICE_KEY] = {"type": "error", "message": "操作を確認できませんでした。"}
            return redirect
        student_id = request.session.get("user_id")
        if not student_id:
            raise HTTPException(403, "ログインしてください。")
        try:
            text = _clean_body(body)
        except ValueError as error:
            request.session[STUDENT_NOTICE_KEY] = {"type": "error", "message": str(error)}
            return redirect
        try:
            with closing(sqlite3.connect(database_path, timeout=10)) as db:
                db.execute("BEGIN IMMEDIATE")
                mentor = db.execute(PUBLISHED_MENTOR_SQL, (admin_id,)).fetchone()
                if mentor is None:
                    db.rollback()
                    raise HTTPException(404, "メンターが見つかりません。")
                now = _now()
                db.execute("""INSERT INTO mentor_inquiry_thread(student_id, mentor_admin_id, created_at, last_message_at)
                              VALUES(?,?,?,?) ON CONFLICT(student_id, mentor_admin_id)
                              DO UPDATE SET last_message_at=excluded.last_message_at""",
                           (student_id, admin_id, now, now))
                thread_id = db.execute("SELECT id FROM mentor_inquiry_thread WHERE student_id=? AND mentor_admin_id=?",
                                       (student_id, admin_id)).fetchone()[0]
                db.execute("""INSERT INTO mentor_inquiry_message(thread_id, sender_type, sender_id, body, created_at)
                              VALUES(?, 'student', ?, ?, ?)""", (thread_id, student_id, text, now))
                create_admin_notification(
                    db, admin_id, "大学生メンターへの問い合わせが届きました",
                    f"生徒ID: {student_id}\n\n{text}\n\n管理画面の「メンター問い合わせ」から返信できます。",
                    None, "mentor_inquiry_received", thread_id)
                db.commit()
        except sqlite3.Error:
            request.session[STUDENT_NOTICE_KEY] = {"type": "error", "message": "メッセージを送信できませんでした。"}
            return redirect
        request.session[STUDENT_CSRF_KEY] = secrets.token_urlsafe(32)
        request.session[STUDENT_NOTICE_KEY] = {"type": "success", "message": "メッセージを送信しました。"}
        return redirect

    @router.get("/admin/mentor-inquiries", response_class=HTMLResponse)
    async def admin_inquiry_list(request: Request):
        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            admin_id = require_admin(db, request)
            rows = db.execute("""
                SELECT t.id, t.student_id, t.last_message_at, COALESCE(s.school, '') AS student_school,
                       (SELECT body FROM mentor_inquiry_message m WHERE m.thread_id=t.id
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_body,
                       (SELECT sender_type FROM mentor_inquiry_message m WHERE m.thread_id=t.id
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_sender,
                       (SELECT COUNT(*) FROM mentor_inquiry_message m WHERE m.thread_id=t.id
                        AND m.sender_type='student' AND m.read_at IS NULL) AS unread_count
                FROM mentor_inquiry_thread t LEFT JOIN student s ON s.id=t.student_id
                WHERE t.mentor_admin_id=?
                ORDER BY t.last_message_at DESC, t.id DESC""", (admin_id,)).fetchall()
        threads = [dict(row, last_message_at=format_inquiry_time(row["last_message_at"])) for row in rows]
        return templates.TemplateResponse(request=request, name="admin/mentor_inquiries.html", context={
            "request": request, "admin_id": admin_id, "threads": threads,
            "notice": request.session.pop(ADMIN_NOTICE_KEY, None)})

    def own_thread(db, admin_id, thread_id):
        row = db.execute("""SELECT t.id, t.student_id, COALESCE(s.school, '') AS student_school
                            FROM mentor_inquiry_thread t LEFT JOIN student s ON s.id=t.student_id
                            WHERE t.id=? AND t.mentor_admin_id=?""", (thread_id, admin_id)).fetchone()
        if row is None:
            raise HTTPException(404, "問い合わせが見つかりません。")
        return row

    @router.get("/admin/mentor-inquiries/{thread_id}", response_class=HTMLResponse)
    async def admin_inquiry_thread(request: Request, thread_id: int):
        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            admin_id = require_admin(db, request)
            thread = dict(own_thread(db, admin_id, thread_id))
            messages = _messages(db, thread_id)
            db.execute("""UPDATE mentor_inquiry_message SET read_at=?
                          WHERE thread_id=? AND sender_type='student' AND read_at IS NULL""", (_now(), thread_id))
            db.commit()
        return templates.TemplateResponse(request=request, name="admin/mentor_inquiry_thread.html", context={
            "request": request, "admin_id": admin_id, "thread": thread, "messages": messages,
            "csrf_token": _csrf_token(request, ADMIN_CSRF_KEY), "max_length": MAX_INQUIRY_LENGTH,
            "notice": request.session.pop(ADMIN_NOTICE_KEY, None)})

    @router.post("/admin/mentor-inquiries/{thread_id}/reply")
    async def admin_inquiry_reply(request: Request, thread_id: int, body: str = Form(""), csrf: str = Form("")):
        redirect = RedirectResponse(f"/admin/mentor-inquiries/{thread_id}#reply", 303)
        if not _valid_csrf(request, csrf, ADMIN_CSRF_KEY):
            request.session[ADMIN_NOTICE_KEY] = {"type": "error", "message": "操作を確認できませんでした。"}
            return redirect
        try:
            text = _clean_body(body)
        except ValueError as error:
            request.session[ADMIN_NOTICE_KEY] = {"type": "error", "message": str(error)}
            return redirect
        try:
            with closing(sqlite3.connect(database_path, timeout=10)) as db:
                db.execute("BEGIN IMMEDIATE")
                admin_id = require_admin(db, request)
                thread = own_thread(db, admin_id, thread_id)
                profile = db.execute("SELECT display_name FROM mentor_profile WHERE admin_id=?", (admin_id,)).fetchone()
                mentor_name = (profile[0] if profile and profile[0] else admin_id)
                now = _now()
                db.execute("""INSERT INTO mentor_inquiry_message(thread_id, sender_type, sender_id, body, created_at)
                              VALUES(?, 'admin', ?, ?, ?)""", (thread_id, admin_id, text, now))
                db.execute("UPDATE mentor_inquiry_thread SET last_message_at=? WHERE id=?", (now, thread_id))
                # Replying implies the mentor has read the student's messages.
                db.execute("""UPDATE mentor_inquiry_message SET read_at=?
                              WHERE thread_id=? AND sender_type='student' AND read_at IS NULL""", (now, thread_id))
                create_notification(
                    db, thread[1], "大学生メンターから返信がありました",
                    f"{mentor_name}さんから問い合わせへの返信があります。\n\n{text}\n\n"
                    "「大学生メンター」の一覧から該当メンターの「問い合わせる」を開くと確認・返信できます。",
                    "mentor_inquiry_reply", "mentor_inquiry", thread_id,
                    sender_type="admin", sender_name=mentor_name)
                db.commit()
        except sqlite3.Error:
            request.session[ADMIN_NOTICE_KEY] = {"type": "error", "message": "返信を送信できませんでした。"}
            return redirect
        request.session[ADMIN_CSRF_KEY] = secrets.token_urlsafe(32)
        request.session[ADMIN_NOTICE_KEY] = {"type": "success", "message": "返信を送信しました。"}
        return redirect

    return router
