import os
import unittest
from unittest.mock import MagicMock, patch

import google_calendar


class GoogleCalendarTests(unittest.TestCase):
    def settings(self):
        return patch.dict(os.environ, {
            "GOOGLE_CALENDAR_CLIENT_ID": "client",
            "GOOGLE_CALENDAR_CLIENT_SECRET": "secret",
            "GOOGLE_CALENDAR_REFRESH_TOKEN": "refresh",
            "GOOGLE_CALENDAR_ID": "primary",
        }, clear=False)

    def test_create_uses_jst_unique_conference_request_and_no_attendees(self):
        service = MagicMock()
        service.events.return_value.insert.return_value.execute.return_value = {
            "id": "event-1", "hangoutLink": "https://meet.google.com/abc-defg-hij"}
        with self.settings(), patch("google_calendar._service", return_value=service):
            result = google_calendar.create_mentor_event(
                day="2099-01-01", start_time="13:00", end_time="14:00",
                student_label="student-1", mentor_name="Mentor", consultation="相談",
                reservation_request_id="unique-request")
        self.assertEqual(result, ("event-1", "https://meet.google.com/abc-defg-hij"))
        kwargs = service.events.return_value.insert.call_args.kwargs
        self.assertEqual(kwargs["conferenceDataVersion"], 1)
        self.assertEqual(kwargs["sendUpdates"], "none")
        self.assertEqual(kwargs["body"]["attendees"], [])
        self.assertEqual(kwargs["body"]["start"]["timeZone"], "Asia/Tokyo")
        self.assertTrue(kwargs["body"]["start"]["dateTime"].endswith("+09:00"))
        self.assertEqual(kwargs["body"]["conferenceData"]["createRequest"]["requestId"], "unique-request")

    def test_missing_or_untrusted_meet_url_deletes_event_and_fails(self):
        service = MagicMock()
        service.events.return_value.insert.return_value.execute.return_value = {
            "id": "event-2", "hangoutLink": "javascript:alert(1)"}
        service.events.return_value.get.return_value.execute.return_value = {"id": "event-2"}
        with self.settings(), patch("google_calendar._service", return_value=service), patch("google_calendar.time.sleep"):
            with self.assertRaises(google_calendar.GoogleCalendarError):
                google_calendar.create_mentor_event(
                    day="2099-01-01", start_time="13:00", end_time="14:00",
                    student_label="s", mentor_name="m", consultation="c",
                    reservation_request_id="request")
        service.events.return_value.delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
