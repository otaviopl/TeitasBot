"""Chart file cleanup utility.

Removes PNG files older than a configurable threshold from the charts directory
to prevent unbounded disk usage over time.
"""
from __future__ import annotations

import logging
import os
import time

_CHARTS_ENV_VAR = "ASSISTANT_CHARTS_DIR"
_CHARTS_SUBDIR = "assistant_charts"
_DEFAULT_MAX_AGE_DAYS = 7
_logger = logging.getLogger(__name__)


def _get_charts_dir() -> str:
    base = os.getenv(_CHARTS_ENV_VAR, "").strip()
    if not base:
        import tempfile
        base = os.path.join(tempfile.gettempdir(), _CHARTS_SUBDIR)
    return base


def clean_old_charts(
    *,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    charts_dir: str | None = None,
) -> dict[str, int]:
    """Delete chart PNG files older than *max_age_days* from the charts directory.

    Args:
        max_age_days: Files last modified more than this many days ago are deleted.
                      Must be >= 1; values below 1 are clamped to 1.
        charts_dir: Override the directory to scan. Defaults to ASSISTANT_CHARTS_DIR
                    env var or the system temp sub-directory.

    Returns:
        A dict with keys ``deleted`` (count) and ``errors`` (count).
    """
    safe_max_age = max(1, int(max_age_days))
    target_dir = charts_dir or _get_charts_dir()

    if not os.path.isdir(target_dir):
        return {"deleted": 0, "errors": 0}

    cutoff = time.time() - safe_max_age * 86400
    deleted = 0
    errors = 0

    for entry in os.scandir(target_dir):
        if not entry.is_file():
            continue
        if not entry.name.lower().endswith(".png"):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue

        if mtime < cutoff:
            try:
                os.remove(entry.path)
                deleted += 1
                _logger.debug("Removed old chart file: %s", entry.path)
            except OSError as exc:
                errors += 1
                _logger.warning("Failed to remove chart file %s: %s", entry.path, exc)

    if deleted or errors:
        _logger.info("Chart cleanup: deleted=%d errors=%d dir=%s", deleted, errors, target_dir)

    return {"deleted": deleted, "errors": errors}
