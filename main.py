from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import closing
from pathlib import Path

import sqlite3, shutil, bcrypt, datetime, csv, secrets
from urllib.parse import urlencode

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "database.db"
UPLOADS_DIR = (BASE_DIR / "uploads").resolve()
JST = datetime.timezone(datetime.timedelta(hours=9))

app = FastAPI()

conn = sqlite3.connect("database/database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS study (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        introduce TEXT NOT NULL,
        filename TEXT NOT NULL,
        pdfpath TEXT NOT NULL,
        userid TEXT NOT NULL,
        time TEXT NOT NULL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS student (
        id TEXT NOT NULL,
        pwd TEXT NOT NULL,
        school TEXT NOT NULL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS teacher (
        id TEXT NOT NULL,
        pwd TEXT NOT NULL,
        school TEXT NOT NULL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin (
        id TEXT PRIMARY KEY NOT NULL,
        pwd TEXT NOT NULL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS reservation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userid TEXT NOT NULL,
        day TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        purpose TEXT NOT NULL
    )
""")
reservation_columns = {
    row[1] for row in cursor.execute("PRAGMA table_info(reservation)").fetchall()
}
if "equipment" in reservation_columns:
    conn.commit()
    with conn:
        original_count = conn.execute("SELECT COUNT(*) FROM reservation").fetchone()[0]
        conn.execute("DROP TABLE IF EXISTS reservation_without_equipment")
        conn.execute("""
            CREATE TABLE reservation_without_equipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userid TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                purpose TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO reservation_without_equipment
                (id, userid, day, start_time, end_time, purpose)
            SELECT id, userid, day, start_time, end_time, purpose
            FROM reservation
        """)
        migrated_count = conn.execute(
            "SELECT COUNT(*) FROM reservation_without_equipment"
        ).fetchone()[0]
        if migrated_count != original_count:
            raise RuntimeError("工作室予約データの移行件数が一致しません。")
        conn.execute("DROP TABLE reservation")
        conn.execute("ALTER TABLE reservation_without_equipment RENAME TO reservation")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS equipment_reservation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userid TEXT NOT NULL, equipment TEXT NOT NULL,
        start_day TEXT NOT NULL, end_day TEXT NOT NULL,
        quantity INTEGER NOT NULL, purpose TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        equipment_id TEXT,
        returned INTEGER NOT NULL DEFAULT 0
    )
""")
equipment_reservation_columns = {
    row[1] for row in cursor.execute("PRAGMA table_info(equipment_reservation)").fetchall()
}
if "equipment_id" not in equipment_reservation_columns:
    cursor.execute("ALTER TABLE equipment_reservation ADD COLUMN equipment_id TEXT")
if "returned" not in equipment_reservation_columns:
    cursor.execute(
        "ALTER TABLE equipment_reservation "
        "ADD COLUMN returned INTEGER NOT NULL DEFAULT 0"
    )
cursor.execute("""
    CREATE TABLE IF NOT EXISTS equipment_room_reservation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userid TEXT NOT NULL,
        equipment_id TEXT NOT NULL,
        equipment TEXT NOT NULL,
        use_day TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        purpose TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        returned INTEGER NOT NULL DEFAULT 0
    )
""")
equipment_room_reservation_columns = {
    row[1] for row in cursor.execute(
        "PRAGMA table_info(equipment_room_reservation)"
    ).fetchall()
}
if "returned" not in equipment_room_reservation_columns:
    cursor.execute(
        "ALTER TABLE equipment_room_reservation "
        "ADD COLUMN returned INTEGER NOT NULL DEFAULT 0"
    )
conn.commit()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

def load_equipment_catalog(catalog_path=None):
    catalog = []
    seen_ids = set()
    path = catalog_path or BASE_DIR / "csv" / "equipment-reservation.csv"
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                count = int(row.get("count") or "")
            except (TypeError, ValueError):
                continue
            equipment_id = (row.get("id") or "").strip()
            name = (row.get("name") or "").strip()
            usage_type = (row.get("usage_type") or "").strip()
            if (
                not equipment_id or not name or count <= 0 or
                usage_type not in {"takeout", "in_room"} or
                equipment_id in seen_ids
            ):
                continue
            seen_ids.add(equipment_id)
            catalog.append({
                "id": equipment_id,
                "name": name,
                "image": (row.get("image") or "").strip(),
                "content": (row.get("content") or "").strip(),
                "count": count,
                "usage_type": usage_type
            })
    return catalog


def load_schools():
    with open(BASE_DIR / "csv" / "school.csv", "r", encoding="utf-8", newline="") as f:
        return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]

def find_equipment(catalog, equipment_id=None, equipment_name=None):
    if equipment_id:
        return next((item for item in catalog if item["id"] == equipment_id), None)
    if equipment_name:
        return next((item for item in catalog if item["name"] == equipment_name), None)
    return None


def date_range(start_day, end_day):
    current = start_day
    while current <= end_day:
        yield current
        current += datetime.timedelta(days=1)


def takeout_availability(item, start_day, end_day, db=None):
    database = db or conn
    rows = database.execute(
        """
        SELECT start_day, end_day, quantity
        FROM equipment_reservation
        WHERE (equipment_id = ? OR ((equipment_id IS NULL OR equipment_id = '') AND equipment = ?))
          AND start_day <= ? AND end_day >= ?
        """,
        (item["id"], item["name"], end_day.isoformat(), start_day.isoformat())
    ).fetchall()
    reserved_by_day = {day.isoformat(): 0 for day in date_range(start_day, end_day)}
    for reserved_start, reserved_end, quantity in rows:
        overlap_start = max(start_day, datetime.date.fromisoformat(reserved_start))
        overlap_end = min(end_day, datetime.date.fromisoformat(reserved_end))
        for day in date_range(overlap_start, overlap_end):
            reserved_by_day[day.isoformat()] += quantity
    peak_reserved = max(reserved_by_day.values(), default=0)
    return {
        **item,
        "reserved": peak_reserved,
        "available": max(item["count"] - peak_reserved, 0),
        "reserved_by_day": reserved_by_day
    }


def room_slot_availability(item, use_day, db=None):
    database = db or conn
    rows = database.execute(
        """
        SELECT start_time, end_time, quantity
        FROM equipment_room_reservation
        WHERE equipment_id = ? AND use_day = ?
        """,
        (item["id"], use_day.isoformat())
    ).fetchall()
    now = datetime.datetime.now(JST)
    slots = []
    for total_minutes in range(9 * 60, 20 * 60 + 1, 30):
        start_time = f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
        end_minutes = total_minutes + 30
        end_time = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
        reserved = sum(
            quantity for reserved_start, reserved_end, quantity in rows
            if reserved_start < end_time and reserved_end > start_time
        )
        slot_start = datetime.datetime.combine(
            use_day,
            datetime.time(total_minutes // 60, total_minutes % 60),
            tzinfo=JST
        )
        closed = use_day == now.date() and slot_start < now
        slots.append({
            "start_time": start_time,
            "end_time": end_time,
            "reserved_quantity": reserved,
            "available_quantity": max(item["count"] - reserved, 0),
            "closed": closed
        })
    return slots


def equipment_availability(equipment, start_day, end_day, catalog):
    """Compatibility wrapper for the original name-based takeout lookup."""
    item = find_equipment(catalog, equipment_name=equipment)
    if item is None:
        return None
    return takeout_availability(
        item,
        datetime.date.fromisoformat(start_day),
        datetime.date.fromisoformat(end_day)
    )


#classの定義
#httpが呼び出されたとき最初に実行
class LoginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        #loginしてから一日以上たったらログアウト
        if request.session.get("teacher_login") == True:
            login_time = datetime.datetime.strptime(
                request.session.get("teacher_time"),
                "%Y-%m-%d %H:%M:%S"
            )
            if (datetime.datetime.now() - login_time).days >= 1:
                request.session.pop("teacher_login", None)
                request.session.pop("teacher_id", None)
                request.session.pop("teacher_time", None)
                print("teacherログアウト")
        
        if request.session.get("user_login") == True:
            login_time = datetime.datetime.strptime(
                request.session.get("user_time"),
                "%Y-%m-%d %H:%M:%S"
            )
            if (datetime.datetime.now() - login_time).days >= 1:
                request.session.pop("user_login", None)
                request.session.pop("user_id", None)
                request.session.pop("user_time", None)
                print("userログアウト")

        if request.session.get("admin_login") == True:
            login_time = datetime.datetime.strptime(
                request.session.get("admin_time"),
                "%Y-%m-%d %H:%M:%S"
            )
            if (datetime.datetime.now() - login_time).days >= 1:
                request.session.pop("admin_login", None)
                request.session.pop("admin_id", None)
                request.session.pop("admin_time", None)
                print("adminログアウト")

        #ログインが必要なページにアクセスした場合、ログインページにリダイレクト
        #ログインなしでもアクセスできるページを定義
        public_paths = [
            "/login",
            "/registration",
            "/session",
            "/logout",
            "/admin/login"
        ]

        # 静的ファイルなどはそのまま通す
        if (
            request.url.path.startswith("/static") or
            request.url.path.startswith("/uploads")
        ):
            return await call_next(request)

        # public_paths は誰でもアクセス可能
        if request.url.path in public_paths:
            return await call_next(request)

        # admin 配下は admin 用セッションだけを許可する
        if request.url.path.startswith("/admin"):
            if request.session.get("admin_login") == True:
                return await call_next(request)
            return RedirectResponse("/admin/login", status_code=303)

        # teacher ログイン済みなら teacher 配下のみ許可
        if request.session.get("teacher_login") == True:
            if request.url.path.startswith("/teacher"):
                return await call_next(request)
            else:
                return RedirectResponse("/teacher", status_code=303)

        # 一般ユーザーログイン済みなら teacher 配下以外を許可
        if request.session.get("user_login") == True:
            if not request.url.path.startswith("/teacher"):
                return await call_next(request)
            else:
                return RedirectResponse("/login", status_code=303)

        # 未ログインはログインページへ
        return RedirectResponse("/login", status_code=303)

app.add_middleware(LoginCheckMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key="TEKNE"
)


#get関数
@app.get("/", response_class = HTMLResponse)
async def Home(request: Request):
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def Dashboard(request: Request):
    user_id = request.session.get("user_id")
    now = datetime.datetime.now(JST)
    today = now.date().isoformat()
    current_time = now.strftime("%H:%M")

    cursor.execute(
        """
        SELECT day, start_time, end_time, purpose
        FROM reservation
        WHERE userid = ?
          AND (day > ? OR (day = ? AND end_time > ?))
        ORDER BY day ASC, start_time ASC, end_time ASC, id ASC
        LIMIT 1
        """,
        (user_id, today, today, current_time)
    )
    next_reservation = cursor.fetchone()
    cursor.execute(
        """
        SELECT equipment, start_day, end_day, quantity
        FROM equipment_reservation
        WHERE userid = ? AND end_day >= ?
        ORDER BY
            CASE WHEN start_day <= ? THEN 0 ELSE 1 END ASC,
            start_day ASC,
            end_day ASC,
            id ASC
        LIMIT 1
        """,
        (user_id, today, today)
    )
    next_equipment_reservation = cursor.fetchone()

    return templates.TemplateResponse(
        request = request,
        name = "dashboard.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": user_id,
            "next_reservation": next_reservation,
            "next_equipment_reservation": next_equipment_reservation
        }
    )

@app.get("/addform", response_class = HTMLResponse)
async def AddForm(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "addform.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
    )

@app.get("/studylist", response_class = HTMLResponse)
async def StudyList(request: Request):    
    cursor.execute("SELECT * FROM study")
    studies = cursor.fetchall()

    return templates.TemplateResponse(
        request = request,
        name = "studylist.html",
        context = {
            "request": request,
            "studies": studies,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
    )

@app.get("/uploads/{id}.pdf")
async def pdf(id: int):
    pdf_path = f"uploads/{id}.pdf"

    cursor.execute(
        "SELECT filename FROM study WHERE id = ?",
        (id,)
    )

    return FileResponse(
        path = pdf_path,
        media_type = "application/pdf"
    )

@app.get("/reservation", response_class = HTMLResponse)
async def ReservationPage(request: Request, day: str = None):
    today = datetime.datetime.now(JST).date()
    initial_day = ""
    if day:
        try:
            selected_day = datetime.date.fromisoformat(day)
        except ValueError:
            selected_day = None
        if selected_day is not None and selected_day >= today:
            initial_day = selected_day.isoformat()

    return templates.TemplateResponse(
        request = request,
        name = "reservation.html",
        context = {
            "request": request,
            "today": today.isoformat(),
            "initial_day": initial_day,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
    )


@app.get("/reservation/availability")
async def ReservationAvailability(day: str):
    try:
        target_date = datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="日付が正しくありません。")

    now = datetime.datetime.now(JST)
    if target_date < now.date():
        raise HTTPException(status_code=400, detail="過去の日付は選択できません。")

    cursor.execute(
        "SELECT start_time, end_time FROM reservation WHERE day = ?",
        (target_date.isoformat(),)
    )
    reserved_times = []
    for start_time, end_time in cursor.fetchall():
        start_total = sum(value * factor for value, factor in zip(map(int, start_time.split(":")), (60, 1)))
        end_total = sum(value * factor for value, factor in zip(map(int, end_time.split(":")), (60, 1)))
        while start_total < end_total:
            reserved_times.append(f"{start_total // 60:02d}:{start_total % 60:02d}")
            start_total += 30

    closed_times = []
    if target_date == now.date():
        for total_minutes in range(9 * 60, 21 * 60, 30):
            slot_time = datetime.datetime.combine(
                target_date,
                datetime.time(total_minutes // 60, total_minutes % 60),
                tzinfo=JST
            )
            if slot_time < now:
                closed_times.append(f"{total_minutes // 60:02d}:{total_minutes % 60:02d}")

    return {
        "day": target_date.isoformat(),
        "reserved_times": sorted(set(reserved_times)),
        "closed_times": closed_times
    }


@app.get("/reservation/{year}/{month}/{day}", response_class = HTMLResponse)
async def ReservationLegacyDate(request: Request, year: int, month: int, day: int):
    try:
        target_date = datetime.date(year, month, day)
    except ValueError:
        raise HTTPException(status_code = 404, detail = "Not Found")

    if target_date < datetime.datetime.now(JST).date():
        raise HTTPException(status_code = 404, detail = "Not Found")
    return RedirectResponse(
        f"/reservation?{urlencode({'day': target_date.isoformat()})}",
        status_code=303
    )

@app.get("/login")
async def Login(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "login.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
    )

@app.get("/admin/login", response_class=HTMLResponse)
async def AdminLoginPage(request: Request):
    if request.session.get("admin_login") == True:
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="admin/login.html",
        context={"request": request}
    )

@app.post("/admin/login")
async def AdminLogin(
    request: Request,
    id: str = Form(...),
    pwd: str = Form(...)
):
    cursor.execute("SELECT pwd FROM admin WHERE id = ?", (id,))
    admin = cursor.fetchone()

    if admin is None or not bcrypt.checkpw(pwd.encode(), admin[0].encode()):
        return templates.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={"request": request, "error": "ID またはパスワードが正しくありません。"},
            status_code=401
        )

    request.session["admin_login"] = True
    request.session["admin_id"] = id
    request.session["admin_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return RedirectResponse("/admin", status_code=303)

@app.get("/admin", response_class=HTMLResponse)
async def AdminHome(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin/index.html",
        context={"request": request, "admin_id": request.session.get("admin_id")}
    )

@app.get("/admin/logout")
async def AdminLogout(request: Request):
    request.session.pop("admin_login", None)
    request.session.pop("admin_id", None)
    request.session.pop("admin_time", None)
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin/reservation", response_class=HTMLResponse)
async def AdminReservationPage(request: Request, start_day: str = None, end_day: str = None):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    today = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).date().isoformat()

    if start_day and end_day:
        cursor.execute("""
            SELECT id, userid, day, start_time, end_time, purpose
            FROM reservation
            WHERE day >= ? AND day >= ? AND day <= ?
            ORDER BY day ASC, start_time ASC, id ASC
        """, (today, start_day, end_day))
    else:
        cursor.execute("""
            SELECT id, userid, day, start_time, end_time, purpose
            FROM reservation
            WHERE day >= ?
            ORDER BY day ASC, start_time ASC, id ASC
        """, (today,))
    reservations = cursor.fetchall()

    return templates.TemplateResponse(
        request=request,
        name="admin/reservation.html",
        context={
            "request": request,
            "admin_id": request.session.get("admin_id"),
            "reservations": reservations,
            "start_day": start_day,
            "end_day": end_day
        }
    )


@app.get("/admin/equipment-reservation", response_class=HTMLResponse)
async def AdminEquipmentReservationPage(
    request: Request,
    start_day: str = None,
    end_day: str = None,
    unreturned_only: str = None
):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    show_unreturned_only = unreturned_only == "1"

    takeout_conditions = []
    takeout_params = []
    if start_day and end_day:
        takeout_conditions.append("start_day <= ? AND end_day >= ?")
        takeout_params.extend((end_day, start_day))
    if show_unreturned_only:
        takeout_conditions.append("returned = 0")
    takeout_where = (
        f"WHERE {' AND '.join(takeout_conditions)}" if takeout_conditions else ""
    )
    cursor.execute(f"""
        SELECT id, userid, equipment, start_day, end_day, quantity, purpose, note, returned
        FROM equipment_reservation
        {takeout_where}
        ORDER BY start_day ASC, id ASC
    """, takeout_params)
    equipment_reservations = cursor.fetchall()

    room_conditions = []
    room_params = []
    if start_day and end_day:
        room_conditions.append("use_day >= ? AND use_day <= ?")
        room_params.extend((start_day, end_day))
    if show_unreturned_only:
        room_conditions.append("returned = 0")
    room_where = f"WHERE {' AND '.join(room_conditions)}" if room_conditions else ""
    cursor.execute(f"""
        SELECT id, userid, equipment, use_day, start_time, end_time, quantity, purpose, note, returned
        FROM equipment_room_reservation
        {room_where}
        ORDER BY use_day ASC, start_time ASC, id ASC
    """, room_params)
    equipment_room_reservations = cursor.fetchall()

    csrf_token = request.session.get("equipment_reservation_csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        request.session["equipment_reservation_csrf_token"] = csrf_token
    notice = request.session.pop("equipment_reservation_notice", None)

    return templates.TemplateResponse(
        request=request,
        name="admin/equipment_reservation.html",
        context={
            "request": request,
            "admin_id": request.session.get("admin_id"),
            "equipment_reservations": equipment_reservations,
            "equipment_room_reservations": equipment_room_reservations,
            "start_day": start_day,
            "end_day": end_day,
            "unreturned_only": show_unreturned_only,
            "csrf_token": csrf_token,
            "notice": notice
        }
    )


@app.post("/admin/equipment-reservation/cancel")
async def AdminCancelEquipmentReservation(
    request: Request,
    reservation_type: str = Form(...),
    reservation_id: int = Form(...),
    csrf_token: str = Form(...)
):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    session_token = request.session.get("equipment_reservation_csrf_token", "")
    if not session_token or not secrets.compare_digest(csrf_token, session_token):
        request.session["equipment_reservation_notice"] = {
            "type": "error",
            "message": "操作を確認できませんでした。ページを再読み込みして、もう一度お試しください。"
        }
        return RedirectResponse("/admin/equipment-reservation", status_code=303)

    table_by_type = {
        "takeout": "equipment_reservation",
        "in_room": "equipment_room_reservation"
    }
    table = table_by_type.get(reservation_type)
    if table is None:
        request.session["equipment_reservation_notice"] = {
            "type": "error",
            "message": "予約種別が正しくありません。"
        }
        return RedirectResponse("/admin/equipment-reservation", status_code=303)

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        deleted_count = db.execute(
            f"DELETE FROM {table} WHERE id = ?", (reservation_id,)
        ).rowcount
        db.commit()

    if deleted_count != 1:
        request.session["equipment_reservation_notice"] = {
            "type": "error",
            "message": "取消対象の予約が見つかりませんでした。"
        }
    else:
        request.session["equipment_reservation_notice"] = {
            "type": "success",
            "message": "実験器具の予約を取り消しました。"
        }
        request.session["equipment_reservation_csrf_token"] = secrets.token_urlsafe(32)

    return RedirectResponse("/admin/equipment-reservation", status_code=303)


@app.post("/admin/equipment-reservation/return-status")
async def AdminUpdateEquipmentReturnStatus(
    request: Request,
    reservation_type: str = Form(...),
    reservation_id: int = Form(...),
    returned: int = Form(...),
    csrf_token: str = Form(...)
):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    session_token = request.session.get("equipment_reservation_csrf_token", "")
    if not session_token or not secrets.compare_digest(csrf_token, session_token):
        request.session["equipment_reservation_notice"] = {
            "type": "error",
            "message": "操作を確認できませんでした。ページを再読み込みして、もう一度お試しください。"
        }
        return RedirectResponse("/admin/equipment-reservation", status_code=303)

    table_by_type = {
        "takeout": "equipment_reservation",
        "in_room": "equipment_room_reservation"
    }
    table = table_by_type.get(reservation_type)
    if table is None or returned not in (0, 1):
        request.session["equipment_reservation_notice"] = {
            "type": "error",
            "message": "返却状態の指定が正しくありません。"
        }
        return RedirectResponse("/admin/equipment-reservation", status_code=303)

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        updated_count = db.execute(
            f"UPDATE {table} SET returned = ? WHERE id = ?",
            (returned, reservation_id)
        ).rowcount
        db.commit()

    if updated_count != 1:
        request.session["equipment_reservation_notice"] = {
            "type": "error",
            "message": "更新対象の予約が見つかりませんでした。"
        }
    else:
        request.session["equipment_reservation_notice"] = {
            "type": "success",
            "message": "返却状態を更新しました。"
        }
        request.session["equipment_reservation_csrf_token"] = secrets.token_urlsafe(32)

    return RedirectResponse("/admin/equipment-reservation", status_code=303)


@app.get("/admin/studies", response_class=HTMLResponse)
async def AdminStudiesPage(request: Request):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        studies = db.execute("""
            SELECT
                study.id,
                study.name,
                study.introduce,
                study.filename,
                study.pdfpath,
                study.userid,
                study.time,
                student.id,
                student.school
            FROM study
            LEFT JOIN student ON study.userid = student.id
            ORDER BY study.id ASC
        """).fetchall()

    return templates.TemplateResponse(
        request=request,
        name="admin/studies.html",
        context={
            "request": request,
            "admin_id": request.session.get("admin_id"),
            "studies": studies
        }
    )


@app.post("/admin/studies/{study_id}/delete")
async def AdminDeleteStudy(request: Request, study_id: int):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        try:
            study = db.execute(
                "SELECT pdfpath FROM study WHERE id = ?",
                (study_id,)
            ).fetchone()

            if study is None:
                return RedirectResponse("/admin/studies", status_code=303)

            db.execute("DELETE FROM study WHERE id = ?", (study_id,))
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise

    pdfpath = study[0]
    if pdfpath:
        target_path = (UPLOADS_DIR / pdfpath).resolve()
        try:
            target_path.relative_to(UPLOADS_DIR)
        except ValueError:
            pass
        else:
            try:
                target_path.unlink(missing_ok=True)
            except OSError:
                # DB deletion has completed; leave an undeleted file for manual cleanup.
                pass

    return RedirectResponse("/admin/studies", status_code=303)


@app.get("/admin/accounts", response_class=HTMLResponse)
async def AdminAccountsPage(request: Request, type: str = "student", school: str = None):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    account_type = type if type in ("student", "teacher") else "student"
    schools = load_schools()
    selected_school = school if school in schools else None
    table_name = "student" if account_type == "student" else "teacher"

    query = f"SELECT rowid, id, school FROM {table_name}"
    params = ()
    if selected_school:
        query += " WHERE school = ?"
        params = (selected_school,)
    query += " ORDER BY id ASC, rowid ASC"

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        accounts = db.execute(query, params).fetchall()

    csrf_token = request.session.get("account_csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        request.session["account_csrf_token"] = csrf_token

    school_query = urlencode({"school": selected_school}) if selected_school else ""

    return templates.TemplateResponse(
        request=request,
        name="admin/accounts.html",
        context={
            "request": request,
            "admin_id": request.session.get("admin_id"),
            "account_type": account_type,
            "accounts": accounts,
            "schools": schools,
            "selected_school": selected_school,
            "school_query": school_query,
            "csrf_token": csrf_token,
            "notice": request.session.pop("account_notice", None)
        }
    )


@app.post("/admin/accounts/delete")
async def AdminDeleteAccount(
    request: Request,
    account_type: str = Form(...),
    account_rowid: int = Form(...),
    account_id: str = Form(...),
    admin_id: str = Form(...),
    admin_password: str = Form(...),
    csrf_token: str = Form(...),
    return_school: str = Form("")
):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    safe_type = account_type if account_type in ("student", "teacher") else "student"
    schools = load_schools()
    safe_school = return_school if return_school in schools else None
    redirect_params = {"type": safe_type}
    if safe_school:
        redirect_params["school"] = safe_school
    redirect_url = "/admin/accounts?" + urlencode(redirect_params)

    session_token = request.session.get("account_csrf_token", "")
    if not session_token or not secrets.compare_digest(csrf_token, session_token):
        request.session["account_notice"] = {
            "type": "error",
            "message": "セッションを確認できませんでした。もう一度お試しください。"
        }
        return RedirectResponse(redirect_url, status_code=303)

    if account_type not in ("student", "teacher"):
        request.session["account_notice"] = {
            "type": "error",
            "message": "削除対象の種類が正しくありません。"
        }
        return RedirectResponse(redirect_url, status_code=303)

    session_admin_id = request.session.get("admin_id")
    authenticated = False

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        try:
            admin = db.execute(
                "SELECT pwd FROM admin WHERE id = ?",
                (session_admin_id,)
            ).fetchone()

            if admin_id == session_admin_id and admin is not None:
                try:
                    authenticated = bcrypt.checkpw(
                        admin_password.encode(),
                        admin[0].encode()
                    )
                except ValueError:
                    authenticated = False

            if not authenticated:
                request.session["account_notice"] = {
                    "type": "error",
                    "message": "管理者IDまたはパスワードが正しくありません。"
                }
                return RedirectResponse(redirect_url, status_code=303)

            table_name = "student" if account_type == "student" else "teacher"
            target = db.execute(
                f"SELECT id FROM {table_name} WHERE rowid = ? AND id = ?",
                (account_rowid, account_id)
            ).fetchone()

            if target is None:
                request.session["account_notice"] = {
                    "type": "error",
                    "message": "削除対象のアカウントが見つかりませんでした。"
                }
                return RedirectResponse(redirect_url, status_code=303)

            db.execute(
                f"DELETE FROM {table_name} WHERE rowid = ? AND id = ?",
                (account_rowid, account_id)
            )
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise

    account_label = "生徒" if account_type == "student" else "先生"
    request.session["account_notice"] = {
        "type": "success",
        "message": f"{account_label}アカウント「{account_id}」を削除しました。"
    }
    request.session["account_csrf_token"] = secrets.token_urlsafe(32)
    return RedirectResponse(redirect_url, status_code=303)

@app.get("/equipment-reservation", response_class=HTMLResponse)
async def EquipmentReservationPage(request: Request):
    today = datetime.date.today().isoformat()
    return templates.TemplateResponse(
        request=request,
        name="equipment_reservation.html",
        context={
            "request": request,
            "catalog": load_equipment_catalog(),
            "selected_day": today,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
    )

@app.get("/registration")
async def Registration(request: Request):
    schools = load_schools()

    return templates.TemplateResponse(
        request = request,
        name = "registration.html",
        context = {
            "request": request,
            "schools": schools
        }
    )

@app.get("/logout")
async def Logout(request: Request):
    request.session.pop("user_login", None)
    request.session.pop("user_id", None)
    request.session.pop("user_time", None)
    request.session.pop("teacher_login", None)
    request.session.pop("teacher_id", None)
    request.session.pop("teacher_time", None)
    return templates.TemplateResponse(
        request = request,
        name = "logout.html"
    )

@app.get("/mypage")
async def Mypage(request: Request):
    user_id = request.session.get("user_id")

    cursor.execute(
        """
        SELECT *
        FROM student
        WHERE id = ?
        """,
        (user_id,)
    )

    user_school = cursor.fetchone()[2]

    #userの予約した情報を取得
    cursor.execute(
        """
        SELECT *
        FROM reservation
        WHERE userid = ? AND day >= ?
        """,
        (
            user_id,
            datetime.datetime.now(
                datetime.timezone(datetime.timedelta(hours=9))
            ).date().isoformat()
        )
    )

    reservations = [i[2:4] for i in cursor.fetchall()]
    reservations.sort()

    cursor.execute("""SELECT id, equipment, start_day, end_day, quantity, purpose, note
        FROM equipment_reservation WHERE userid = ? ORDER BY start_day, end_day, id""", (user_id,))
    equipment_reservations = [
        {
            "usage_type": "持ち出し",
            "equipment": row[1],
            "usage_date": f"{row[2]} ～ {row[3]}",
            "usage_time": "-",
            "quantity": row[4],
            "purpose": row[5],
            "note": row[6] or "-",
            "sort_key": (row[2], "", 0, row[0]),
            "cancel_url": f"/mypage/equipment-reservation/{row[0]}/cancel",
            "cancel_method": "get"
        }
        for row in cursor.fetchall()
    ]

    cursor.execute("""SELECT id, equipment, use_day, start_time, end_time, quantity, purpose, note
        FROM equipment_room_reservation
        WHERE userid = ?
        ORDER BY use_day, start_time, end_time, id""", (
            user_id,
        ))
    equipment_room_reservations = [
        {
            "usage_type": "工作室内",
            "equipment": row[1],
            "usage_date": row[2],
            "usage_time": f"{row[3]} ～ {row[4]}",
            "quantity": row[5],
            "purpose": row[6],
            "note": row[7] or "-",
            "sort_key": (row[2], row[3], 1, row[0]),
            "cancel_url": f"/mypage/equipment-room-reservation/{row[0]}/cancel",
            "cancel_method": "post"
        }
        for row in cursor.fetchall()
    ]
    all_equipment_reservations = sorted(
        equipment_reservations + equipment_room_reservations,
        key=lambda reservation: reservation["sort_key"]
    )

    return templates.TemplateResponse(
        request = request,
        name = "mypage.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": user_id,
            "user_school": user_school,
            "reservations": reservations,
            "equipment_reservations": all_equipment_reservations
        }
    )

@app.get("/mypage/edit")
async def Edit(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "mypage/edit.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
    )

@app.get("/mypage/del_reservation/{day}_{time}")
async def DelReservation(request: Request, day: str, time: str):
    user_id = request.session.get("user_id")
    cursor.execute(
        """
        DELETE FROM reservation
        WHERE userid = ?
        AND day = ?
        AND start_time = ?
        """,
        (user_id, day, time)
    )
    conn.commit()
    return RedirectResponse("/mypage", status_code=303)

@app.post("/equipment-reservation")
async def CreateEquipmentReservation(
    request: Request,
    equipment_id: str = Form(""),
    equipment: str = Form(""),
    start_day: str = Form(...),
    end_day: str = Form(...),
    quantity: int = Form(...),
    purpose: str = Form(...),
    note: str = Form("")
):
    catalog = load_equipment_catalog()
    item = find_equipment(catalog, equipment_id=equipment_id, equipment_name=equipment)
    if item is None:
        raise HTTPException(status_code=400, detail="選択した器具は利用できません。")
    if item["usage_type"] != "takeout":
        raise HTTPException(status_code=400, detail="この器具は工作室内専用です。")
    try:
        start = datetime.date.fromisoformat(start_day)
        end = datetime.date.fromisoformat(end_day)
    except ValueError:
        raise HTTPException(status_code=400, detail="日付の形式が正しくありません。")
    if start < datetime.datetime.now(JST).date() or end < start:
        raise HTTPException(status_code=400, detail="利用日を正しく指定してください。")
    if (end - start).days + 1 > 7:
        raise HTTPException(status_code=400, detail="貸出期間は最長7日間です。")
    clean_purpose = purpose.strip()
    clean_note = note.strip()
    if quantity < 1 or not clean_purpose:
        raise HTTPException(status_code=400, detail="数量と使用目的を入力してください。")
    if len(clean_purpose) > 500 or len(clean_note) > 500:
        raise HTTPException(status_code=400, detail="使用目的と備考は500文字以内で入力してください。")

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            availability = takeout_availability(item, start, end, db)
            if quantity > availability["available"]:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"選択した期間は必要な数量を確保できません。利用可能数: {availability['available']}"
                )
            db.execute("""INSERT INTO equipment_reservation
                (userid, equipment, start_day, end_day, quantity, purpose, note, equipment_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                    request.session.get("user_id"), item["name"], start.isoformat(),
                    end.isoformat(), quantity, clean_purpose, clean_note, item["id"]
                ))
            db.commit()
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise
    return {"result": True}

@app.get("/mypage/equipment-reservation/{reservation_id}/cancel")
async def CancelEquipmentReservation(request: Request, reservation_id: int):
    cursor.execute("DELETE FROM equipment_reservation WHERE id = ? AND userid = ?", (reservation_id, request.session.get("user_id")))
    conn.commit()
    return RedirectResponse("/mypage", status_code=303)

@app.get("/equipment-availability")
async def EquipmentAvailability(start_day: str, end_day: str, equipment_id: str = "", equipment: str = ""):
    item = find_equipment(load_equipment_catalog(), equipment_id=equipment_id, equipment_name=equipment)
    if item is None:
        raise HTTPException(status_code=404, detail="器具が見つかりません。")
    if item["usage_type"] != "takeout":
        raise HTTPException(status_code=400, detail="この器具は工作室内専用です。")
    try:
        start = datetime.date.fromisoformat(start_day)
        end = datetime.date.fromisoformat(end_day)
    except ValueError:
        raise HTTPException(status_code=400, detail="日付の形式が正しくありません。")
    if start < datetime.datetime.now(JST).date() or end < start or (end - start).days + 1 > 7:
        raise HTTPException(status_code=400, detail="利用期間は最長7日間で指定してください。")
    availability = takeout_availability(item, start, end)
    return {"count": item["count"], "available": availability["available"]}


@app.get("/equipment-room-availability")
async def EquipmentRoomAvailability(equipment_id: str, day: str):
    item = find_equipment(load_equipment_catalog(), equipment_id=equipment_id)
    if item is None:
        raise HTTPException(status_code=404, detail="器具が見つかりません。")
    if item["usage_type"] != "in_room":
        raise HTTPException(status_code=400, detail="この器具は持ち出し用です。")
    try:
        use_day = datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="日付の形式が正しくありません。")
    if use_day < datetime.datetime.now(JST).date():
        raise HTTPException(status_code=400, detail="過去の日付は選択できません。")
    return {"count": item["count"], "day": day, "slots": room_slot_availability(item, use_day)}


@app.post("/equipment-room-reservation")
async def CreateEquipmentRoomReservation(
    request: Request,
    equipment_id: str = Form(...),
    use_day: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    quantity: int = Form(...),
    purpose: str = Form(...),
    note: str = Form("")
):
    if request.session.get("user_login") is not True or not request.session.get("user_id"):
        raise HTTPException(status_code=401, detail="ログインが必要です。")
    item = find_equipment(load_equipment_catalog(), equipment_id=equipment_id)
    if item is None:
        raise HTTPException(status_code=400, detail="選択した器具は利用できません。")
    if item["usage_type"] != "in_room":
        raise HTTPException(status_code=400, detail="この器具は持ち出し用です。")
    try:
        reservation_day = datetime.date.fromisoformat(use_day)
        parsed_start = datetime.datetime.strptime(start_time, "%H:%M").time()
        parsed_end = datetime.datetime.strptime(end_time, "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=400, detail="予約日時が正しくありません。")
    reservation_start = datetime.datetime.combine(reservation_day, parsed_start, tzinfo=JST)
    duration_minutes = (
        datetime.datetime.combine(reservation_day, parsed_end, tzinfo=JST) - reservation_start
    ).total_seconds() // 60
    if (
        reservation_start < datetime.datetime.now(JST) or
        start_time != parsed_start.strftime("%H:%M") or
        end_time != parsed_end.strftime("%H:%M") or
        parsed_start < datetime.time(9, 0) or parsed_end > datetime.time(20, 30) or
        duration_minutes < 30 or duration_minutes % 30 != 0 or
        parsed_start.minute not in (0, 30) or parsed_end.minute not in (0, 30)
    ):
        raise HTTPException(status_code=400, detail="この時間帯は予約できません。")
    clean_purpose = purpose.strip()
    clean_note = note.strip()
    if quantity < 1 or not clean_purpose:
        raise HTTPException(status_code=400, detail="数量と使用目的を入力してください。")
    if len(clean_purpose) > 500 or len(clean_note) > 500:
        raise HTTPException(status_code=400, detail="使用目的と備考は500文字以内で入力してください。")

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            slots = room_slot_availability(item, reservation_day, db)
            selected_slots = [
                slot for slot in slots
                if slot["start_time"] < end_time and slot["end_time"] > start_time
            ]
            if not selected_slots or any(slot["closed"] for slot in selected_slots):
                db.rollback()
                raise HTTPException(status_code=400, detail="この時間帯は予約できません。")
            available = min(slot["available_quantity"] for slot in selected_slots)
            if quantity > available:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"選択した時間帯は在庫が不足しています。利用可能数: {available}"
                )
            db.execute("""INSERT INTO equipment_room_reservation
                (userid, equipment_id, equipment, use_day, start_time, end_time, quantity, purpose, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    request.session.get("user_id"), item["id"], item["name"], reservation_day.isoformat(),
                    start_time, end_time, quantity, clean_purpose, clean_note
                ))
            db.commit()
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise
    return {"result": True}


@app.post("/mypage/equipment-room-reservation/{reservation_id}/cancel")
async def CancelEquipmentRoomReservation(request: Request, reservation_id: int):
    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        db.execute(
            "DELETE FROM equipment_room_reservation WHERE id = ? AND userid = ?",
            (reservation_id, request.session.get("user_id"))
        )
        db.commit()
    return RedirectResponse("/mypage", status_code=303)

#teacherページ
@app.get("/teacher")
async def teacher(request: Request):
    teacher_id = request.session.get("teacher_id")

    cursor.execute("""
    SELECT *
    FROM teacher
    WHERE id = ?
    """, (teacher_id,))

    teacher_school = cursor.fetchone()[2]

    return templates.TemplateResponse(
        request = request,
        name = "teacher.html",
        context = {
            "request": request,
            "teacher_login": request.session.get("teacher_login"),
            "teacher_id": teacher_id,
            "teacher_school": teacher_school
        }
    )

@app.get("/teacher/edit")
async def Edit(request: Request):
    teacher_id = request.session.get("teacher_id")

    cursor.execute("""
    SELECT school
    FROM teacher
    WHERE id = ?
    """, (teacher_id,))
    teacher = cursor.fetchone()

    return templates.TemplateResponse(
        request = request,
        name = "teacher/edit.html",
        context = {
            "request": request,
            "teacher_login": request.session.get("teacher_login"),
            "teacher_id": teacher_id,
            "teacher_school": teacher[0] if teacher else ""
        }
    )

@app.get("/teacher/studylist", response_class = HTMLResponse)
async def StudyList(request: Request):
    if request.session.get("teacher_login") != True:
        return RedirectResponse("/login", status_code=303)

    teacher_id = request.session.get("teacher_id")
    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        teacher = db.execute(
            "SELECT school FROM teacher WHERE id = ?",
            (teacher_id,)
        ).fetchone()

        studies = []
        if teacher is not None:
            studies = db.execute("""
                SELECT study.*
                FROM study
                WHERE EXISTS (
                    SELECT 1
                    FROM student
                    WHERE student.id = study.userid
                      AND student.school = ?
                )
                ORDER BY study.id ASC
            """, (teacher[0],)).fetchall()

    csrf_token = request.session.get("teacher_studylist_csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        request.session["teacher_studylist_csrf_token"] = csrf_token
    delete_succeeded = request.session.pop("teacher_studylist_delete_succeeded", False)

    return templates.TemplateResponse(
        request = request,
        name = "teacher/studylist.html",
        context = {
            "request": request,
            "studies": studies,
            "teacher_login": request.session.get("teacher_login"),
            "teacher_id": teacher_id,
            "csrf_token": csrf_token,
            "delete_succeeded": delete_succeeded
        }
    )


@app.post("/teacher/studylist/{study_id}/delete")
async def TeacherDeleteStudy(
    request: Request,
    study_id: int,
    csrf_token: str = Form("")
):
    redirect = RedirectResponse("/teacher/studylist", status_code=303)
    if request.session.get("teacher_login") != True:
        return RedirectResponse("/login", status_code=303)

    expected_token = request.session.get("teacher_studylist_csrf_token")
    if not expected_token or not secrets.compare_digest(expected_token, csrf_token):
        return redirect

    teacher_id = request.session.get("teacher_id")
    study = None
    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        try:
            teacher = db.execute(
                "SELECT school FROM teacher WHERE id = ?",
                (teacher_id,)
            ).fetchone()
            if teacher is None:
                return redirect

            study = db.execute("""
                SELECT study.pdfpath
                FROM study
                WHERE study.id = ?
                  AND EXISTS (
                      SELECT 1
                      FROM student
                      WHERE student.id = study.userid
                        AND student.school = ?
                  )
            """, (study_id, teacher[0])).fetchone()

            if study is None:
                return redirect

            deleted_count = db.execute("""
                DELETE FROM study
                WHERE id = ?
                  AND EXISTS (
                      SELECT 1
                      FROM student
                      WHERE student.id = study.userid
                        AND student.school = ?
                  )
            """, (study_id, teacher[0])).rowcount
            if deleted_count != 1:
                db.rollback()
                return redirect
            db.commit()
        except sqlite3.Error:
            db.rollback()
            return redirect

    request.session["teacher_studylist_csrf_token"] = secrets.token_urlsafe(32)
    pdfpath = study[0]
    if pdfpath:
        target_path = (UPLOADS_DIR / pdfpath).resolve()
        try:
            target_path.relative_to(UPLOADS_DIR)
        except ValueError:
            pass
        else:
            try:
                target_path.unlink(missing_ok=True)
            except OSError:
                pass

    request.session["teacher_studylist_delete_succeeded"] = True
    return redirect

#仮 request.sessionをfalseにする
@app.get("/reset")
async def Reset(request: Request):
    request.session["teacher_login"] = False
    request.session["user_login"] = False

#仮 sessionの中を確認
@app.get("/session")
async def Session(request: Request):
    return request.session


#post関数
@app.post("/login")
async def Login(
    request: Request,
    type: str = Form(...),
    id: str = Form(...),
    pwd: str = Form(...)
):
    if type == "student":
        #dbからチェック
        cursor.execute(
            "SELECT * FROM student WHERE id = ?",
            (id,)
        )
        
        #入力情報と照合
        hashed_pwd = cursor.fetchone()
        if hashed_pwd is None:
            return {"result": False}
        else:    
            pwdcheck = bcrypt.checkpw(
                pwd.encode(),
                hashed_pwd[1].encode()
            )

        if pwdcheck:
            request.session.pop("teacher_login", None)
            request.session.pop("teacher_id", None)
            request.session.pop("teacher_time", None)
            request.session["user_login"] = True
            request.session["user_id"] = id
            request.session["user_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return {"result": True}
        else:
            return {"result": False}
    
    elif type == "teacher":
        #dbからチェック
        cursor.execute(
            "SELECT * FROM teacher WHERE id = ?",
            (id,)
        )
        
        #入力情報と照合
        hashed_pwd = cursor.fetchone()
        if hashed_pwd is None:
            return {"result": False}
        else:    
            pwdcheck = bcrypt.checkpw(
                pwd.encode(),
                hashed_pwd[1].encode()
            )

        if pwdcheck:
            request.session.pop("user_login", None)
            request.session.pop("user_id", None)
            request.session.pop("user_time", None)
            request.session["teacher_login"] = True
            request.session["teacher_id"] = id
            request.session["teacher_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return {"result": True}
        else:
            return {"result": False}

@app.post("/registration")
async def Registration(
    type: str = Form(...),
    id: str = Form(...),
    pwd: str = Form(...),
    school: str = Form(...)
):
    #生徒用
    if type == "student":
        if (id.isascii() and
            len(id) > 7 and
            
            pwd.isascii() and
            len(pwd) > 7 and
            any(i.isalpha() for i in pwd) and
            any(i.isdigit() for i in pwd) and

            school != "notselect"
            ):
            #IDがかぶっていないかチェック
            cursor.execute(
                "SELECT * FROM student WHERE id = ?",
                (id,)
            )

            if cursor.fetchone() != None:
                return {"result": 2}
            else: 
                #dbにIDとパスワードに追加
                hashed_pwd = bcrypt.hashpw(
                    pwd.encode(),
                    bcrypt.gensalt()
                ).decode()
                cursor.execute(
                    """
                    INSERT INTO student (id, pwd, school)
                    VALUES (?, ?, ?)
                    """,
                    (id, hashed_pwd, school)
                )
                conn.commit()
                
                print("dbに情報を追加")

                return {"result": 0}
        else:
            return {"result": 1}
    #先生用
    elif type == "teacher":
        if (id.isascii() and
            len(id) > 7 and
            
            pwd.isascii() and
            len(pwd) > 7 and
            any(i.isalpha() for i in pwd) and
            any(i.isdigit() for i in pwd) and
    
            school != "notselect"
            ):
            #IDがかぶっていないかチェック
            cursor.execute(
                "SELECT * FROM teacher WHERE id = ?",
                (id,)
            )
    
            if cursor.fetchone() != None:
                return {"result": 2}
            else: 
                #dbにIDとパスワードに追加
                hashed_pwd = bcrypt.hashpw(
                    pwd.encode(),
                    bcrypt.gensalt()
                ).decode()
                cursor.execute(
                    """
                    INSERT INTO teacher (id, pwd, school)
                    VALUES (?, ?, ?)
                    """,
                    (id, hashed_pwd, school)
                )
                conn.commit()
                
                print("dbに情報を追加")
    
                return {"result": 0}
        else:
            return {"result": 1}

@app.post("/addform")
async def Add(
    request: Request,
    name: str = Form(...),
    introduce: str = Form(...),
    pdf: UploadFile = File(...)
):
    #dbに保存
    cursor.execute(
        """
        INSERT INTO study (name, introduce, filename, pdfpath, userid, time)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, introduce, pdf.filename, "", request.session.get("user_id"), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    id = cursor.lastrowid
    cursor.execute(
        "UPDATE study SET pdfpath = ? WHERE id = ?",
        (f"{id}.pdf", id)
    )
    conn.commit()

    #pdfファイルを保存
    pdf_path = f"uploads/{id}.pdf"
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(pdf.file, f)
    
    print("dbに情報を追加,ファイルを保存")

@app.post("/reservation/date")
async def ReservationDate(
    request: Request,
    day: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    purpose: str = Form(...)
):
    try:
        reservation_day = datetime.date.fromisoformat(day)
        parsed_start = datetime.datetime.strptime(start_time, "%H:%M").time()
        parsed_end = datetime.datetime.strptime(end_time, "%H:%M").time()
    except ValueError:
        return JSONResponse(
            {"result": False, "message": "予約日時が正しくありません。"},
            status_code=400
        )

    reservation_start = datetime.datetime.combine(reservation_day, parsed_start, tzinfo=JST)
    reservation_end = datetime.datetime.combine(reservation_day, parsed_end, tzinfo=JST)
    duration_minutes = int((reservation_end - reservation_start).total_seconds() // 60)
    if (
        reservation_start < datetime.datetime.now(JST) or
        duration_minutes <= 0 or
        duration_minutes > 180 or
        duration_minutes % 30 != 0 or
        parsed_start.minute not in (0, 30) or
        parsed_end.minute not in (0, 30) or
        parsed_start < datetime.time(9, 0) or
        parsed_end > datetime.time(21, 0)
    ):
        return JSONResponse(
            {"result": False, "message": "予約時間は当日以降の9:00〜21:00から、30分単位・3時間以内で選択してください。"},
            status_code=400
        )

    if not purpose.strip():
        return JSONResponse(
            {"result": False, "message": "使用目的を入力してください。"},
            status_code=400
        )

    with closing(sqlite3.connect(DATABASE_PATH)) as db:
        db.execute("BEGIN IMMEDIATE")
        overlap = db.execute(
            """
            SELECT 1 FROM reservation
            WHERE day = ? AND start_time < ? AND end_time > ?
            LIMIT 1
            """,
            (reservation_day.isoformat(), end_time, start_time)
        ).fetchone()
        if overlap is not None:
            db.rollback()
            return JSONResponse(
                {"result": False, "message": "選択した時間帯は、すでに予約されています。空き状況を更新しました。"},
                status_code=409
            )

        db.execute(
            """
            INSERT INTO reservation (userid, day, start_time, end_time, purpose)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                request.session.get("user_id"),
                reservation_day.isoformat(),
                start_time,
                end_time,
                purpose.strip()
            )
        )
        db.commit()

    return {"result": True, "message": "予約が完了しました。マイページで予約を確認できます。"}

@app.post("/mypage/edit/id")
async def Edit(
    request: Request,
    old_id: str = Form(...),
    old_pwd: str = Form(...),
    new_id: str= Form(...)
):
    #dbからチェック
    cursor.execute(
        "SELECT * FROM student WHERE id = ?",
        (old_id,)
    )
    
    #入力情報と照合
    hashed_pwd = cursor.fetchone()
    if hashed_pwd is None:
        return {"result": False}
    else:    
        pwdcheck = bcrypt.checkpw(
            old_pwd.encode(),
            hashed_pwd[1].encode()
        )

    if pwdcheck:
        #IDがかぶっていないかチェック
        cursor.execute(
            "SELECT * FROM student WHERE id = ?",
            (new_id,)
        )

        if cursor.fetchone() != None:
            return {"result": 1}
        else:
            if(
                new_id.isascii() and
                len(new_id) > 7
            ):
                cursor.execute("""
                UPDATE student
                SET id = ?
                WHERE id = ?
                """, (new_id, old_id))
                conn.commit()
                return {"result": 3}
            else:
                return {"result": 2}
    else:
        return {"result": 0}

@app.post("/mypage/edit/pwd")
async def Edit(
    request: Request,
    old_id: str = Form(...),
    old_pwd: str = Form(...),
    new_pwd: str= Form(...)
):
    #dbからチェック
    cursor.execute(
        "SELECT * FROM student WHERE id = ?",
        (old_id,)
    )
    
    #入力情報と照合
    hashed_pwd = cursor.fetchone()
    if hashed_pwd is None:
        return {"result": False}
    else:    
        pwdcheck = bcrypt.checkpw(
            old_pwd.encode(),
            hashed_pwd[1].encode()
        )

    if pwdcheck:
        if(
            new_pwd.isascii() and
            len(new_pwd) > 7 and
            any(i.isalpha() for i in new_pwd) and
            any(i.isdigit() for i in new_pwd)
        ):
            new_hashed_pwd = bcrypt.hashpw(
                new_pwd.encode(),
                bcrypt.gensalt()
            ).decode()
            cursor.execute("""
            UPDATE student
            SET pwd = ?
            WHERE id = ?
            """, (new_hashed_pwd, old_id))
            conn.commit()

            return {"result": 2}
        else:
            return {"result": 1}
    else:
        return {"result": 0}

@app.post("/teacher/edit/id")
async def Edit(
    request: Request,
    old_id: str = Form(...),
    old_pwd: str = Form(...),
    new_id: str= Form(...)
):
    #dbからチェック
    cursor.execute(
        "SELECT * FROM teacher WHERE id = ?",
        (old_id,)
    )
    
    #入力情報と照合
    hashed_pwd = cursor.fetchone()
    if hashed_pwd is None:
        return {"result": False}
    else:    
        pwdcheck = bcrypt.checkpw(
            old_pwd.encode(),
            hashed_pwd[1].encode()
        )

    if pwdcheck:
        #IDがかぶっていないかチェック
        cursor.execute(
            "SELECT * FROM teacher WHERE id = ?",
            (new_id,)
        )

        if cursor.fetchone() != None:
            return {"result": 1}
        else:
            if(
                new_id.isascii() and
                len(new_id) > 7
            ):
                cursor.execute("""
                UPDATE teacher
                SET id = ?
                WHERE id = ?
                """, (new_id, old_id))
                conn.commit()
                request.session["teacher_id"] = new_id
                return {"result": 3}
            else:
                return {"result": 2}
    else:
        return {"result": 0}

@app.post("/teacher/edit/pwd")
async def Edit(
    request: Request,
    old_id: str = Form(...),
    old_pwd: str = Form(...),
    new_pwd: str= Form(...)
):
    #dbからチェック
    cursor.execute(
        "SELECT * FROM teacher WHERE id = ?",
        (old_id,)
    )
    
    #入力情報と照合
    hashed_pwd = cursor.fetchone()
    if hashed_pwd is None:
        return {"result": False}
    else:    
        pwdcheck = bcrypt.checkpw(
            old_pwd.encode(),
            hashed_pwd[1].encode()
        )

    if pwdcheck:
        if(
            new_pwd.isascii() and
            len(new_pwd) > 7 and
            any(i.isalpha() for i in new_pwd) and
            any(i.isdigit() for i in new_pwd)
        ):
            new_hashed_pwd = bcrypt.hashpw(
                new_pwd.encode(),
                bcrypt.gensalt()
            ).decode()
            cursor.execute("""
            UPDATE teacher
            SET pwd = ?
            WHERE id = ?
            """, (new_hashed_pwd, old_id))
            conn.commit()

            return {"result": 2}
        else:
            return {"result": 1}
    else:
        return {"result": 0}
