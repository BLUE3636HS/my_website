from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import sqlite3, shutil, bcrypt

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key="blue"
)

studydb = sqlite3.connect("database/study.db", check_same_thread=False)
studycursor = studydb.cursor()

studycursor.execute("""
CREATE TABLE IF NOT EXISTS study (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    introduce TEXT NOT NULL,
    filename TEXT,
    pdfpath TEXT
)
""")
studydb.commit()


userdb = sqlite3.connect("database/user.db", check_same_thread=False)
usercursor = userdb.cursor()

usercursor.execute("""
CREATE TABLE IF NOT EXISTS user (
    id TEXT NOT NULL,
    pwd TEXT NOT NULL
)
""")
userdb.commit()


admindb = sqlite3.connect("database/admin_user.db", check_same_thread=False)
admincursor = admindb.cursor()

admincursor.execute("""
CREATE TABLE IF NOT EXISTS admin_user (
    id TEXT NOT NULL,
    pwd TEXT NOT NULL
)
""")
admindb.commit()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


#classの定義


@app.get("/home", response_class = HTMLResponse)
def Home(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "home.html"
    )

@app.get("/addform", response_class = HTMLResponse)
def Start(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "addform.html"
    )

@app.get("/studylist", response_class = HTMLResponse)
def Start(request: Request):
    studycursor.execute("SELECT * FROM study")
    users = studycursor.fetchall()

    return templates.TemplateResponse(
        request = request,
        name = "pdflist.html",
        context = {
            "request": request,
            "users": users
        }
    )

@app.get("/uploads/{id}.pdf")
def pdf(id: int):
    pdf_path = f"uploads/{id}.pdf"

    studycursor.execute(
        "SELECT filename FROM study WHERE id = ?",
        (id,)
    )
    filename = studycursor.fetchone()[0]
    print(filename)

    return FileResponse(
        path = pdf_path,
        media_type = "application/pdf",
        filename = filename
    )

@app.get("/login")
def Login(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "login.html"
    )

@app.get("/registration")
def Registration(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "registration.html"
    )

#ADMINページ
@app.get("/admin")
def Admin(request: Request):
    if request.session.get("admin_login") != True:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
            )
    else:
        return templates.TemplateResponse(
            request = request,
            name = "admin.html"
        )

@app.get("/admin/login")
def Login(request: Request):    
    return templates.TemplateResponse(
        request = request,
        name = "admin/login.html"
    )

@app.get("/admin/registration")
def Registration(request: Request):
    return templates.TemplateResponse(
        request = request,
        name = "admin/registration.html"
    )

#仮 request.sessionをfalseにする
@app.get("/reset")
def Reset(request: Request):
    request.session["admin_login"] = False
    print(request.session.get("admin_login"))
    request.session["login"] = False
    print(request.session.get("login"))



@app.post("/login")
def Login(
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
        request.session["login"] = True
        return {"result": True}
    else:
        return {"result": False}

@app.post("/registration")
def Regisrtation(
    request: Request,
    id: str = Form(...),
    pwd: str = Form(...)
):
    if (id.isascii() and
        len(id) > 7 and
        
        pwd.isascii() and
        len(pwd) > 7 and
        any(i.isalpha() for i in pwd) and
        any(i.isdigit() for i in pwd)
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
                INSERT INTO user (id, pwd)
                VALUES (?, ?)
                """,
                (id, hashed_pwd)
            )
            userdb.commit()
            
            print("user.dbに情報を追加")

            return {"result": 0}
    else:
        return {"result": 1}

@app.post("/add")
def Add(
    name: str = Form(...),
    introduce: str = Form(...),
    pdf: UploadFile = File(...)
):
    #dbに保存
    studycursor.execute(
        """
        INSERT INTO study (name, introduce, filename, pdfpath)
        VALUES (?, ?, ?, ?)
        """,
        (name, introduce, pdf.filename, "")
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
def Login(
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
        return {"result": True}
    else:
        return {"result": False}
    
@app.post("/admin/registration")
def Regisrtation(
    request: Request,
    id: str = Form(...),
    pwd: str = Form(...)
):
    if (id.isascii() and
        len(id) > 7 and
        
        pwd.isascii() and
        len(pwd) > 7 and
        any(i.isalpha() for i in pwd) and
        any(i.isdigit() for i in pwd)
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
                INSERT INTO admin_user (id, pwd)
                VALUES (?, ?)
                """,
                (id, hashed_pwd)
            )
            admindb.commit()
            
            print("admin_user.dbに情報を追加")

            return {"result": 0}
    else:
        return {"result": 1}