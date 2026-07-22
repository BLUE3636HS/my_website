from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import sqlite3, shutil, bcrypt, datetime, csv

app = FastAPI()


studydb = sqlite3.connect("database/study.db", check_same_thread=False)
studycursor = studydb.cursor()

studycursor.execute("""
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
studydb.commit()


userdb = sqlite3.connect("database/user.db", check_same_thread=False)
usercursor = userdb.cursor()

usercursor.execute("""
CREATE TABLE IF NOT EXISTS user (
    id TEXT NOT NULL,
    pwd TEXT NOT NULL,
    school TEXT NOT NULL
)
""")
userdb.commit()


admindb = sqlite3.connect("database/admin_user.db", check_same_thread=False)
admincursor = admindb.cursor()

admincursor.execute("""
CREATE TABLE IF NOT EXISTS admin_user (
    id TEXT NOT NULL,
    pwd TEXT NOT NULL,
    school TEXT NOT NULL
)
""")
admindb.commit()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


#classの定義
#httpが呼び出されたとき最初に実行
class LoginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        #loginしてから一日以上たったらログアウト
        if request.session.get("admin_login") == True:
            login_time = datetime.datetime.strptime(
                request.session.get("admin_time"),
                "%Y-%m-%d %H:%M:%S"
            )
            if (datetime.datetime.now() - login_time).days >= 1:
                request.session["admin_login"] = False
                print("adminログアウト")
        
        if request.session.get("user_login") == True:
            login_time = datetime.datetime.strptime(
                request.session.get("user_time"),
                "%Y-%m-%d %H:%M:%S"
            )
            if (datetime.datetime.now() - login_time).days >= 1:
                request.session["user_login"] = False
                print("userログアウト")

        #loginしていない場合はログインページにリダイレクト
        if(
            request.url.path.startswith("/admin") and
            request.url.path != "/admin/login" and
            request.url.path != "/admin/registration" and
            request.session.get("admin_login") != True
        ):
            return RedirectResponse("/admin/login", status_code = 303)
        elif(
                (
                request.url.path.startswith("/addform") or
                request.url.path.startswith("/studylist")
                ) and
            request.session.get("user_login") != True
        ):
            return RedirectResponse("/login", status_code = 303)
        
        response = await call_next(request)
        return response
app.add_middleware(LoginCheckMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key="blue"
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
    studycursor.execute("SELECT * FROM study")
    studies = studycursor.fetchall()
    print(type(studies))

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

    studycursor.execute(
        "SELECT filename FROM study WHERE id = ?",
        (id,)
    )

    return FileResponse(
        path = pdf_path,
        media_type = "application/pdf"
    )

@app.get("/login")
async def Login(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "login.html"
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
    return templates.TemplateResponse(
        request = request,
        name = "mypage.html"
    )

#ADMINページ
@app.get("/admin")
async def Admin(request: Request):    
    return templates.TemplateResponse(
        request = request,
        name = "admin.html"
    )

@app.get("/admin/login")
async def Login(request: Request):    
    return templates.TemplateResponse(
        request = request,
        name = "admin/login.html"
    )

@app.get("/admin/registration")
async def Registration(request: Request):
    with open("csv/school.csv", "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        schools = [row[0] for row in reader]

    return templates.TemplateResponse(
        request = request,
        name = "admin/registration.html",
        context = {
            "request": request,
            "schools": schools
        }
    )

#仮 request.sessionをfalseにする
@app.get("/reset")
async def Reset(request: Request):
    request.session["admin_login"] = False
    print(request.session.get("admin_login"))
    request.session["user_login"] = False
    print(request.session.get("user_login"))

#仮 sessionの中を確認
@app.get("/session")
async def Session(request: Request):
    return request.session


#post関数
@app.post("/login")
async def Login(
    request: Request,
    id: str = Form(...),
    pwd: str = Form(...)
):
    #user.dbからチェック
    usercursor.execute(
        "SELECT * FROM user WHERE id = ?",
        (id,)
    )
    
    #入力情報と照合
    hashed_pwd = usercursor.fetchone()
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

@app.post("/registration")
async def Registration(
    request: Request,
    id: str = Form(...),
    pwd: str = Form(...),
    school: str = Form(...)
):
    if (id.isascii() and
        len(id) > 7 and
        
        pwd.isascii() and
        len(pwd) > 7 and
        any(i.isalpha() for i in pwd) and
        any(i.isdigit() for i in pwd) and

        school != "notselect"
        ):
        #IDがかぶっていないかチェック
        usercursor.execute(
            "SELECT * FROM user WHERE id = ?",
            (id,)
        )

        if usercursor.fetchone() != None:
            return {"result": 2}
        else: 
            #dbにIDとパスワードに追加
            hashed_pwd = bcrypt.hashpw(
                pwd.encode(),
                bcrypt.gensalt()
            ).decode()
            usercursor.execute(
                """
                INSERT INTO user (id, pwd, school)
                VALUES (?, ?, ?)
                """,
                (id, hashed_pwd, school)
            )
            userdb.commit()
            
            print("user.dbに情報を追加")

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
    studycursor.execute(
        """
        INSERT INTO study (name, introduce, filename, pdfpath, userid, time)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, introduce, pdf.filename, "", request.session.get("user_id"), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    id = studycursor.lastrowid
    studycursor.execute(
        "UPDATE study SET pdfpath = ? WHERE id = ?",
        (f"{id}.pdf", id)
    )
    studydb.commit()

    #pdfファイルを保存
    pdf_path = f"uploads/{id}.pdf"
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(pdf.file, f)
    
    print("study.dbに情報を追加,ファイルを保存")

@app.post("/admin/login")
async def Login(
    request: Request,
    id: str = Form(...),
    pwd: str = Form(...)
):
    #admin_user.dbからチェック
    admincursor.execute(
        "SELECT * FROM admin_user WHERE id = ?",
        (id,)
    )    

    #入力情報と照合
    hashed_pwd = admincursor.fetchone()
    if hashed_pwd is None:
        return {"result": False}
    else:    
        pwdcheck = bcrypt.checkpw(
            pwd.encode(),
            hashed_pwd[1].encode()
        )

    if pwdcheck:
        request.session["admin_login"] = True
        request.session["admin_id"] = id
        request.session["admin_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"result": True}
    else:
        return {"result": False}
    
@app.post("/admin/registration")
async def Registration(
    request: Request,
    id: str = Form(...),
    pwd: str = Form(...),
    school: str = Form(...)
):
    if (id.isascii() and
        len(id) > 7 and
        
        pwd.isascii() and
        len(pwd) > 7 and
        any(i.isalpha() for i in pwd) and
        any(i.isdigit() for i in pwd) and

        school != "notselect"
        ):
        #IDがかぶっていないかチェック
        admincursor.execute(
            "SELECT * FROM admin_user WHERE id = ?",
            (id,)
        )

        if admincursor.fetchone() != None:
            return {"result": 2}
        else: 
            #dbにIDとパスワードに追加
            hashed_pwd = bcrypt.hashpw(
                pwd.encode(),
                bcrypt.gensalt()
            ).decode()
            admincursor.execute(
                """
                INSERT INTO admin_user (id, pwd, school)
                VALUES (?, ?, ?)
                """,
                (id, hashed_pwd, school)
            )
            admindb.commit()
            
            print("admin_user.dbに情報を追加")

            return {"result": 0}
    else:
        return {"result": 1}