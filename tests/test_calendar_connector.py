import json
import datetime
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from calendar_connector import calendar_connector


class _MockLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeEventsService:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        calendar_id = kwargs["calendarId"]
        return _FakeExecute(
            {
                "items": [
                    {
                        "id": f"id-{calendar_id}",
                        "summary": f"Event {calendar_id}",
                        "start": {"dateTime": "2026-03-10T10:00:00Z"},
                        "end": {"dateTime": "2026-03-10T11:00:00Z"},
                    }
                ]
            }
        )

    def insert(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeExecute(
            {
                "id": "created-id",
                "summary": kwargs["body"]["summary"],
                "start": {"dateTime": kwargs["body"]["start"]["dateTime"]},
                "end": {"dateTime": kwargs["body"]["end"]["dateTime"]},
                "htmlLink": "https://calendar.google.com/event?eid=created-id",
            }
        )


class _FakeService:
    def __init__(self):
        self._events = _FakeEventsService()

    def events(self):
        return self._events


class TestCalendarConnector(unittest.TestCase):
    def test_extract_first_json_object(self):
        payload = calendar_connector._extract_first_json_object('{"a":1}{"b":2}')
        self.assertEqual(payload["a"], 1)

    @patch("calendar_connector.calendar_connector.Credentials.from_authorized_user_info")
    @patch("calendar_connector.calendar_connector.Credentials.from_authorized_user_file")
    def test_load_credentials_recovers_token_with_trailing_json(
        self,
        mock_from_file,
        mock_from_info,
    ):
        mock_from_file.side_effect = json.JSONDecodeError("Extra data", "{}", 2)
        mock_from_info.return_value = object()

        with tempfile.NamedTemporaryFile(mode="w+", delete=True, encoding="utf-8") as temp_token:
            temp_token.write('{"refresh_token":"abc","client_id":"id","client_secret":"secret","token_uri":"uri"}{"extra":true}')
            temp_token.flush()

            creds = calendar_connector._load_credentials_from_token(
                temp_token.name,
                ["scope"],
                _MockLogger(),
            )

            self.assertIsNotNone(creds)
            with open(temp_token.name, "r", encoding="utf-8") as token_file:
                saved = json.load(token_file)
            self.assertEqual(saved["refresh_token"], "abc")

    def test_normalize_event_datetime_supports_short_format(self):
        iso_value, parsed = calendar_connector._normalize_event_datetime(
            "2026-03-06T10:00",
            "America/Sao_Paulo",
        )
        self.assertIn("2026-03-06T10:00:00", iso_value)
        self.assertIsNotNone(parsed.tzinfo)

    def test_normalize_event_datetime_rejects_invalid_timezone(self):
        with self.assertRaises(ValueError):
            calendar_connector._normalize_event_datetime(
                "2026-03-06T10:00",
                "UTC-3",
            )

    @patch("calendar_connector.calendar_connector.calendar_connect")
    def test_list_week_events_uses_primary_calendar(self, mock_connect):
        fake_service = _FakeService()
        mock_connect.return_value = fake_service

        with patch(
            "calendar_connector.calendar_connector.now_in_configured_timezone",
            return_value=datetime.datetime(2026, 3, 5, 18, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
        ):
            events = calendar_connector.list_week_events(
                project_logger=_MockLogger(),
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Event primary")
        call_kwargs = fake_service.events().calls[0]
        self.assertEqual(call_kwargs["timeMin"], "2026-03-05T21:00:00Z")
        self.assertEqual(call_kwargs["timeMax"], "2026-03-12T21:00:00Z")

    @patch("calendar_connector.calendar_connector.calendar_connect")
    def test_list_current_week_events_uses_primary_calendar(self, mock_connect):
        fake_service = _FakeService()
        mock_connect.return_value = fake_service

        events = calendar_connector.list_current_week_events(
            project_logger=_MockLogger(),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Event primary")
        call_kwargs = fake_service.events().calls[0]
        self.assertEqual(call_kwargs["calendarId"], "primary")
        self.assertIn("timeMin", call_kwargs)
        self.assertIn("timeMax", call_kwargs)

    @patch("calendar_connector.calendar_connector.calendar_connect")
    def test_list_current_week_events_on_sunday_uses_next_six_days(self, mock_connect):
        fake_service = _FakeService()
        mock_connect.return_value = fake_service

        with patch(
            "calendar_connector.calendar_connector.today_in_configured_timezone",
            return_value=datetime.date(2026, 3, 1),
        ), patch(
            "calendar_connector.calendar_connector.get_configured_timezone",
            return_value=ZoneInfo("America/Sao_Paulo"),
        ):
            calendar_connector.list_current_week_events(project_logger=_MockLogger())

        call_kwargs = fake_service.events().calls[0]
        self.assertEqual(call_kwargs["timeMin"], "2026-03-01T03:00:00Z")
        self.assertEqual(call_kwargs["timeMax"], "2026-03-08T03:00:00Z")

    @patch("calendar_connector.calendar_connector.calendar_connect")
    def test_list_upcoming_events_uses_date_field_when_datetime_missing(self, mock_connect):
        class _UpcomingEventsService(_FakeEventsService):
            def list(self, **kwargs):
                self.calls.append(kwargs)
                return _FakeExecute(
                    {
                        "items": [
                            {
                                "id": "id-upcoming",
                                "summary": "All day",
                                "start": {"date": "2026-03-10"},
                            }
                        ]
                    }
                )

        class _UpcomingService:
            def __init__(self):
                self._events = _UpcomingEventsService()

            def events(self):
                return self._events

        fake_service = _UpcomingService()
        mock_connect.return_value = fake_service
        with patch(
            "calendar_connector.calendar_connector.now_in_configured_timezone",
            return_value=datetime.datetime(2026, 3, 5, 18, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
        ):
            events = calendar_connector.list_upcoming_events(project_logger=_MockLogger(), max_results=3)

        self.assertEqual(events[0]["start"], "2026-03-10")
        call_kwargs = fake_service.events().calls[0]
        self.assertEqual(call_kwargs["maxResults"], 3)
        self.assertEqual(call_kwargs["timeMin"], "2026-03-05T21:00:00Z")

    @patch("calendar_connector.calendar_connector.calendar_connect")
    def test_create_calendar_event_builds_payload_and_returns_created_event(self, mock_connect):
        fake_service = _FakeService()
        mock_connect.return_value = fake_service

        created = calendar_connector.create_calendar_event(
            project_logger=_MockLogger(),
            summary="Kickoff",
            start_datetime="2026-03-06T10:00",
            end_datetime="2026-03-06T11:00",
            description="Alinhamento",
            timezone="America/Sao_Paulo",
        )

        self.assertEqual(created["id"], "created-id")
        self.assertEqual(created["summary"], "Kickoff")
        insert_call = fake_service.events().calls[0]
        self.assertEqual(insert_call["calendarId"], "primary")
        self.assertEqual(insert_call["body"]["description"], "Alinhamento")

    @patch("calendar_connector.calendar_connector.calendar_connect")
    def test_create_calendar_event_rejects_invalid_range(self, mock_connect):
        mock_connect.return_value = _FakeService()
        with self.assertRaises(ValueError):
            calendar_connector.create_calendar_event(
                project_logger=_MockLogger(),
                summary="Kickoff",
                start_datetime="2026-03-06T11:00",
                end_datetime="2026-03-06T10:00",
                timezone="America/Sao_Paulo",
            )


if __name__ == "__main__":
    unittest.main()
