from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import sqlite3, shutil, bcrypt, datetime, csv

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
    CREATE TABLE IF NOT EXISTS reservation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userid TEXT NOT NULL,
        time TEXT NOT NULL
    )
""")
conn.commit()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


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
                request.session["teacher_login"] = False
                print("teacherログアウト")
        
        if request.session.get("user_login") == True:
            login_time = datetime.datetime.strptime(
                request.session.get("user_time"),
                "%Y-%m-%d %H:%M:%S"
            )
            if (datetime.datetime.now() - login_time).days >= 1:
                request.session["user_login"] = False
                print("userログアウト")

        # ログインなしでもアクセスできるページを定義
        public_paths = [
            "/login",
            "/registration",
            "/session",
            "/logout"
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

        # 管理者ログイン済みなら teacher 配下のみ許可
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
    return templates.TemplateResponse(
        request = request,
        name = "reservation/date.html",
        context = {
            "request": request,
            "year": year,
            "month": month,
            "day": day,
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

@app.get("/registration")
async def Registration(request: Request):
    with open("csv/school.csv", "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        schools = [row[0] for row in reader]

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
    request.session.clear()
    return templates.TemplateResponse(
        request = request,
        name = "logout.html"
    )

@app.get("/mypage")
async def Mypage(request: Request):
    user_id = request.session.get("user_id")

    cursor.execute("""
    SELECT *
    FROM student
    WHERE id = ?
    """, (user_id,))

    user_school = cursor.fetchone()[2]

    return templates.TemplateResponse(
        request = request,
        name = "mypage.html",
        context = {
            "request": request,
            "user_login": request.session.get("user_login"),
            "user_id": user_id,
            "user_school": user_school
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