from __future__ import annotations

import os
import resource

from assistant_connector import app_health


def _get_process_rss_bytes() -> int:
    statm_path = "/proc/self/statm"
    try:
        with open(statm_path, "r", encoding="utf-8") as statm_file:
            fields = statm_file.read().strip().split()
        if len(fields) >= 2:
            rss_pages = int(fields[1])
            page_size = os.sysconf("SC_PAGE_SIZE")
            return max(0, rss_pages * page_size)
    except (OSError, ValueError):
        pass

    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if usage <= 0:
        return 0
    # Linux ru_maxrss is KiB.
    return int(usage * 1024)


def get_application_hardware_status(_arguments, _context):
    snapshot = app_health.get_health_snapshot()
    rss_bytes = _get_process_rss_bytes()
    return {
        "bot_status": snapshot["bot_status"],
        "task_checker_status": snapshot["task_checker_status"],
        "memory_total_mb": round(rss_bytes / (1024 * 1024), 2),
        "uptime_seconds": snapshot["uptime_seconds"],
    }
