from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from assistant_connector.memory_store import ConversationMemoryStore

SUPPORTED_RECURRENCE_PATTERNS = {"none", "daily", "weekly", "monthly"}
SUPPORTED_TASK_TYPES = {"general", "logging_reminder"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_memory_store() -> ConversationMemoryStore:
    default_memory_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "assistant_memory.sqlite3")
    )
    resolved_memory_path = os.getenv("ASSISTANT_MEMORY_PATH", default_memory_path)
    return ConversationMemoryStore(resolved_memory_path)


def _resolve_timezone_name(timezone_value: str | None) -> tuple[str, timezone | ZoneInfo]:
    requested = str(timezone_value or "").strip() or str(os.getenv("TIMEZONE", "UTC")).strip() or "UTC"
    gmt_match = re.fullmatch(r"(?:GMT|UTC)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", requested, re.IGNORECASE)
    if gmt_match:
        signal = 1 if gmt_match.group(1) == "+" else -1
        hours = int(gmt_match.group(2))
        minutes = int(gmt_match.group(3) or 0)
        if hours > 23 or minutes > 59:
            raise ValueError("Invalid GMT/UTC offset timezone")
        offset = signal * timedelta(hours=hours, minutes=minutes)
        label = f"UTC{gmt_match.group(1)}{hours:02d}:{minutes:02d}"
        return label, timezone(offset, name=label)
    try:
        return requested, ZoneInfo(requested)
    except Exception as error:
        raise ValueError(f"Invalid timezone: {requested}") from error


def _normalize_scheduled_time(raw_value: str, timezone_hint: str | None) -> tuple[str, str]:
    text = str(raw_value or "").strip()
    if not text:
        raise ValueError("scheduled_for is required")
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        utc_value = parsed.astimezone(timezone.utc)
        timezone_name = str(parsed.tzinfo)
    else:
        timezone_name, tz_info = _resolve_timezone_name(timezone_hint)
        utc_value = parsed.replace(tzinfo=tz_info).astimezone(timezone.utc)
    utc_iso = utc_value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return utc_iso, timezone_name


def _can_manage_task_owner(*, task_user_id: str, context_user_id: str) -> bool:
    if str(task_user_id) == str(context_user_id):
        return True
    authorized_user_id = str(os.getenv("TELEGRAM_ALLOWED_USER_ID", "")).strip()
    if authorized_user_id and str(context_user_id) == authorized_user_id:
        return True
    if not str(task_user_id).strip() and authorized_user_id and str(context_user_id) == authorized_user_id:
        return True
    return False


def _normalize_recurrence_pattern(value: object | None) -> str:
    normalized = str(value or "none").strip().lower() or "none"
    if normalized not in SUPPORTED_RECURRENCE_PATTERNS:
        raise ValueError("recurrence must be one of: none, daily, weekly, monthly")
    return normalized


def _normalize_task_type(value: object | None) -> str:
    normalized = str(value or "general").strip().lower() or "general"
    if normalized not in SUPPORTED_TASK_TYPES:
        raise ValueError("task_type must be one of: general, logging_reminder")
    return normalized


def _coalesce_identifier(value: object, fallback: object) -> str:
    candidate = str(value if value is not None else "").strip()
    if candidate:
        return candidate
    return str(fallback if fallback is not None else "").strip()


def create_scheduled_task(arguments, context):
    message = str(arguments.get("message", "")).strip()
    if not message:
        raise ValueError("message is required")
    scheduled_for = str(arguments.get("scheduled_for", "")).strip()
    if not scheduled_for:
        raise ValueError("scheduled_for is required")
    timezone_hint = arguments.get("timezone")
    normalized_scheduled_for, effective_timezone = _normalize_scheduled_time(scheduled_for, timezone_hint)

    user_id = _coalesce_identifier(arguments.get("user_id"), context.user_id)
    channel_id = _coalesce_identifier(arguments.get("channel_id"), context.channel_id)
    guild_id = context.guild_id
    max_attempts = max(1, int(arguments.get("max_attempts", 3)))
    notify_email_to = str(arguments.get("notify_email_to", "")).strip()
    recurrence_pattern = _normalize_recurrence_pattern(arguments.get("recurrence"))
    task_type = _normalize_task_type(arguments.get("task_type"))

    memory_store = _build_memory_store()
    task_id = memory_store.create_scheduled_task(
        user_id=user_id,
        channel_id=channel_id,
        guild_id=guild_id,
        message=message,
        scheduled_for=normalized_scheduled_for,
        scheduled_timezone=effective_timezone,
        notify_email_to=notify_email_to,
        recurrence_pattern=recurrence_pattern,
        max_attempts=max_attempts,
        task_type=task_type,
    )
    task = memory_store.get_scheduled_task(task_id)
    return {
        "status": "created",
        "task": task,
    }


def list_scheduled_tasks(arguments, context):
    limit = min(max(int(arguments.get("limit", 20)), 1), 100)
    raw_statuses = arguments.get("statuses")
    statuses = None
    if isinstance(raw_statuses, str):
        statuses = [raw_statuses]
    elif isinstance(raw_statuses, list):
        statuses = [str(item) for item in raw_statuses]
    include_all_users = bool(arguments.get("include_all_users", False))
    context_user_id = str(context.user_id)
    memory_store = _build_memory_store()
    if include_all_users:
        tasks = memory_store.list_scheduled_tasks(
            limit=limit,
            statuses=statuses,
            user_id=None,
        )
    else:
        tasks = memory_store.list_scheduled_tasks(
            limit=limit,
            statuses=statuses,
            user_id=context_user_id,
        )
        if not tasks and _can_manage_task_owner(task_user_id="", context_user_id=context_user_id):
            fallback_tasks = memory_store.list_scheduled_tasks(
                limit=100,
                statuses=statuses,
                user_id=None,
            )
            tasks = [task for task in fallback_tasks if not str(task.get("user_id", "")).strip()][:limit]
    return {
        "total": len(tasks),
        "tasks": tasks,
    }


def edit_scheduled_task(arguments, context):
    task_id = str(arguments.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("task_id is required")

    memory_store = _build_memory_store()
    existing = memory_store.get_scheduled_task(task_id)
    if existing is None:
        raise ValueError("scheduled task not found")
    if not _can_manage_task_owner(
        task_user_id=str(existing["user_id"]),
        context_user_id=str(context.user_id),
    ):
        raise ValueError("you can only edit your own scheduled tasks")

    message = arguments.get("message")
    scheduled_for = arguments.get("scheduled_for")
    timezone_hint = arguments.get("timezone")
    max_attempts = arguments.get("max_attempts")
    notify_email_to = arguments.get("notify_email_to")
    recurrence_pattern = arguments.get("recurrence")
    raw_task_type = arguments.get("task_type")
    normalized_scheduled_for = None
    effective_timezone = None
    if scheduled_for is not None:
        normalized_scheduled_for, effective_timezone = _normalize_scheduled_time(str(scheduled_for), timezone_hint)
    elif timezone_hint is not None:
        effective_timezone = str(timezone_hint).strip()
        if not effective_timezone:
            raise ValueError("timezone cannot be empty")
    updated = memory_store.update_scheduled_task(
        task_id=task_id,
        updated_at=_utc_now_iso(),
        message=str(message) if message is not None else None,
        scheduled_for=normalized_scheduled_for,
        scheduled_timezone=effective_timezone,
        notify_email_to=str(notify_email_to).strip() if notify_email_to is not None else None,
        recurrence_pattern=(
            _normalize_recurrence_pattern(recurrence_pattern)
            if recurrence_pattern is not None
            else None
        ),
        max_attempts=int(max_attempts) if max_attempts is not None else None,
        task_type=(
            _normalize_task_type(raw_task_type)
            if raw_task_type is not None
            else None
        ),
    )
    if not updated:
        raise ValueError("scheduled task cannot be edited in current status")

    return {
        "status": "updated",
        "task": memory_store.get_scheduled_task(task_id),
    }


def cancel_scheduled_task(arguments, context):
    task_id = str(arguments.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("task_id is required")

    memory_store = _build_memory_store()
    existing = memory_store.get_scheduled_task(task_id)
    if existing is None:
        raise ValueError("scheduled task not found")
    if not _can_manage_task_owner(
        task_user_id=str(existing["user_id"]),
        context_user_id=str(context.user_id),
    ):
        raise ValueError("you can only cancel your own scheduled tasks")

    reason = str(arguments.get("reason", "")).strip()
    cancelled = memory_store.cancel_scheduled_task(
        task_id=task_id,
        cancelled_at=_utc_now_iso(),
        reason=reason,
    )
    if not cancelled:
        raise ValueError("scheduled task cannot be cancelled in current status")

    return {
        "status": "cancelled",
        "task": memory_store.get_scheduled_task(task_id),
    }
