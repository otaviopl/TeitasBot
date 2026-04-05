from __future__ import annotations

import logging
import os
import subprocess

_PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_COPILOT_BIN = os.getenv("COPILOT_CLI_PATH", os.path.expanduser("~/.local/bin/copilot"))
_DEFAULT_TIMEOUT = 300  # 5 minutes
_SERVICE_NAME = "personal-assistant-bot"
_MAX_OUTPUT_CHARS = 6000

logger = logging.getLogger(__name__)


def run_copilot_task(arguments: dict, _context) -> dict:
    """Execute the Copilot CLI in autopilot mode with a given task prompt."""

    task_description = str(arguments.get("task_description", "")).strip()
    if not task_description:
        return {"error": "task_description is required"}

    timeout = int(
        os.getenv("COPILOT_TASK_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT))
    )

    cmd = [
        _COPILOT_BIN,
        "-p", task_description,
        "--yolo",
        "--autopilot",
        "--no-ask-user",
        "-s",
    ]

    logger.info("Running Copilot CLI task: %s", task_description[:120])

    try:
        result = subprocess.run(
            cmd,
            cwd=_PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "error": "copilot_cli_not_found",
            "message": (
                f"Copilot CLI binary not found at '{_COPILOT_BIN}'. "
                "Set COPILOT_CLI_PATH env var to the correct path."
            ),
        }
    except subprocess.TimeoutExpired:
        return {
            "error": "timeout",
            "message": f"Copilot CLI did not finish within {timeout}s.",
        }

    output = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if len(output) > _MAX_OUTPUT_CHARS:
        output = output[-_MAX_OUTPUT_CHARS:] + "\n...(truncated)"

    logger.info(
        "Copilot CLI finished with exit code %d (%d chars output)",
        result.returncode,
        len(output),
    )

    return {
        "exit_code": result.returncode,
        "output": output,
        "stderr": stderr[:1000] if stderr else "",
        "success": result.returncode == 0,
    }


def restart_bot_service(arguments: dict, _context) -> dict:
    """Schedule a delayed restart of the bot systemd service.

    A short delay (3s) is used so the current response can be sent
    to the user before the process is terminated by systemd.
    """

    delay_seconds = int(arguments.get("delay_seconds", 3))
    delay_seconds = max(1, min(delay_seconds, 30))

    cmd = f"sleep {delay_seconds} && sudo systemctl restart {_SERVICE_NAME}"

    logger.info("Scheduling bot service restart in %ds", delay_seconds)

    try:
        subprocess.Popen(
            ["bash", "-c", cmd],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.exception("Failed to schedule restart")
        return {
            "error": "restart_failed",
            "message": str(exc),
        }

    return {
        "status": "restart_scheduled",
        "delay_seconds": delay_seconds,
        "service": _SERVICE_NAME,
    }
