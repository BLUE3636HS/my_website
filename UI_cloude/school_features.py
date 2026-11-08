"""School-specific visibility and access settings for student features."""

import datetime
import secrets
import sqlite3
from contextlib import closing

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse


FEATURES = (
    ("dashboard", "ダッシュボード"),
    ("studies_add", "研究成果追加フォーム"),
    ("studies_list", "研究成果一覧"),
    ("community", "TEKNEコミュニティ"),
    ("workshop_reservation", "TEKNE工作室予約"),
    ("mentor_reservation", "大学生メンター予約"),
    ("equipment_reservation", "実験器具の貸し出し予約"),
)
FEATURE_KEYS = {key for key, _ in FEATURES}


def feature_for_student_path(path):
    if path == "/dashboard":
        return "dashboard"
    if path == "/addform":
        return "studies_add"
    if path == "/studylist":
        return "studies_list"
    if path == "/community" or path.startswith("/community/"):
        return "community"
    if path == "/reservation" or path.startswith("/reservation/"):
        return "workshop_reservation"
    if path == "/mentor-reservation" or path.startswith("/mentor-reservation/"):
        return "mentor_reservation"
    if path in {"/equipment-reservation", "/equipment-availability",
                "/equipment-room-availability", "/equipment-room-reservation"}:
        return "equipment_reservation"
    return None


def school_names_from_database(db):
    return [row[0] for row in db.execute(
        "SELECT name AS school FROM registered_school "
        "WHERE name IS NOT NULL AND TRIM(name)<>'' "
        "UNION SELECT school FROM student "
        "WHERE school IS NOT NULL AND TRIM(school)<>'' "
        "ORDER BY school"
    ).fetchall()]


def initialize_school_feature_settings(db):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""
        CREATE TABLE IF NOT EXISTS school_feature_setting (
            school TEXT NOT NULL,
            feature_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (school, feature_key)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS global_feature_setting (
            feature_key TEXT PRIMARY KEY NOT NULL,
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            updated_at TEXT NOT NULL
        )
    """)


def seed_school_feature_settings(database_path):
    with closing(sqlite3.connect(database_path, timeout=10)) as db:
        initialize_school_feature_settings(db)
        schools = school_names_from_database(db)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        db.executemany(
            "INSERT OR IGNORE INTO school_feature_setting(school, feature_key, enabled, updated_at) VALUES(?,?,1,?)",
            [(school, feature, now) for school in schools for feature in FEATURE_KEYS],
        )
        db.commit()


def school_feature_settings(db, school):
    flags = {key: True for key, _ in FEATURES}
    if not school:
        return flags
    rows = db.execute(
        "SELECT feature_key, enabled FROM school_feature_setting WHERE school=?",
        (school,),
    ).fetchall()
    flags.update({key: bool(enabled) for key, enabled in rows if key in FEATURE_KEYS})
    return flags


def school_feature_flags(db, school):
    flags = school_feature_settings(db, school)
    global_rows = db.execute(
        "SELECT feature_key, enabled FROM global_feature_setting"
    ).fetchall()
    for key, enabled in global_rows:
        if key in FEATURE_KEYS:
            flags[key] = flags[key] and bool(enabled)
    return flags


def enabled_for_student(database_path, user_id, feature):
    if feature not in FEATURE_KEYS:
        return False
    with closing(sqlite3.connect(database_path)) as db:
        row = db.execute("SELECT school FROM student WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return False
        return school_feature_flags(db, row[0])[feature]


def build_school_feature_router(database_path, templates):
    router = APIRouter(prefix="/admin/school-features")

    def render(request, error=None, status_code=200):
        with closing(sqlite3.connect(database_path)) as db:
            from admin_management import require_super_admin
            require_super_admin(db, request)
            schools = school_names_from_database(db)
            settings = {}
            global_settings = {row[0]: bool(row[1]) for row in db.execute(
                "SELECT feature_key, enabled FROM global_feature_setting"
            ).fetchall()}
            for school in schools:
                settings[school] = school_feature_settings(db, school)
        token = request.session.setdefault("school_features_csrf", secrets.token_urlsafe(32))
        return templates.TemplateResponse(
            request=request,
            name="admin/school_features.html",
            context={"request": request, "schools": schools, "features": FEATURES,
                     "global_settings": global_settings,
                     "settings": settings, "csrf_token": token, "error": error,
                     "notice": request.session.pop("school_features_notice", None)},
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("")
    async def settings_page(request: Request):
        return render(request)

    @router.post("")
    async def update_settings(request: Request, csrf_token: str = Form(...)):
        form = await request.form()
        with closing(sqlite3.connect(database_path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            from admin_management import require_super_admin
            require_super_admin(db, request)
            expected = request.session.get("school_features_csrf", "")
            if not expected or not secrets.compare_digest(expected, csrf_token):
                raise HTTPException(403, "CSRFトークンが不正です。")
            schools = school_names_from_database(db)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for feature in FEATURE_KEYS:
                value = form.get(f"global:{feature}")
                if value not in (None, "1"):
                    raise HTTPException(400, "全体設定の値が不正です。")
                db.execute(
                    "INSERT INTO global_feature_setting(feature_key,enabled,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(feature_key) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",
                    (feature, int(value == "1"), now),
                )
            for school in schools:
                for feature in FEATURE_KEYS:
                    value = form.get(f"enabled:{school}:{feature}")
                    if value not in (None, "1"):
                        raise HTTPException(400, "設定値が不正です。")
                    db.execute(
                        "INSERT INTO school_feature_setting(school,feature_key,enabled,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(school,feature_key) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",
                        (school, feature, int(value == "1"), now),
                    )
            db.commit()
        request.session["school_features_csrf"] = secrets.token_urlsafe(32)
        request.session["school_features_notice"] = "学校ごとの表示設定を保存しました。"
        return RedirectResponse("/admin/school-features", status_code=303)

    return router
