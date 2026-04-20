from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_STATE = {
    "app_started_at": time.time(),
    "bot_status": "stopped",
    "task_checker_status": "stopped",
}


def mark_app_started() -> None:
    with _LOCK:
        _STATE["app_started_at"] = time.time()
        _STATE["bot_status"] = "starting"
        _STATE["task_checker_status"] = "stopped"


def set_bot_status(status: str) -> None:
    with _LOCK:
        _STATE["bot_status"] = str(status).strip() or "unknown"


def set_task_checker_status(status: str) -> None:
    with _LOCK:
        _STATE["task_checker_status"] = str(status).strip() or "unknown"


def get_health_snapshot() -> dict[str, float | str]:
    with _LOCK:
        started_at = float(_STATE["app_started_at"])
        bot_status = str(_STATE["bot_status"])
        task_checker_status = str(_STATE["task_checker_status"])
    uptime_seconds = max(0, int(time.time() - started_at))
    return {
        "bot_status": bot_status,
        "task_checker_status": task_checker_status,
        "uptime_seconds": uptime_seconds,
    }
