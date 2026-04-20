import unittest
from unittest.mock import MagicMock, patch

from assistant_connector.tools import system_tools


class TestSystemTools(unittest.TestCase):
    @patch("assistant_connector.tools.system_tools.os.sysconf", return_value=4096)
    @patch("assistant_connector.tools.system_tools.open", create=True)
    def test_get_process_rss_bytes_prefers_proc_statm(self, mock_open, _mock_sysconf):
        mock_open.return_value.__enter__.return_value.read.return_value = "100 5 0 0 0 0 0"
        self.assertEqual(system_tools._get_process_rss_bytes(), 5 * 4096)

    @patch("assistant_connector.tools.system_tools.resource.getrusage")
    @patch("assistant_connector.tools.system_tools.open", side_effect=OSError("missing"), create=True)
    def test_get_process_rss_bytes_falls_back_to_getrusage(self, _mock_open, mock_getrusage):
        usage = MagicMock()
        usage.ru_maxrss = 321
        mock_getrusage.return_value = usage
        self.assertEqual(system_tools._get_process_rss_bytes(), 321 * 1024)

    @patch("assistant_connector.tools.system_tools.resource.getrusage")
    @patch("assistant_connector.tools.system_tools.open", side_effect=OSError("missing"), create=True)
    def test_get_process_rss_bytes_returns_zero_when_usage_not_available(self, _mock_open, mock_getrusage):
        usage = MagicMock()
        usage.ru_maxrss = 0
        mock_getrusage.return_value = usage
        self.assertEqual(system_tools._get_process_rss_bytes(), 0)

    @patch("assistant_connector.tools.system_tools._get_process_rss_bytes", return_value=5 * 1024 * 1024)
    @patch("assistant_connector.tools.system_tools.app_health.get_health_snapshot")
    def test_get_application_hardware_status_uses_health_snapshot(self, mock_snapshot, _mock_rss):
        mock_snapshot.return_value = {
            "bot_status": "running",
            "task_checker_status": "idle",
            "uptime_seconds": 99,
        }
        payload = system_tools.get_application_hardware_status({}, None)
        self.assertEqual(payload["bot_status"], "running")
        self.assertEqual(payload["task_checker_status"], "idle")
        self.assertEqual(payload["uptime_seconds"], 99)
        self.assertEqual(payload["memory_total_mb"], 5.0)


if __name__ == "__main__":
    unittest.main()
