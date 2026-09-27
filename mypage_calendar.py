"""Read-only normalization of a student's reservations for the My Page calendar."""
import datetime

from mentor_reservations import mentor_reservations_for_student

JST = datetime.timezone(datetime.timedelta(hours=9))


def calendar_reservations(db, user_id, now=None):
    now = now or datetime.datetime.now(JST)
    today, current_time = now.date().isoformat(), now.strftime("%H:%M")
    events = []

    def add(kind, reservation_id, category, title, start, end, start_time, end_time,
            details, cancel_url, method="post", returned=False):
        is_past = end < today or (end == today and bool(end_time) and end_time <= current_time)
        can_cancel = not is_past and not returned
        # Takeout reservations remain colored while an item still needs to be returned,
        # even when the planned end date has passed.
        is_muted = returned if kind == "takeout" else is_past
        events.append({
            "key": f"{kind}-{reservation_id}", "kind": kind, "category": category,
            "title": title, "start": start, "end": end, "startTime": start_time,
            "endTime": end_time, "details": details, "can_cancel": can_cancel,
            "isMuted": is_muted,
            "cancel_url": cancel_url, "cancel_method": method,
        })

    for row in mentor_reservations_for_student(db, user_id):
        if row["status"] != "active":
            continue
        add("mentor", row["id"], "mentor", row["mentor_name"], row["day"], row["day"],
            row["start_time"], row["end_time"], [
                ("利用形式", "オンライン" if row["meeting_type"] == "online" else "オフライン"),
                ("相談内容", row["consultation"])],
            f"/mypage/mentor-reservation/{row['id']}/cancel")

    for row in db.execute("""SELECT id, day, start_time, end_time, purpose FROM reservation
                             WHERE userid = ? AND status = 'active' ORDER BY day, start_time, id""", (user_id,)):
        add("room", row[0], "room", "TEKNE工作室", row[1], row[1], row[2], row[3],
            [("利用目的", row[4])], f"/mypage/reservation/{row[0]}/cancel")

    for row in db.execute("""SELECT id, equipment, start_day, end_day, quantity, purpose, returned
                             FROM equipment_reservation WHERE userid = ?""", (user_id,)):
        add("takeout", row[0], "equipment", row[1], row[2], row[3], "", "", [
            ("利用区分", "持ち出し"), ("数量", row[4]), ("使用目的", row[5]),
            ("返却状態", "返却済み" if row[6] else "未返却")],
            f"/mypage/equipment-reservation/{row[0]}/cancel", "get", bool(row[6]))

    for row in db.execute("""SELECT id, equipment, use_day, start_time, end_time, quantity, purpose
                             FROM equipment_room_reservation WHERE userid = ?""", (user_id,)):
        add("equipment-room", row[0], "equipment", row[1], row[2], row[2], row[3], row[4], [
            ("利用区分", "工作室内"), ("数量", row[5]), ("使用目的", row[6]), ("返却状態", "対象外")],
            f"/mypage/equipment-room-reservation/{row[0]}/cancel")

    return sorted(events, key=lambda event: (event["start"], event["startTime"], event["key"]))
