"""Prepare fictional data for the local TEKNE preview."""

import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path

import bcrypt

ROOT = Path(__file__).resolve().parent
SCHOOL = "TEKNEデモ高等学校"
STUDENT = "demo_student"
TEACHER = "demo_teacher"
ADMIN = "demo_admin"
PASSWORD = "TekneDemo2026"
GROUP = "材料実験チーム"


def seed():
    (ROOT / "database").mkdir(exist_ok=True)
    from main import DATABASE_PATH, JST, UPLOADS_DIR, tekne_chat_service
    from notifications import create_notification
    from school_features import seed_school_feature_settings
    from school_registration import issue_school_id
    from tekne_chat import create_chat_session, link_student_identity, revoke_chat_session

    now = dt.datetime.now(JST)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    day = (now.date() + dt.timedelta(days=2)).isoformat()
    returned = (now.date() + dt.timedelta(days=3)).isoformat()
    code_file = DATABASE_PATH.parent / "demo-school-id.txt"
    with closing(sqlite3.connect(DATABASE_PATH)) as db, db:
        new_school = not db.execute("SELECT 1 FROM registered_school WHERE name=?", (SCHOOL,)).fetchone()
        if new_school:
            school_code = issue_school_id(db, SCHOOL)
        else:
            school_code = None
        password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
        for student_id, name, kana in (
            (STUDENT, "実験花子", "ジッケンハナコ"),
            ("demo_partner", "実験太郎", "ジッケンタロウ"),
        ):
            if not db.execute("SELECT 1 FROM student WHERE id=?", (student_id,)).fetchone():
                db.execute("INSERT INTO student(id,pwd,school,name,furigana) VALUES (?,?,?,?,?)",
                           (student_id, password_hash, SCHOOL, name, kana))
            link_student_identity(db, student_id, SCHOOL)
        if not db.execute("SELECT 1 FROM teacher WHERE id=?", (TEACHER,)).fetchone():
            db.execute("INSERT INTO teacher(id,pwd,school,role) VALUES (?,?,?,'super_teacher')",
                       (TEACHER, password_hash, SCHOOL))
        db.execute("INSERT OR IGNORE INTO admin(id,pwd,name,role) VALUES (?,?,?,'super_admin')",
                   (ADMIN, password_hash, "デモ管理者"))
        db.execute("""INSERT OR IGNORE INTO mentor_profile(admin_id,is_published,display_name,description,created_at,updated_at)
                      VALUES (?,1,?,?,?,?)""",
                   (ADMIN, "デモメンター", "実験の進め方とデータ整理について相談できます。", timestamp, timestamp))
        db.executemany("""INSERT OR IGNORE INTO mentor_available_slot(admin_id,day,start_time,offline_available,created_at,updated_at)
                          VALUES (?,?,?,1,?,?)""",
                       [(ADMIN, day, time, timestamp, timestamp) for time in ("15:00", "15:30", "16:00")])
        if not db.execute("SELECT 1 FROM school_report_template WHERE school=?", (SCHOOL,)).fetchone():
            db.execute("INSERT OR IGNORE INTO study_template(seed_key,name,owner_school) VALUES (?,?,?)",
                       ("ui-codex-demo", "実験レポート", SCHOOL))
            template_id = db.execute("SELECT id FROM study_template WHERE seed_key='ui-codex-demo'").fetchone()[0]
            db.execute("""INSERT INTO study_template_field(template_id,label,position)
                          SELECT ?, '実験の目的', 1 WHERE NOT EXISTS (
                              SELECT 1 FROM study_template_field WHERE template_id=?
                          )""", (template_id, template_id))
            db.execute("INSERT INTO school_report_template(school,template_id,updated_at,updated_by) VALUES (?,?,?,?)",
                       (SCHOOL, template_id, timestamp, TEACHER))

        sample = UPLOADS_DIR / "demo-research.txt"
        if not sample.exists():
            sample.write_text("架空の研究サンプル\n素材ごとに水の吸収量を比較しました。\n", encoding="utf-8")
        if not db.execute("SELECT 1 FROM study WHERE userid=? AND pdfpath=?", (STUDENT, sample.name)).fetchone():
            db.execute("INSERT INTO study(name,introduce,filename,pdfpath,userid,time) VALUES (?,?,?,?,?,?)",
                       ("素材ごとの吸水性の比較", "紙・布・木材の吸水量を比較した実験です。",
                        "吸水性の実験メモ.txt", sample.name, STUDENT, timestamp))
        first_slot = dt.datetime.combine(now.date(), dt.time(13))
        for offset in range(8):
            start = first_slot + dt.timedelta(minutes=30 * offset)
            end = start + dt.timedelta(minutes=30)
            db.execute("""INSERT OR IGNORE INTO reservation_available_slot(admin_id,day,start_time,end_time,created_at,updated_at)
                          VALUES (?,?,?,?,?,?)""",
                       (ADMIN, day, start.strftime("%H:%M"), end.strftime("%H:%M"), timestamp, timestamp))
        purpose = "デモ：素材の加工"
        if not db.execute("SELECT 1 FROM reservation WHERE userid=? AND purpose=?", (STUDENT, purpose)).fetchone():
            db.execute("INSERT INTO reservation(userid,day,start_time,end_time,purpose,created_at) VALUES (?,?,?,?,?,?)",
                       (STUDENT, day, "14:00", "15:00", purpose, timestamp))
        purpose = "デモ：試料の分注"
        if not db.execute("SELECT 1 FROM equipment_reservation WHERE userid=? AND purpose=?", (STUDENT, purpose)).fetchone():
            db.execute("""INSERT INTO equipment_reservation(userid,equipment,equipment_id,start_day,end_day,quantity,purpose)
                          VALUES (?,'マイクロピペット','EQ001',?,?,1,?)""", (STUDENT, day, returned, purpose))
        title = "工作室を利用する前に"
        if not db.execute("SELECT 1 FROM notification WHERE recipient_user_id=? AND title=?", (STUDENT, title)).fetchone():
            create_notification(db, STUDENT, title, "当日は生徒証を持参してください。")
        post = "素材の吸水性を比べる実験を進めています。工夫した点があれば教えてください。"
        if not db.execute("SELECT 1 FROM community_post WHERE user_id=? AND content=?", (STUDENT, post)).fetchone():
            db.execute("INSERT INTO community_post(user_id,content,created_at) VALUES (?,?,?)", (STUDENT, post, timestamp))
    if school_code:
        code_file.write_text(school_code + "\n", encoding="ascii")
    seed_school_feature_settings(DATABASE_PATH)

    token = create_chat_session(DATABASE_PATH, STUDENT)
    session = {"user_login": True, "user_id": STUDENT, "user_time": timestamp, "tekne_chat_session": token}
    try:
        if not any(group["name"] == GROUP for group in tekne_chat_service.groups(session)):
            group = tekne_chat_service.create_group(session, GROUP)
            tekne_chat_service.invite(session, group["id"], "demo_partner")
            tekne_chat_service.create_message(session, group["id"], "次回は試料の厚みをそろえて測定しましょう。")
    finally:
        revoke_chat_session(DATABASE_PATH, token)

    print(f"Student: {STUDENT} / {PASSWORD}")
    print(f"School registration code file: {code_file}")


if __name__ == "__main__":
    seed()
