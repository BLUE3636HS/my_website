import datetime
import secrets
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from notifications import create_notification, reservation_body


JST = datetime.timezone(datetime.timedelta(hours=9))
OPEN_MINUTES = 9 * 60
CLOSE_MINUTES = 22 * 60
BOOKING_CONFLICT_MESSAGE = (
    "すでに生徒の予約が入っています。予約を削除してから再度対応可能時間の変更を行ってください"
)


def initialize_mentor_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS mentor_profile (
            admin_id TEXT PRIMARY KEY NOT NULL,
            is_published INTEGER NOT NULL DEFAULT 0 CHECK (is_published IN (0, 1)),
            display_name TEXT NOT NULL DEFAULT '',
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mentor_available_slot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id TEXT NOT NULL,
            day TEXT NOT NULL,
            start_time TEXT NOT NULL,
            online_available INTEGER NOT NULL DEFAULT 0 CHECK (online_available IN (0, 1)),
            offline_available INTEGER NOT NULL DEFAULT 0 CHECK (offline_available IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (admin_id, day, start_time),
            CHECK (online_available = 1 OR offline_available = 1)
        );
        CREATE TABLE IF NOT EXISTS mentor_reservation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            mentor_admin_id TEXT NOT NULL,
            day TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            meeting_type TEXT NOT NULL CHECK (meeting_type IN ('online', 'offline')),
            consultation TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
            created_at TEXT NOT NULL,
            cancelled_at TEXT,
            cancelled_by_type TEXT,
            cancelled_by_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mentor_slot_admin_day_time
            ON mentor_available_slot(admin_id, day, start_time);
        CREATE INDEX IF NOT EXISTS idx_mentor_reservation_mentor_active
            ON mentor_reservation(mentor_admin_id, day, status, start_time, end_time);
        CREATE INDEX IF NOT EXISTS idx_mentor_reservation_student_active
            ON mentor_reservation(student_id, day, status, start_time, end_time);
    """)


def time_to_minutes(value):
    parsed = datetime.datetime.strptime(value, "%H:%M").time()
    return parsed.hour * 60 + parsed.minute


def minutes_to_time(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def slot_range(start_time, end_time):
    return [minutes_to_time(value) for value in range(time_to_minutes(start_time), time_to_minutes(end_time), 30)]


def valid_csrf(request, token, key):
    expected = request.session.get(key, "")
    return bool(expected and token and secrets.compare_digest(expected, token))


def csrf_token(request, key):
    token = request.session.get(key)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[key] = token
    return token


def validate_range(day, start_time, end_time, *, tomorrow_only=True, max_minutes=None):
    try:
        target_day = datetime.date.fromisoformat(day)
        start = time_to_minutes(start_time)
        end = time_to_minutes(end_time)
    except (TypeError, ValueError):
        raise ValueError("日付または時間帯が正しくありません。")
    earliest = datetime.datetime.now(JST).date() + datetime.timedelta(days=1 if tomorrow_only else 0)
    duration = end - start
    if (target_day < earliest or start < OPEN_MINUTES or end > CLOSE_MINUTES or
            start >= end or start % 30 or end % 30 or (max_minutes and duration > max_minutes)):
        raise ValueError("日付または時間帯が正しくありません。")
    return target_day, start, end


def mentor_reservations_for_student(db, student_id):
    db.row_factory = sqlite3.Row
    today = datetime.datetime.now(JST).date().isoformat()
    rows = db.execute("""
        SELECT r.*, COALESCE(NULLIF(p.display_name, ''), r.mentor_admin_id) AS mentor_name
        FROM mentor_reservation r
        LEFT JOIN mentor_profile p ON p.admin_id = r.mentor_admin_id
        WHERE r.student_id = ?
        ORDER BY CASE WHEN r.status = 'active' AND r.day >= ? THEN 0 ELSE 1 END,
                 CASE WHEN r.status = 'active' AND r.day >= ? THEN r.day END ASC,
                 CASE WHEN r.status = 'active' AND r.day >= ? THEN r.start_time END ASC,
                 r.day DESC, r.start_time DESC, r.id DESC
    """, (student_id, today, today, today)).fetchall()
    return [dict(row) for row in rows]


def build_router(database_path, templates):
    router = APIRouter()

    @router.get("/admin/mentor-profile", response_class=HTMLResponse)
    async def admin_mentor_profile(request: Request):
        admin_id = request.session.get("admin_id")
        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            profile = db.execute("SELECT * FROM mentor_profile WHERE admin_id = ?", (admin_id,)).fetchone()
        return templates.TemplateResponse(request=request, name="admin/mentor_profile.html", context={
            "request": request, "admin_id": admin_id, "profile": profile,
            "csrf_token": csrf_token(request, "mentor_profile_csrf"),
            "notice": request.session.pop("mentor_profile_notice", None),
        })

    @router.post("/admin/mentor-profile")
    async def update_admin_mentor_profile(request: Request, display_name: str = Form(""),
                                          is_published: str = Form("0"), csrf: str = Form("")):
        admin_id = request.session.get("admin_id")
        if not valid_csrf(request, csrf, "mentor_profile_csrf"):
            request.session["mentor_profile_notice"] = {"type": "error", "message": "操作を確認できませんでした。"}
            return RedirectResponse("/admin/mentor-profile", 303)
        name = display_name.strip()
        published = 1 if is_published == "1" else 0
        if not name or len(name) > 100:
            request.session["mentor_profile_notice"] = {"type": "error", "message": "メンター名は1〜100文字で入力してください。"}
            return RedirectResponse("/admin/mentor-profile", 303)
        now = datetime.datetime.now(JST).isoformat()
        with closing(sqlite3.connect(database_path)) as db:
            db.execute("""INSERT INTO mentor_profile(admin_id,is_published,display_name,created_at,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(admin_id) DO UPDATE SET
                is_published=excluded.is_published,display_name=excluded.display_name,updated_at=excluded.updated_at""",
                (admin_id, published, name, now, now))
            db.commit()
        request.session["mentor_profile_csrf"] = secrets.token_urlsafe(32)
        request.session["mentor_profile_notice"] = {"type": "success", "message": "大学生メンター情報を保存しました。"}
        return RedirectResponse("/admin/mentor-profile", 303)

    def schedule_rows(db, admin_id, day):
        existing = {row[0]: (bool(row[1]), bool(row[2])) for row in db.execute(
            "SELECT start_time,online_available,offline_available FROM mentor_available_slot WHERE admin_id=? AND day=?",
            (admin_id, day))}
        return [{"start_time": minutes_to_time(value), "end_time": minutes_to_time(value + 30),
                 "online_available": existing.get(minutes_to_time(value), (False, False))[0],
                 "offline_available": existing.get(minutes_to_time(value), (False, False))[1]}
                for value in range(OPEN_MINUTES, CLOSE_MINUTES, 30)]

    @router.get("/admin/mentor-schedule", response_class=HTMLResponse)
    async def admin_mentor_schedule(request: Request, day: str = None):
        tomorrow = datetime.datetime.now(JST).date() + datetime.timedelta(days=1)
        try:
            selected = datetime.date.fromisoformat(day) if day else tomorrow
        except ValueError:
            selected = tomorrow
        if selected < tomorrow:
            selected = tomorrow
        admin_id = request.session.get("admin_id")
        with closing(sqlite3.connect(database_path)) as db:
            slots = schedule_rows(db, admin_id, selected.isoformat())
        return templates.TemplateResponse(request=request, name="admin/mentor_schedule.html", context={
            "request": request, "admin_id": admin_id, "tomorrow": tomorrow.isoformat(),
            "selected_day": selected.isoformat(), "slots": slots,
            "csrf_token": csrf_token(request, "mentor_schedule_csrf"),
            "notice": request.session.pop("mentor_schedule_notice", None),
        })

    @router.get("/admin/mentor-schedule/availability")
    async def admin_mentor_schedule_availability(request: Request, day: str):
        validate_range(day, "09:00", "09:30")
        with closing(sqlite3.connect(database_path)) as db:
            return {"day": day, "slots": schedule_rows(db, request.session.get("admin_id"), day)}

    @router.post("/admin/mentor-schedule")
    async def update_admin_mentor_schedule(request: Request, day: str = Form(...), start_time: str = Form(...),
                                           end_time: str = Form(...), online: str = Form("0"),
                                           offline: str = Form("0"), action: str = Form(...), csrf: str = Form("")):
        return_url = "/admin/mentor-schedule?" + urlencode({"day": day})
        notice_key = "mentor_schedule_notice"
        if not valid_csrf(request, csrf, "mentor_schedule_csrf"):
            request.session[notice_key] = {"type": "error", "message": "操作を確認できませんでした。"}
            return RedirectResponse(return_url, 303)
        try:
            validate_range(day, start_time, end_time)
            if action not in {"add", "remove"}:
                raise ValueError("操作が正しくありません。")
            selected_online, selected_offline = online == "1", offline == "1"
            if not (selected_online or selected_offline):
                raise ValueError("オンラインまたはオフラインを1つ以上選択してください。")
            admin_id = request.session.get("admin_id")
            times = slot_range(start_time, end_time)
            now = datetime.datetime.now(JST).isoformat()
            with closing(sqlite3.connect(database_path, timeout=10)) as db:
                db.execute("BEGIN IMMEDIATE")
                for slot in times:
                    row = db.execute("SELECT online_available,offline_available FROM mentor_available_slot WHERE admin_id=? AND day=? AND start_time=?",
                                     (admin_id, day, slot)).fetchone()
                    old_on, old_off = (row if row else (0, 0))
                    new_on = old_on or selected_online if action == "add" else old_on and not selected_online
                    new_off = old_off or selected_offline if action == "add" else old_off and not selected_offline
                    if action == "remove":
                        reservations = db.execute("""SELECT meeting_type FROM mentor_reservation
                            WHERE mentor_admin_id=? AND day=? AND status='active' AND start_time < ? AND end_time > ?""",
                            (admin_id, day, minutes_to_time(time_to_minutes(slot)+30), slot)).fetchall()
                        if any((r[0] == "online" and not new_on) or (r[0] == "offline" and not new_off) for r in reservations):
                            raise ValueError(BOOKING_CONFLICT_MESSAGE)
                    if new_on or new_off:
                        db.execute("""INSERT INTO mentor_available_slot(admin_id,day,start_time,online_available,offline_available,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?) ON CONFLICT(admin_id,day,start_time) DO UPDATE SET
                            online_available=excluded.online_available,offline_available=excluded.offline_available,updated_at=excluded.updated_at""",
                            (admin_id, day, slot, int(new_on), int(new_off), now, now))
                    else:
                        db.execute("DELETE FROM mentor_available_slot WHERE admin_id=? AND day=? AND start_time=?", (admin_id, day, slot))
                db.commit()
            request.session[notice_key] = {"type": "success", "message": "対応可能時間を更新しました。"}
            request.session["mentor_schedule_csrf"] = secrets.token_urlsafe(32)
        except (ValueError, sqlite3.Error) as error:
            request.session[notice_key] = {"type": "error", "message": str(error) or "更新できませんでした。"}
        return RedirectResponse(return_url, 303)

    @router.get("/mentor-reservation", response_class=HTMLResponse)
    async def mentor_list(request: Request):
        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            mentors = db.execute("SELECT admin_id,display_name FROM mentor_profile WHERE is_published=1 ORDER BY display_name,admin_id").fetchall()
        return templates.TemplateResponse(request=request, name="mentor/list.html", context={
            "request": request, "user_id": request.session.get("user_id"), "mentors": mentors})

    @router.get("/mentor-reservation/{admin_id}", response_class=HTMLResponse)
    async def mentor_booking_page(request: Request, admin_id: str):
        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            mentor = db.execute("SELECT admin_id,display_name FROM mentor_profile WHERE admin_id=? AND is_published=1", (admin_id,)).fetchone()
        if mentor is None:
            raise HTTPException(404, "メンターが見つかりません。")
        tomorrow = (datetime.datetime.now(JST).date() + datetime.timedelta(days=1)).isoformat()
        return templates.TemplateResponse(request=request, name="mentor/book.html", context={
            "request": request, "user_id": request.session.get("user_id"), "mentor": mentor, "tomorrow": tomorrow,
            "csrf_token": csrf_token(request, "mentor_booking_csrf"),
        })

    @router.get("/mentor-reservation/{admin_id}/availability")
    async def mentor_availability(request: Request, admin_id: str, day: str, meeting_type: str):
        validate_range(day, "09:00", "09:30")
        if meeting_type not in {"online", "offline"}:
            raise HTTPException(400, "利用形式が正しくありません。")
        column = "online_available" if meeting_type == "online" else "offline_available"
        with closing(sqlite3.connect(database_path)) as db:
            published = db.execute("SELECT 1 FROM mentor_profile WHERE admin_id=? AND is_published=1", (admin_id,)).fetchone()
            if not published:
                raise HTTPException(404, "メンターが見つかりません。")
            slots = {row[0] for row in db.execute(f"SELECT start_time FROM mentor_available_slot WHERE admin_id=? AND day=? AND {column}=1", (admin_id, day))}
            reservations = db.execute("SELECT start_time,end_time FROM mentor_reservation WHERE mentor_admin_id=? AND day=? AND status='active'", (admin_id, day)).fetchall()
            occupied = {slot for start, end in reservations for slot in slot_range(start, end)}
        return {"day": day, "slots": sorted(slots - occupied)}

    @router.post("/mentor-reservation/{admin_id}")
    async def create_mentor_reservation(request: Request, admin_id: str, day: str = Form(...),
                                        start_time: str = Form(...), end_time: str = Form(...),
                                        meeting_type: str = Form(...), consultation: str = Form(...), csrf: str = Form("")):
        if not valid_csrf(request, csrf, "mentor_booking_csrf"):
            return JSONResponse({"result": False, "message": "操作を確認できませんでした。"}, 403)
        try:
            validate_range(day, start_time, end_time, max_minutes=180)
            if time_to_minutes(end_time) - time_to_minutes(start_time) < 30 or meeting_type not in {"online", "offline"}:
                raise ValueError("30分以上3時間以内で利用形式を選択してください。")
            content = consultation.strip()
            if not content or len(content) > 2000:
                raise ValueError("相談内容は1〜2000文字で入力してください。")
            student_id = request.session.get("user_id")
            required = slot_range(start_time, end_time)
            column = "online_available" if meeting_type == "online" else "offline_available"
            with closing(sqlite3.connect(database_path, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN IMMEDIATE")
                mentor = db.execute("SELECT display_name FROM mentor_profile WHERE admin_id=? AND is_published=1", (admin_id,)).fetchone()
                if mentor is None:
                    raise ValueError("このメンターは現在予約できません。")
                available = {row[0] for row in db.execute(f"SELECT start_time FROM mentor_available_slot WHERE admin_id=? AND day=? AND {column}=1", (admin_id, day))}
                if any(slot not in available for slot in required):
                    raise ValueError("選択した時間帯は現在予約できません。")
                overlap = db.execute("""SELECT 1 FROM mentor_reservation WHERE day=? AND status='active'
                    AND start_time < ? AND end_time > ? AND (mentor_admin_id=? OR student_id=?) LIMIT 1""",
                    (day, end_time, start_time, admin_id, student_id)).fetchone()
                if overlap:
                    raise ValueError("選択した時間帯には別の予約があります。")
                now = datetime.datetime.now(JST).isoformat()
                reservation_id = db.execute("""INSERT INTO mentor_reservation
                    (student_id,mentor_admin_id,day,start_time,end_time,meeting_type,consultation,status,created_at)
                    VALUES(?,?,?,?,?,?,?,'active',?)""",
                    (student_id, admin_id, day, start_time, end_time, meeting_type, content, now)).lastrowid
                payload = {"mentor_name": mentor[0], "day": day, "start_time": start_time, "end_time": end_time,
                           "meeting_type": meeting_type, "consultation": content}
                create_notification(db, student_id, "大学生メンター予約を受け付けました",
                                    reservation_body("mentor", payload), "reservation_created", "mentor", reservation_id)
                db.commit()
            return {"result": True, "message": "予約が完了しました。マイページで確認できます。"}
        except (ValueError, sqlite3.Error) as error:
            return JSONResponse({"result": False, "message": str(error) or "予約できませんでした。"}, 409)

    @router.post("/mypage/mentor-reservation/{reservation_id}/cancel")
    async def student_cancel_mentor(request: Request, reservation_id: int, csrf: str = Form("")):
        if not valid_csrf(request, csrf, "mypage_mentor_csrf"):
            request.session["mypage_mentor_notice"] = {"type": "error", "message": "操作を確認できませんでした。"}
            return RedirectResponse("/mypage", 303)
        student_id = request.session.get("user_id")
        with closing(sqlite3.connect(database_path)) as db:
            changed = db.execute("""UPDATE mentor_reservation SET status='cancelled',cancelled_at=?,cancelled_by_type='student',cancelled_by_id=?
                WHERE id=? AND student_id=? AND status='active'""", (datetime.datetime.now(JST).isoformat(), student_id, reservation_id, student_id)).rowcount
            db.commit()
        request.session["mypage_mentor_notice"] = {"type": "success" if changed else "error", "message": "予約をキャンセルしました。" if changed else "有効な予約が見つかりませんでした。"}
        request.session["mypage_mentor_csrf"] = secrets.token_urlsafe(32)
        return RedirectResponse("/mypage", 303)

    @router.get("/admin/mentor-reservations", response_class=HTMLResponse)
    async def admin_mentor_reservations(request: Request, scope: str = "all"):
        admin_id = request.session.get("admin_id")
        params = () if scope != "mine" else (admin_id,)
        where = "" if scope != "mine" else "WHERE r.mentor_admin_id=?"
        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(f"""SELECT r.*,COALESCE(NULLIF(p.display_name,''),r.mentor_admin_id) mentor_name,
                COALESCE(s.school,'') student_school FROM mentor_reservation r
                LEFT JOIN mentor_profile p ON p.admin_id=r.mentor_admin_id LEFT JOIN student s ON s.id=r.student_id
                {where} ORDER BY CASE WHEN r.status='active' AND r.day>=date('now','+9 hours') THEN 0 ELSE 1 END,r.day,r.start_time,r.id""", params).fetchall()
        return templates.TemplateResponse(request=request, name="admin/mentor_reservations.html", context={
            "request": request, "admin_id": admin_id, "reservations": rows, "scope": scope,
            "csrf_token": csrf_token(request, "admin_mentor_reservation_csrf"),
            "notice": request.session.pop("admin_mentor_reservation_notice", None)})

    @router.post("/admin/mentor-reservations/{reservation_id}/cancel")
    async def admin_cancel_mentor(request: Request, reservation_id: int, csrf: str = Form(""), scope: str = Form("all")):
        redirect = "/admin/mentor-reservations?" + urlencode({"scope": scope})
        if not valid_csrf(request, csrf, "admin_mentor_reservation_csrf"):
            request.session["admin_mentor_reservation_notice"] = {"type": "error", "message": "操作を確認できませんでした。"}
            return RedirectResponse(redirect, 303)
        with closing(sqlite3.connect(database_path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT r.*,COALESCE(NULLIF(p.display_name,''),r.mentor_admin_id) mentor_name
                FROM mentor_reservation r LEFT JOIN mentor_profile p ON p.admin_id=r.mentor_admin_id
                WHERE r.id=? AND r.status='active'""", (reservation_id,)).fetchone()
            changed = 0
            if row:
                changed = db.execute("""UPDATE mentor_reservation SET status='cancelled',cancelled_at=?,cancelled_by_type='admin',cancelled_by_id=? WHERE id=? AND status='active'""",
                                     (datetime.datetime.now(JST).isoformat(), request.session.get("admin_id"), reservation_id)).rowcount
                if changed:
                    create_notification(db, row["student_id"], "大学生メンター予約がキャンセルされました",
                                        reservation_body("mentor", row), "reservation_cancelled", "mentor", reservation_id)
            db.commit()
        request.session["admin_mentor_reservation_notice"] = {"type": "success" if changed else "error", "message": "予約をキャンセルしました。" if changed else "有効な予約が見つかりませんでした。"}
        request.session["admin_mentor_reservation_csrf"] = secrets.token_urlsafe(32)
        return RedirectResponse(redirect, 303)

    return router
