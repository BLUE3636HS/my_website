"""先生・管理者ダッシュボード用の読み取り専用集計（データは変更しない）。"""
import datetime

from mypage_calendar import admin_calendar_reservations
from studies import get_submission_collaboration

JST = datetime.timezone(datetime.timedelta(hours=9))
INACTIVE_DAYS = 30
RECENT_STUDY_LIMIT = 5
CATEGORY_LABELS = {"room": "TEKNE工作室", "mentor": "大学生メンター", "equipment": "実験器具"}


def login_status(last_access_at, now=None):
    """最終アクセス日時から 'never'（一度もなし）/'stale'（30日以上なし）/'active' を判定する。"""
    now = now or datetime.datetime.now()
    if not last_access_at:
        return {"state": "never", "days": None, "last_access_at": None}
    try:
        last = datetime.datetime.strptime(last_access_at[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"state": "active", "days": None, "last_access_at": last_access_at}
    days = (now - last).days
    state = "stale" if last < now - datetime.timedelta(days=INACTIVE_DAYS) else "active"
    return {"state": state, "days": days, "last_access_at": last_access_at[:16]}


def teacher_dashboard_summary(db, school, now=None):
    """自校の最近の提出物と、未ログイン（一度もない／30日以上ない）の生徒数を返す。"""
    now = now or datetime.datetime.now()
    threshold = (now - datetime.timedelta(days=INACTIVE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    total, never, stale = db.execute("""
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN last_access_at IS NULL OR last_access_at = '' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN last_access_at IS NOT NULL AND last_access_at <> ''
                                  AND last_access_at < ? THEN 1 ELSE 0 END), 0)
        FROM student WHERE school = ?""", (threshold, school)).fetchone()
    rows = db.execute("""
        SELECT study.id, study.name, study.userid, study.time
        FROM study
        WHERE EXISTS (SELECT 1 FROM student WHERE student.id = study.userid AND student.school = ?)
        ORDER BY study.time DESC, study.id DESC
        LIMIT ?""", (school, RECENT_STUDY_LIMIT)).fetchall()
    collaboration = get_submission_collaboration(db, [row[0] for row in rows])
    recent = []
    for study_id, title, user_id, time in rows:
        group = collaboration.get(study_id)
        recent.append({
            "id": study_id, "title": title, "time": time,
            "submitter": "、".join(group["submitters"]) if group and group["submitters"] else user_id,
            "group_name": group["group_name"] if group else None,
        })
    return {
        "student_count": total, "inactive_never": never, "inactive_stale": stale,
        "inactive_total": never + stale, "inactive_days": INACTIVE_DAYS, "recent_studies": recent,
    }


def admin_dashboard_summary(db, now=None):
    """今日・今後7日間の予約件数（キャンセル済みを除く）と未返却の実験器具を返す。"""
    now = now or datetime.datetime.now(JST)
    today = now.date()
    today_text = today.isoformat()
    week_end = (today + datetime.timedelta(days=7)).isoformat()
    events = admin_calendar_reservations(db, now)

    def counts(selected):
        result = {key: 0 for key in CATEGORY_LABELS}
        for event in selected:
            result[event["category"]] += 1
        return result

    # 持ち出しは「返却済み」を除き、期間が今日にかかるものを今日の予約として数える。
    returned_takeouts = {
        f"takeout-{row[0]}" for row in db.execute("SELECT id FROM equipment_reservation WHERE returned <> 0")
    }
    active = [event for event in events if event["key"] not in returned_takeouts]
    today_events = [event for event in active if event["start"] <= today_text <= event["end"]]
    upcoming = [event for event in active if today_text < event["start"] <= week_end]

    overdue_rows = db.execute("""
        SELECT r.id, r.userid, r.equipment, r.quantity, r.end_day, s.school
        FROM equipment_reservation r LEFT JOIN student s ON s.id = r.userid
        WHERE r.returned = 0 AND r.end_day < ?
        ORDER BY r.end_day ASC, r.id ASC""", (today_text,)).fetchall()
    def days_late(end_day):
        try:
            return (today - datetime.date.fromisoformat(end_day)).days
        except (TypeError, ValueError):
            return None

    overdue = [{
        "id": row[0], "student_id": row[1], "equipment": row[2], "quantity": row[3],
        "end_day": row[4], "school": row[5] or "不明", "days_late": days_late(row[4]),
    } for row in overdue_rows]

    today_list = [{
        "category": event["category"], "category_label": CATEGORY_LABELS[event["category"]],
        "title": event["title"], "student_id": event["studentId"],
        "time": f"{event['startTime']}〜{event['endTime']}" if event["startTime"] else "持ち出し期間中",
        "url": event["management_url"],
    } for event in today_events]
    today_list.sort(key=lambda item: (item["time"] == "持ち出し期間中", item["time"]))

    return {
        "today": today_text, "week_end": week_end,
        "today_counts": counts(today_events), "today_total": len(today_events),
        "upcoming_counts": counts(upcoming), "upcoming_total": len(upcoming),
        "today_list": today_list, "overdue": overdue, "overdue_total": len(overdue),
        "category_labels": CATEGORY_LABELS,
    }
