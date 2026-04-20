from __future__ import annotations

import datetime
import logging
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DEFAULT_TIMEZONE_NAME = "UTC"
_OFFSET_PATTERN = re.compile(r"^(UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)
_LOGGER = logging.getLogger(__name__)
_WARNED_INVALID_TIMEZONES: set[str] = set()


def _parse_utc_gmt_offset(raw_value: str):
    match = _OFFSET_PATTERN.fullmatch(raw_value)
    if not match:
        return None

    sign = 1 if match.group(2) == "+" else -1
    hours = int(match.group(3))
    minutes = int(match.group(4) or 0)
    if hours > 23 or minutes > 59:
        return None

    delta = datetime.timedelta(hours=hours, minutes=minutes)
    offset = sign * delta
    normalized_name = f"UTC{match.group(2)}{hours:02d}:{minutes:02d}"
    return normalized_name, datetime.timezone(offset, name=normalized_name)


def _resolve_configured_timezone():
    raw_timezone = str(os.getenv("TIMEZONE", _DEFAULT_TIMEZONE_NAME)).strip()
    if not raw_timezone:
        raw_timezone = _DEFAULT_TIMEZONE_NAME

    try:
        return raw_timezone, ZoneInfo(raw_timezone)
    except ZoneInfoNotFoundError:
        pass

    parsed_offset = _parse_utc_gmt_offset(raw_timezone)
    if parsed_offset is not None:
        return parsed_offset

    if raw_timezone not in _WARNED_INVALID_TIMEZONES:
        _WARNED_INVALID_TIMEZONES.add(raw_timezone)
        _LOGGER.warning(
            "Invalid TIMEZONE value '%s'. Falling back to UTC.",
            raw_timezone,
        )
    return _DEFAULT_TIMEZONE_NAME, datetime.timezone.utc


def get_configured_timezone_name() -> str:
    timezone_name, _ = _resolve_configured_timezone()
    return timezone_name


def get_configured_timezone() -> datetime.tzinfo:
    _, timezone_info = _resolve_configured_timezone()
    return timezone_info


def now_in_configured_timezone() -> datetime.datetime:
    return datetime.datetime.now(get_configured_timezone())


def today_in_configured_timezone() -> datetime.date:
    return now_in_configured_timezone().date()


def today_iso_in_configured_timezone() -> str:
    return today_in_configured_timezone().isoformat()


def build_time_context() -> dict[str, str]:
    timezone_name, configured_timezone = _resolve_configured_timezone()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local = now_utc.astimezone(configured_timezone)
    offset = now_local.utcoffset() or datetime.timedelta()
    total_minutes = int(offset.total_seconds() // 60)
    signal = "+" if total_minutes >= 0 else "-"
    absolute_minutes = abs(total_minutes)
    offset_hours, offset_minutes = divmod(absolute_minutes, 60)
    offset_label = f"{signal}{offset_hours:02d}:{offset_minutes:02d}"
    return {
        "timezone_name": timezone_name,
        "local_now_iso": now_local.replace(microsecond=0).isoformat(),
        "local_date_iso": now_local.date().isoformat(),
        "local_utc_offset": offset_label,
        "utc_now_iso": now_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "utc_date_iso": now_utc.date().isoformat(),
    }
