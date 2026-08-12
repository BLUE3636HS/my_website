from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import closing
from pathlib import Path

import sqlite3, shutil, bcrypt, datetime, csv, json, secrets
from urllib.parse import urlencode

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "database.db"
UPLOADS_DIR = (BASE_DIR / "uploads").resolve()

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
        equipment TEXT NOT NULL,
        purpose TEXT NOT NULL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS equipment_reservation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userid TEXT NOT NULL, equipment TEXT NOT NULL,
        start_day TEXT NOT NULL, end_day TEXT NOT NULL,
        quantity INTEGER NOT NULL, purpose TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    )
""")
conn.commit()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

def load_equipment_catalog():
    catalog = []
    with open("csv/equipment-reservation.csv", "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                count = int(row.get("count", ""))
            except ValueError:
                continue
            if row.get("name") and count > 0:
                catalog.append({"name": row["name"].strip(), "image": row.get("image", "").strip(), "content": row.get("content", "").strip(), "count": count})
    return catalog


def load_schools():
    with open(BASE_DIR / "csv" / "school.csv", "r", encoding="utf-8", newline="") as f:
        return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]

def equipment_availability(equipment, start_day, end_day, catalog):
    item = next((item for item in catalog if item["name"] == equipment), None)
    if item is None:
        return None
    cursor.execute("""SELECT COALESCE(SUM(quantity), 0) FROM equipment_reservation
        WHERE equipment = ? AND start_day <= ? AND end_day >= ?""", (equipment, end_day, start_day))
    reserved = cursor.fetchone()[0]
    return {**item, "reserved": reserved, "available": max(item["count"] - reserved, 0)}


def format_reserved_equipment(value):
    try:
        equipment = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value

    if not isinstance(equipment, list):
        return value

    return ", ".join(str(item) for item in equipment)


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
    return templates.TemplateResponse(
        request = request,
        name = "home.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
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
async def Reservation(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "reservation.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
    )

@app.get("/reservation/{year}/{month}/{day}", response_class = HTMLResponse)
async def Reservation(request: Request, year: int, month: int, day: int):
    try:
        target_date = datetime.date(year, month, day)
    except ValueError:
        raise HTTPException(status_code = 404, detail = "Not Found")

    if target_date < datetime.date.today():
        raise HTTPException(status_code = 404, detail = "Not Found")

    cursor.execute("SELECT start_time, end_time FROM reservation WHERE day = ?", (target_date.isoformat(),))
    reserved_times = []
    for start_time, end_time in cursor.fetchall():
        start_total = sum(value * factor for value, factor in zip(map(int, start_time.split(":")), (60, 1)))
        end_total = sum(value * factor for value, factor in zip(map(int, end_time.split(":")), (60, 1)))
        while start_total < end_total:
            reserved_times.append(f"{start_total // 60:02d}:{start_total % 60:02d}")
            start_total += 30

    with open("csv/equipment.csv", "r", encoding="utf-8") as f:
        equipments = [row[0] for row in csv.reader(f) if row]

    return templates.TemplateResponse(
        request = request,
        name = "reservation/date.html",
        context = {
            "request": request,
            "year": year,
            "month": f"{month:02d}",
            "day": f"{day:02d}",
            "reserved_times": reserved_times,
            "equipments": equipments,
            "user_login": request.session.get("user_login"),
            "user_id": request.session.get("user_id")
        }
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
            SELECT id, userid, day, start_time, end_time, equipment, purpose
            FROM reservation
            WHERE day >= ? AND day >= ? AND day <= ?
            ORDER BY day ASC, start_time ASC, id ASC
        """, (today, start_day, end_day))
    else:
        cursor.execute("""
            SELECT id, userid, day, start_time, end_time, equipment, purpose
            FROM reservation
            WHERE day >= ?
            ORDER BY day ASC, start_time ASC, id ASC
        """, (today,))
    reservations = [
        (*reservation[:5], format_reserved_equipment(reservation[5]), *reservation[6:])
        for reservation in cursor.fetchall()
    ]

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
async def AdminEquipmentReservationPage(request: Request, start_day: str = None, end_day: str = None):
    if request.session.get("admin_login") != True:
        return RedirectResponse("/admin/login", status_code=303)

    today = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).date().isoformat()

    if start_day and end_day:
        cursor.execute("""
            SELECT id, userid, equipment, start_day, end_day, quantity, purpose, note
            FROM equipment_reservation
            WHERE end_day >= ? AND start_day <= ? AND end_day >= ?
            ORDER BY start_day ASC, id ASC
        """, (today, end_day, start_day))
    else:
        cursor.execute("""
            SELECT id, userid, equipment, start_day, end_day, quantity, purpose, note
            FROM equipment_reservation
            WHERE end_day >= ?
            ORDER BY start_day ASC, id ASC
        """, (today,))
    equipment_reservations = cursor.fetchall()

    return templates.TemplateResponse(
        request=request,
        name="admin/equipment_reservation.html",
        context={
            "request": request,
            "admin_id": request.session.get("admin_id"),
            "equipment_reservations": equipment_reservations,
            "start_day": start_day,
            "end_day": end_day
        }
    )


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
    equipment_reservations = cursor.fetchall()

    return templates.TemplateResponse(
        request = request,
        name = "mypage.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": user_id,
            "user_school": user_school,
            "reservations": reservations,
            "equipment_reservations": equipment_reservations
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
async def CreateEquipmentReservation(request: Request, equipment: str = Form(...), start_day: str = Form(...), end_day: str = Form(...), quantity: int = Form(...), purpose: str = Form(...), note: str = Form("")):
    try:
        start = datetime.date.fromisoformat(start_day)
        end = datetime.date.fromisoformat(end_day)
    except ValueError:
        raise HTTPException(status_code=400, detail="日付の形式が正しくありません。")
    if start < datetime.date.today() or end < start:
        raise HTTPException(status_code=400, detail="利用日を正しく指定してください。")
    if (end - start).days + 1 > 7:
        raise HTTPException(status_code=400, detail="貸出期間は最長7日間です。")
    if quantity < 1 or not purpose.strip():
        raise HTTPException(status_code=400, detail="数量と使用目的を入力してください。")
    item = equipment_availability(equipment, start.isoformat(), end.isoformat(), load_equipment_catalog())
    if item is None:
        raise HTTPException(status_code=400, detail="選択した器具は利用できません。")
    if quantity > item["available"]:
        raise HTTPException(status_code=409, detail=f"在庫が不足しています。利用可能数: {item['available']}")
    cursor.execute("""INSERT INTO equipment_reservation
        (userid, equipment, start_day, end_day, quantity, purpose, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)""", (request.session.get("user_id"), equipment, start.isoformat(), end.isoformat(), quantity, purpose.strip(), note.strip()))
    conn.commit()
    return {"result": True}

@app.get("/mypage/equipment-reservation/{reservation_id}/cancel")
async def CancelEquipmentReservation(request: Request, reservation_id: int):
    cursor.execute("DELETE FROM equipment_reservation WHERE id = ? AND userid = ?", (reservation_id, request.session.get("user_id")))
    conn.commit()
    return RedirectResponse("/mypage", status_code=303)

@app.get("/equipment-availability")
async def EquipmentAvailability(equipment: str, start_day: str, end_day: str):
    try:
        start = datetime.date.fromisoformat(start_day)
        end = datetime.date.fromisoformat(end_day)
    except ValueError:
        raise HTTPException(status_code=400, detail="日付の形式が正しくありません。")
    if end < start or (end - start).days + 1 > 7:
        raise HTTPException(status_code=400, detail="利用期間は最長7日間で指定してください。")
    item = equipment_availability(equipment, start.isoformat(), end.isoformat(), load_equipment_catalog())
    if item is None:
        raise HTTPException(status_code=404, detail="器具が見つかりません。")
    return {"count": item["count"], "available": item["available"]}

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
    return templates.TemplateResponse(
        request = request,
        name = "teacher/edit.html",
        context = {
            "request": request,
            "teacher_login": request.session.get("teacher_login"),
            "teacher_id": request.session.get("teacher_id")
        }
    )

@app.get("/teacher/studylist", response_class = HTMLResponse)
async def StudyList(request: Request):
    cursor.execute("SELECT * FROM study")
    studies = cursor.fetchall()

    #studiesのうち、teacher_userのschoolと一致するものだけを抽出
    teacher_id = request.session.get("teacher_id")
    
    return templates.TemplateResponse(
        request = request,
        name = "teacher/studylist.html",
        context = {
            "request": request,
            "studies": studies,
            "teacher_login": request.session.get("teacher_login"),
            "teacher_id": teacher_id
        }
    )

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
    equipment: str = Form(...),
    purpose: str = Form(...)
):
    try:
        reservation_start = datetime.datetime.strptime(
            f"{day} {start_time}",
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        raise HTTPException(
            status_code = 400,
            detail = "Invalid reservation datetime."
        )

    if reservation_start < datetime.datetime.now():
        raise HTTPException(
            status_code = 400,
            detail = "Past reservation datetime is not allowed."
        )

    # 予約情報をDBに保存
    cursor.execute(
        """
        INSERT INTO reservation (userid, day, start_time, end_time, equipment, purpose)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (request.session.get("user_id"), day, start_time, end_time, equipment, purpose)
    )
    conn.commit()

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
