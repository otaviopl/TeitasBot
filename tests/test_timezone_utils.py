import datetime
import unittest
from unittest.mock import patch

from utils import timezone_utils


class _FixedDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        base = cls(2026, 3, 2, 1, 30, tzinfo=datetime.timezone.utc)
        if tz is None:
            return base.replace(tzinfo=None)
        return base.astimezone(tz)


class TestTimezoneUtils(unittest.TestCase):
    def test_get_configured_timezone_falls_back_to_utc_when_invalid(self):
        with patch.dict("os.environ", {"TIMEZONE": "Invalid/Timezone"}, clear=False), patch(
            "utils.timezone_utils.datetime.datetime",
            _FixedDateTime,
        ):
            self.assertEqual(timezone_utils.get_configured_timezone_name(), "UTC")
            self.assertEqual(timezone_utils.today_iso_in_configured_timezone(), "2026-03-02")

    def test_get_configured_timezone_supports_utc_offset_syntax(self):
        with patch.dict("os.environ", {"TIMEZONE": "GMT-3"}, clear=False):
            self.assertEqual(timezone_utils.get_configured_timezone_name(), "UTC-03:00")

    def test_today_uses_configured_iana_timezone(self):
        with patch.dict("os.environ", {"TIMEZONE": "America/Sao_Paulo"}, clear=False), patch(
            "utils.timezone_utils.datetime.datetime",
            _FixedDateTime,
        ):
            self.assertEqual(timezone_utils.today_in_configured_timezone().isoformat(), "2026-03-01")
            self.assertEqual(timezone_utils.today_iso_in_configured_timezone(), "2026-03-01")

    def test_build_time_context_uses_local_timezone_for_day_boundary(self):
        with patch.dict("os.environ", {"TIMEZONE": "America/Sao_Paulo"}, clear=False), patch(
            "utils.timezone_utils.datetime.datetime",
            _FixedDateTime,
        ):
            context = timezone_utils.build_time_context()
        self.assertEqual(context["timezone_name"], "America/Sao_Paulo")
        self.assertEqual(context["utc_now_iso"], "2026-03-02T01:30:00Z")
        self.assertEqual(context["local_date_iso"], "2026-03-01")
        self.assertTrue(context["local_now_iso"].startswith("2026-03-01T22:30:00-03:00"))
        self.assertEqual(context["local_utc_offset"], "-03:00")

    def test_invalid_timezone_logs_warning_once(self):
        with patch.object(timezone_utils, "_WARNED_INVALID_TIMEZONES", set()), patch.dict(
            "os.environ",
            {"TIMEZONE": "Invalid/Timezone"},
            clear=False,
        ), patch.object(timezone_utils._LOGGER, "warning") as mock_warning:
            self.assertEqual(timezone_utils.get_configured_timezone_name(), "UTC")
            self.assertEqual(timezone_utils.get_configured_timezone_name(), "UTC")
        mock_warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
