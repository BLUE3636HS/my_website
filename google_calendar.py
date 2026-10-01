"""Google Calendar integration for mentor reservations."""

import datetime
import logging
import os
import time
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
JST = datetime.timezone(datetime.timedelta(hours=9))


class GoogleCalendarError(RuntimeError):
    """A safe, user-displayable Google Calendar integration failure."""


def _settings():
    names = ("GOOGLE_CALENDAR_CLIENT_ID", "GOOGLE_CALENDAR_CLIENT_SECRET",
             "GOOGLE_CALENDAR_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if any(not value for value in values.values()):
        raise GoogleCalendarError("Google Meetの準備に必要な設定が完了していません。")
    return values


def _service():
    settings = _settings()
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        credentials = Credentials(
            token=None, refresh_token=settings["GOOGLE_CALENDAR_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings["GOOGLE_CALENDAR_CLIENT_ID"],
            client_secret=settings["GOOGLE_CALENDAR_CLIENT_SECRET"],
            scopes=[CALENDAR_SCOPE],
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)
    except GoogleCalendarError:
        raise
    except Exception as error:
        raise GoogleCalendarError("Google Meetの準備に失敗しました。") from error


def _meet_url(event):
    candidate = None
    if event.get("hangoutLink"):
        candidate = event["hangoutLink"]
    if not candidate:
        for entry in event.get("conferenceData", {}).get("entryPoints", []):
            if entry.get("entryPointType") == "video" and entry.get("uri"):
                candidate = entry["uri"]
                break
    parsed = urlparse(candidate or "")
    return candidate if parsed.scheme == "https" and parsed.hostname == "meet.google.com" else None


def create_mentor_event(*, day, start_time, end_time, student_label,
                        mentor_name, consultation, reservation_request_id):
    settings = _settings()
    service = _service()
    start = datetime.datetime.fromisoformat(f"{day}T{start_time}:00").replace(tzinfo=JST)
    end = datetime.datetime.fromisoformat(f"{day}T{end_time}:00").replace(tzinfo=JST)
    body = {
        "summary": f"大学生メンター相談 - {student_label}",
        "description": f"相談内容: {consultation}\n担当メンター: {mentor_name}",
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
        "attendees": [],
        "conferenceData": {"createRequest": {
            "requestId": reservation_request_id,
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }},
    }
    event_id = None
    try:
        event = service.events().insert(
            calendarId=settings["GOOGLE_CALENDAR_ID"], body=body,
            conferenceDataVersion=1, sendUpdates="none").execute()
        event_id, meet_url = event.get("id"), _meet_url(event)
        for _ in range(5):
            if event_id and meet_url:
                break
            if not event_id:
                break
            time.sleep(0.2)
            event = service.events().get(
                calendarId=settings["GOOGLE_CALENDAR_ID"], eventId=event_id).execute()
            meet_url = _meet_url(event)
        if not event_id or not meet_url:
            if event_id:
                try:
                    service.events().delete(calendarId=settings["GOOGLE_CALENDAR_ID"],
                                            eventId=event_id, sendUpdates="none").execute()
                except Exception:
                    LOGGER.exception("Failed to compensate Calendar event without Meet: event_id=%s", event_id)
            raise GoogleCalendarError("Google Meetの準備に失敗したため、予約を完了できませんでした。")
        return event_id, meet_url
    except GoogleCalendarError:
        raise
    except Exception as error:
        if event_id:
            try:
                service.events().delete(calendarId=settings["GOOGLE_CALENDAR_ID"],
                                        eventId=event_id, sendUpdates="none").execute()
            except Exception:
                LOGGER.exception("Failed to compensate Calendar event after API failure: event_id=%s", event_id)
        raise GoogleCalendarError("Google Meetの準備に失敗したため、予約を完了できませんでした。") from error


def delete_mentor_event(event_id):
    if not event_id:
        return
    settings = _settings()
    try:
        _service().events().delete(calendarId=settings["GOOGLE_CALENDAR_ID"],
                                   eventId=event_id, sendUpdates="none").execute()
    except Exception as error:
        raise GoogleCalendarError("Google Calendar予定を削除できませんでした。") from error
