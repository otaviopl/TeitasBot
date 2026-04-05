import unittest
from unittest.mock import MagicMock, patch, call

from assistant_connector.models import AgentDefinition, ToolExecutionContext
from assistant_connector.tools import dev_tools

OWNER_ID = "123456"


class _FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def exception(self, *_args, **_kwargs):
        return None


def _build_context(user_id=OWNER_ID):
    agent = AgentDefinition(
        agent_id="personal_assistant",
        description="desc",
        model="model",
        system_prompt="prompt",
        tools=[],
    )
    return ToolExecutionContext(
        session_id="session",
        user_id=user_id,
        channel_id="channel",
        guild_id="guild",
        project_logger=_FakeLogger(),
        agent=agent,
        available_tools=[],
        available_agents=[],
    )


class TestOwnerGuard(unittest.TestCase):
    """Verify that both tools reject non-owner callers."""

    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_run_copilot_task_rejects_non_owner(self):
        result = dev_tools.run_copilot_task(
            {"task_description": "hack"}, _build_context(user_id="intruder")
        )
        self.assertEqual(result["error"], "unauthorized")

    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_restart_bot_service_rejects_non_owner(self):
        result = dev_tools.restart_bot_service({}, _build_context(user_id="intruder"))
        self.assertEqual(result["error"], "unauthorized")

    @patch.object(dev_tools, "_OWNER_USER_ID", "")
    def test_run_copilot_task_rejects_when_env_not_set(self):
        result = dev_tools.run_copilot_task(
            {"task_description": "anything"}, _build_context()
        )
        self.assertEqual(result["error"], "not_configured")

    @patch.object(dev_tools, "_OWNER_USER_ID", "")
    def test_restart_bot_service_rejects_when_env_not_set(self):
        result = dev_tools.restart_bot_service({}, _build_context())
        self.assertEqual(result["error"], "not_configured")

    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_context_user_id_is_immutable(self):
        ctx = _build_context()
        with self.assertRaises(AttributeError):
            ctx.user_id = "attacker"


def _mock_popen(stdout="", stderr="", returncode=0):
    """Build a MagicMock that behaves like subprocess.Popen."""
    proc = MagicMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    proc.pid = 99999
    return proc


class TestRunCopilotTask(unittest.TestCase):
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_missing_task_description_returns_error(self):
        result = dev_tools.run_copilot_task({}, _build_context())
        self.assertIn("error", result)

    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_empty_task_description_returns_error(self):
        result = dev_tools.run_copilot_task({"task_description": "  "}, _build_context())
        self.assertIn("error", result)

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_successful_execution_returns_output(self, mock_popen):
        mock_popen.return_value = _mock_popen(
            stdout="Changes applied successfully",
        )
        result = dev_tools.run_copilot_task(
            {"task_description": "Fix the bug"},
            _build_context(),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("Changes applied", result["output"])

        args, kwargs = mock_popen.call_args
        cmd = args[0]
        self.assertIn("-p", cmd)
        self.assertIn("Fix the bug", cmd)
        self.assertIn("--yolo", cmd)
        self.assertIn("--autopilot", cmd)
        self.assertIn("--no-ask-user", cmd)
        self.assertTrue(kwargs.get("start_new_session"))

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_nonzero_exit_code_returns_failure(self, mock_popen):
        mock_popen.return_value = _mock_popen(
            stdout="Error occurred",
            stderr="fatal: something",
            returncode=1,
        )
        result = dev_tools.run_copilot_task(
            {"task_description": "Bad task"},
            _build_context(),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("fatal", result["stderr"])

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_binary_not_found_returns_error(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError("No such file")
        result = dev_tools.run_copilot_task(
            {"task_description": "Some task"},
            _build_context(),
        )
        self.assertEqual(result["error"], "copilot_cli_not_found")

    @patch("assistant_connector.tools.dev_tools.os.killpg")
    @patch("assistant_connector.tools.dev_tools.os.getpgid", return_value=99999)
    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_timeout_returns_error(self, mock_popen, mock_getpgid, mock_killpg):
        import subprocess as _subprocess

        mock_proc = _mock_popen()
        mock_proc.communicate.side_effect = _subprocess.TimeoutExpired(
            cmd="copilot", timeout=300
        )
        mock_popen.return_value = mock_proc
        result = dev_tools.run_copilot_task(
            {"task_description": "Long task"},
            _build_context(),
        )
        self.assertEqual(result["error"], "timeout")
        mock_killpg.assert_called()

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_long_output_is_truncated(self, mock_popen):
        big_output = "x" * 10000
        mock_popen.return_value = _mock_popen(stdout=big_output)
        result = dev_tools.run_copilot_task(
            {"task_description": "Big output task"},
            _build_context(),
        )
        self.assertTrue(result["success"])
        self.assertLessEqual(len(result["output"]), dev_tools._MAX_OUTPUT_CHARS + 50)
        self.assertTrue(result["output"].startswith("...(truncated)"))

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_long_stderr_is_truncated(self, mock_popen):
        big_stderr = "e" * 2000
        mock_popen.return_value = _mock_popen(stderr=big_stderr)
        result = dev_tools.run_copilot_task(
            {"task_description": "Stderr task"},
            _build_context(),
        )
        self.assertTrue(result["stderr"].endswith("...(truncated)"))
        self.assertLessEqual(len(result["stderr"]), 1050)

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_uses_project_dir_as_cwd(self, mock_popen):
        mock_popen.return_value = _mock_popen(stdout="ok")
        dev_tools.run_copilot_task(
            {"task_description": "Check cwd"},
            _build_context(),
        )
        _, kwargs = mock_popen.call_args
        self.assertEqual(kwargs["cwd"], dev_tools._PROJECT_DIR)


class TestRestartBotService(unittest.TestCase):
    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_schedules_restart_with_default_delay(self, mock_popen):
        result = dev_tools.restart_bot_service({}, _build_context())
        self.assertEqual(result["status"], "restart_scheduled")
        self.assertEqual(result["delay_seconds"], 3)
        mock_popen.assert_called_once()
        cmd_arg = mock_popen.call_args[0][0]
        self.assertIn("sleep 3", " ".join(cmd_arg))

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_custom_delay_is_clamped(self, mock_popen):
        result = dev_tools.restart_bot_service(
            {"delay_seconds": 50}, _build_context()
        )
        self.assertEqual(result["delay_seconds"], 30)

        result = dev_tools.restart_bot_service(
            {"delay_seconds": -5}, _build_context()
        )
        self.assertEqual(result["delay_seconds"], 1)

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_popen_failure_returns_error(self, mock_popen):
        mock_popen.side_effect = OSError("Permission denied")
        result = dev_tools.restart_bot_service({}, _build_context())
        self.assertEqual(result["error"], "restart_failed")
        self.assertIn("Permission denied", result["message"])

    @patch("assistant_connector.tools.dev_tools.subprocess.Popen")
    @patch.object(dev_tools, "_OWNER_USER_ID", OWNER_ID)
    def test_popen_called_with_new_session(self, mock_popen):
        dev_tools.restart_bot_service({}, _build_context())
        _, kwargs = mock_popen.call_args
        self.assertTrue(kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
