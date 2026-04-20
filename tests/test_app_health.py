import unittest
from unittest.mock import patch

from assistant_connector import app_health


class TestAppHealth(unittest.TestCase):
    def test_mark_app_started_sets_starting_status(self):
        app_health.set_bot_status("running")
        app_health.set_task_checker_status("running")

        app_health.mark_app_started()
        snapshot = app_health.get_health_snapshot()

        self.assertEqual(snapshot["bot_status"], "starting")
        self.assertEqual(snapshot["task_checker_status"], "stopped")
        self.assertGreaterEqual(snapshot["uptime_seconds"], 0)

    def test_status_setters_use_unknown_for_blank_values(self):
        app_health.set_bot_status("   ")
        app_health.set_task_checker_status("")
        snapshot = app_health.get_health_snapshot()

        self.assertEqual(snapshot["bot_status"], "unknown")
        self.assertEqual(snapshot["task_checker_status"], "unknown")

    def test_snapshot_never_returns_negative_uptime(self):
        with patch("assistant_connector.app_health.time.time", return_value=0):
            app_health.mark_app_started()
        with patch("assistant_connector.app_health.time.time", return_value=-50):
            snapshot = app_health.get_health_snapshot()
        self.assertEqual(snapshot["uptime_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
