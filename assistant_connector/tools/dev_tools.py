from __future__ import annotations

import logging
import os
import signal
import subprocess

_PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_COPILOT_BIN = os.getenv("COPILOT_CLI_PATH", os.path.expanduser("~/.local/bin/copilot"))
_DEFAULT_TIMEOUT = 300  # 5 minutes
_SERVICE_NAME = "personal-assistant-bot"
_MAX_OUTPUT_CHARS = 6000

# Owner user ID loaded once at import time.  Env var is the single source
# of truth and cannot be changed at runtime by the LLM or tool arguments.
_OWNER_USER_ID: str = os.getenv("COPILOT_OWNER_USER_ID", "").strip()

logger = logging.getLogger(__name__)


def _assert_owner(context) -> dict | None:
    """Return an error dict if the caller is not the owner, else None."""
    if not _OWNER_USER_ID:
        return {
            "error": "not_configured",
            "message": "COPILOT_OWNER_USER_ID env var is not set.",
        }
    if str(context.user_id) != _OWNER_USER_ID:
        logger.warning(
            "Blocked dev tool call from non-owner user_id=%s", context.user_id
        )
        return {
            "error": "unauthorized",
            "message": "Only the project owner can use this tool.",
        }
    return None


def run_copilot_task(arguments: dict, context) -> dict:
    """Execute the Copilot CLI in autopilot mode with a given task prompt."""

    if (deny := _assert_owner(context)) is not None:
        return deny

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
        proc = subprocess.Popen(
            cmd,
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {
            "error": "copilot_cli_not_found",
            "message": (
                f"Copilot CLI binary not found at '{_COPILOT_BIN}'. "
                "Set COPILOT_CLI_PATH env var to the correct path."
            ),
        }

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the entire process group to avoid orphan children
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            proc.wait()
        logger.warning("Copilot CLI killed after %ds timeout (pgid=%d)", timeout, pgid)
        return {
            "error": "timeout",
            "message": f"Copilot CLI did not finish within {timeout}s.",
        }

    output = (stdout or "").strip()
    stderr = (stderr or "").strip()

    if len(output) > _MAX_OUTPUT_CHARS:
        output = output[-_MAX_OUTPUT_CHARS:] + "\n...(truncated)"

    logger.info(
        "Copilot CLI finished with exit code %d (%d chars output)",
        proc.returncode,
        len(output),
    )

    return {
        "exit_code": proc.returncode,
        "output": output,
        "stderr": stderr[:1000] if stderr else "",
        "success": proc.returncode == 0,
    }


def restart_bot_service(arguments: dict, context) -> dict:
    """Schedule a delayed restart of the bot systemd service.

    A short delay (3s) is used so the current response can be sent
    to the user before the process is terminated by systemd.
    """

    if (deny := _assert_owner(context)) is not None:
        return deny

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
