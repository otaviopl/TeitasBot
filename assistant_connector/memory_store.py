from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

SUPPORTED_RECURRENCE_PATTERNS = {"none", "daily", "weekly", "monthly"}


class ConversationMemoryStore:
    def __init__(
        self,
        db_path: str,
        *,
        max_messages_per_session: int = 300,
        max_tool_calls_per_session: int = 300,
        max_message_chars: int = 4000,
        max_tool_payload_chars: int = 12000,
    ):
        self._db_path = os.path.abspath(db_path)
        self._lock = threading.Lock()
        self._max_messages_per_session = max(1, int(max_messages_per_session))
        self._max_tool_calls_per_session = max(1, int(max_tool_calls_per_session))
        self._max_message_chars = max(200, int(max_message_chars))
        self._max_tool_payload_chars = max(500, int(max_tool_payload_chars))
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._ensure_schema()

    @property
    def db_path(self) -> str:
        return self._db_path

    def log_memory_edit(
        self,
        user_id: str,
        file_name: str,
        action: str,
        chars_written: int = 0,
        source: str = "user",
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_audit_log (user_id, file_name, action, chars_written, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(user_id), str(file_name), str(action), int(chars_written), str(source)),
            )

    def append_message(self, session_id: str, role: str, content: str) -> None:
        safe_content = self._truncate_text(str(content), self._max_message_chars)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_messages (session_id, role, content)
                VALUES (?, ?, ?)
                """,
                (session_id, role, safe_content),
            )
            self._prune_conversation_messages(connection, session_id)
            connection.commit()

    def get_recent_messages(self, session_id: str, limit: int) -> list[dict[str, str]]:
        safe_limit = max(1, int(limit))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT role, content
                FROM conversation_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, safe_limit),
            )
            rows = cursor.fetchall()
        rows.reverse()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def log_tool_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        arguments_json = self._truncate_text(
            json.dumps(arguments, ensure_ascii=False),
            self._max_tool_payload_chars,
        )
        result_json = self._truncate_text(
            json.dumps(result, ensure_ascii=False),
            self._max_tool_payload_chars,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls (session_id, tool_name, arguments_json, result_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    tool_name,
                    arguments_json,
                    result_json,
                ),
            )
            self._prune_tool_calls(connection, session_id)
            connection.commit()

    def clear_session(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM conversation_messages WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM tool_calls WHERE session_id = ?",
                (session_id,),
            )
            connection.commit()

    def create_metabolism_record(
        self,
        *,
        user_id: str,
        bmr: float,
        tdee: float | None = None,
        activity_factor: float | None = None,
        weight_kg: float | None = None,
        height_cm: float | None = None,
        age: int | None = None,
        sex: str | None = None,
        body_fat_percentage: float | None = None,
        source: str = "assistant",
        notes: str = "",
        measured_at: str | None = None,
    ) -> dict[str, Any]:
        clean_user_id = str(user_id or "").strip()
        if not clean_user_id:
            raise ValueError("user_id is required")

        safe_bmr = float(bmr)
        if safe_bmr <= 0:
            raise ValueError("bmr must be greater than zero")

        safe_tdee = float(tdee) if tdee is not None else None
        if safe_tdee is not None and safe_tdee <= 0:
            raise ValueError("tdee must be greater than zero when provided")

        safe_activity_factor = float(activity_factor) if activity_factor is not None else None
        if safe_activity_factor is not None and safe_activity_factor <= 0:
            raise ValueError("activity_factor must be greater than zero when provided")

        safe_weight_kg = float(weight_kg) if weight_kg is not None else None
        if safe_weight_kg is not None and safe_weight_kg <= 0:
            raise ValueError("weight_kg must be greater than zero when provided")

        safe_height_cm = float(height_cm) if height_cm is not None else None
        if safe_height_cm is not None and safe_height_cm <= 0:
            raise ValueError("height_cm must be greater than zero when provided")

        safe_age = int(age) if age is not None else None
        if safe_age is not None and safe_age <= 0:
            raise ValueError("age must be greater than zero when provided")

        safe_body_fat_percentage = (
            float(body_fat_percentage) if body_fat_percentage is not None else None
        )
        if safe_body_fat_percentage is not None and not (0 < safe_body_fat_percentage < 100):
            raise ValueError("body_fat_percentage must be between 0 and 100 when provided")

        safe_source = str(source or "assistant").strip().lower() or "assistant"
        safe_notes = str(notes or "").strip()
        safe_sex = str(sex or "").strip().lower() or None
        safe_measured_at = (
            self._normalize_utc_iso(measured_at)
            if measured_at is not None
            else self._utc_now_iso()
        )
        created_at = self._utc_now_iso()

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO metabolism_history (
                    user_id,
                    measured_at,
                    source,
                    bmr,
                    tdee,
                    activity_factor,
                    weight_kg,
                    height_cm,
                    age,
                    sex,
                    body_fat_percentage,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_user_id,
                    safe_measured_at,
                    self._truncate_text(safe_source, 64),
                    round(safe_bmr, 2),
                    round(safe_tdee, 2) if safe_tdee is not None else None,
                    round(safe_activity_factor, 4) if safe_activity_factor is not None else None,
                    round(safe_weight_kg, 2) if safe_weight_kg is not None else None,
                    round(safe_height_cm, 2) if safe_height_cm is not None else None,
                    safe_age,
                    self._truncate_text(safe_sex, 32) if safe_sex else None,
                    (
                        round(safe_body_fat_percentage, 2)
                        if safe_body_fat_percentage is not None
                        else None
                    ),
                    self._truncate_text(safe_notes, self._max_message_chars),
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM metabolism_history WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            connection.commit()
        return dict(row) if row is not None else {}

    def list_metabolism_history(
        self,
        *,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        clean_user_id = str(user_id or "").strip()
        if not clean_user_id:
            raise ValueError("user_id is required")
        safe_limit = min(max(int(limit), 1), 100)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM metabolism_history
                WHERE user_id = ?
                ORDER BY measured_at DESC, id DESC
                LIMIT ?
                """,
                (clean_user_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_metabolism_record(self, *, user_id: str) -> dict[str, Any] | None:
        rows = self.list_metabolism_history(user_id=user_id, limit=1)
        return rows[0] if rows else None

    def create_scheduled_task(
        self,
        *,
        user_id: str,
        channel_id: str,
        guild_id: str | None,
        message: str,
        scheduled_for: str,
        scheduled_timezone: str = "UTC",
        notify_email_to: str = "",
        recurrence_pattern: str = "none",
        max_attempts: int = 3,
        task_type: str = "general",
    ) -> str:
        clean_message = str(message).strip()
        if not clean_message:
            raise ValueError("Scheduled task message cannot be empty")
        safe_max_attempts = max(1, int(max_attempts))
        safe_scheduled_timezone = str(scheduled_timezone or "UTC").strip() or "UTC"
        safe_notify_email_to = str(notify_email_to or "").strip()
        safe_recurrence_pattern = self._normalize_recurrence_pattern(recurrence_pattern)
        safe_task_type = str(task_type or "general").strip().lower() or "general"
        now_utc = self._normalize_utc_iso(self._utc_now_iso())
        scheduled_for_utc = self._normalize_utc_iso(scheduled_for)
        initial_last_success_at = None
        if safe_recurrence_pattern != "none":
            schedule_timezone = self._resolve_timezone_name(safe_scheduled_timezone)
            base_local = datetime.fromisoformat(scheduled_for_utc.replace("Z", "+00:00")).astimezone(schedule_timezone)
            now_local = datetime.fromisoformat(now_utc.replace("Z", "+00:00")).astimezone(schedule_timezone)
            if self._is_same_recurrence_period(base_local, now_local, safe_recurrence_pattern):
                occurrence_start = self._compute_current_occurrence_start_utc(
                    base_scheduled_for=scheduled_for_utc,
                    recurrence_pattern=safe_recurrence_pattern,
                    timezone_name=safe_scheduled_timezone,
                    reference_utc=now_utc,
                )
                if occurrence_start and occurrence_start < now_utc:
                    initial_last_success_at = occurrence_start
        task_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_tasks (
                    task_id,
                    user_id,
                    channel_id,
                    guild_id,
                    message,
                    scheduled_timezone,
                    notify_email_to,
                    recurrence_pattern,
                    task_type,
                    status,
                    attempt_count,
                    max_attempts,
                    scheduled_for,
                    next_attempt_at,
                    created_at,
                    updated_at,
                    last_success_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    str(user_id),
                    str(channel_id),
                    str(guild_id) if guild_id is not None else None,
                    self._truncate_text(clean_message, self._max_message_chars),
                    self._truncate_text(safe_scheduled_timezone, 64),
                    self._truncate_text(safe_notify_email_to, 320),
                    safe_recurrence_pattern,
                    safe_task_type,
                    safe_max_attempts,
                    scheduled_for_utc,
                    scheduled_for_utc,
                    now_utc,
                    now_utc,
                    initial_last_success_at,
                ),
            )
            connection.commit()
        return task_id

    def claim_next_scheduled_task(
        self,
        *,
        now_utc: str,
        stale_running_after_seconds: int,
    ) -> dict[str, Any] | None:
        safe_now_utc = self._normalize_utc_iso(now_utc)
        stale_before = self._shift_utc_iso(safe_now_utc, -max(1, int(stale_running_after_seconds)))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'retrying',
                    next_attempt_at = ?,
                    locked_at = NULL,
                    updated_at = ?,
                    last_error = CASE
                        WHEN last_error = '' THEN 'Recovered from stale running state'
                        ELSE last_error
                    END
                WHERE status = 'running'
                  AND locked_at IS NOT NULL
                  AND locked_at <= ?
                  AND attempt_count < max_attempts
                """,
                (safe_now_utc, safe_now_utc, stale_before),
            )
            connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'failed',
                    finished_at = ?,
                    locked_at = NULL,
                    updated_at = ?,
                    last_error = CASE
                        WHEN last_error = '' THEN 'Stale running task exceeded retry attempts'
                        ELSE last_error
                    END
                WHERE status = 'running'
                  AND locked_at IS NOT NULL
                  AND locked_at <= ?
                  AND attempt_count >= max_attempts
                """,
                (safe_now_utc, safe_now_utc, stale_before),
            )
            rows = connection.execute(
                """
                SELECT *
                FROM scheduled_tasks
                WHERE status IN ('pending', 'retrying', 'failed')
                ORDER BY
                    CASE
                        WHEN status = 'retrying' THEN 0
                        WHEN status = 'pending' THEN 1
                        ELSE 2
                    END,
                    next_attempt_at ASC,
                    created_at ASC
                LIMIT 200
                """
            ).fetchall()
            selected_task = None
            selected_due_at = None
            for row in rows:
                task = dict(row)
                due_at = self._resolve_task_due_at(task, safe_now_utc)
                if due_at is None:
                    continue
                if selected_due_at is None or due_at < selected_due_at:
                    selected_task = task
                    selected_due_at = due_at
                elif due_at == selected_due_at and selected_task is not None:
                    if str(task.get("created_at", "")) < str(selected_task.get("created_at", "")):
                        selected_task = task
                        selected_due_at = due_at

            if selected_task is None:
                connection.commit()
                return None
            task_id = selected_task["task_id"]
            updated = connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'running',
                    attempt_count = CASE WHEN status = 'failed' THEN 1 ELSE attempt_count + 1 END,
                    started_at = ?,
                    finished_at = NULL,
                    locked_at = ?,
                    updated_at = ?,
                    last_error = CASE WHEN status = 'failed' THEN '' ELSE last_error END
                WHERE task_id = ?
                  AND status IN ('pending', 'retrying', 'failed')
                """,
                (safe_now_utc, safe_now_utc, safe_now_utc, task_id),
            )
            if updated.rowcount != 1:
                connection.commit()
                return None
            claimed_row = connection.execute(
                "SELECT * FROM scheduled_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            connection.commit()
        return dict(claimed_row) if claimed_row is not None else None

    def mark_scheduled_task_succeeded(
        self,
        *,
        task_id: str,
        finished_at: str,
        response_text: str,
    ) -> bool:
        safe_finished_at = self._normalize_utc_iso(finished_at)
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'succeeded',
                    finished_at = ?,
                    locked_at = NULL,
                    updated_at = ?,
                    last_error = '',
                    last_response = ?,
                    last_success_at = ?
                WHERE task_id = ?
                  AND status = 'running'
                """,
                (
                    safe_finished_at,
                    safe_finished_at,
                    self._truncate_text(str(response_text), self._max_tool_payload_chars),
                    safe_finished_at,
                    task_id,
                ),
            )
            connection.commit()
        return updated.rowcount == 1

    def mark_scheduled_task_recurring_succeeded(
        self,
        *,
        task_id: str,
        finished_at: str,
        response_text: str,
    ) -> bool:
        safe_finished_at = self._normalize_utc_iso(finished_at)
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'pending',
                    attempt_count = 0,
                    finished_at = ?,
                    locked_at = NULL,
                    updated_at = ?,
                    last_error = '',
                    last_response = ?,
                    last_success_at = ?
                WHERE task_id = ?
                  AND status = 'running'
                """,
                (
                    safe_finished_at,
                    safe_finished_at,
                    self._truncate_text(str(response_text), self._max_tool_payload_chars),
                    safe_finished_at,
                    task_id,
                ),
            )
            connection.commit()
        return updated.rowcount == 1

    def mark_scheduled_task_retrying(
        self,
        *,
        task_id: str,
        retry_at: str,
        updated_at: str,
        error_text: str,
    ) -> bool:
        safe_retry_at = self._normalize_utc_iso(retry_at)
        safe_updated_at = self._normalize_utc_iso(updated_at)
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'retrying',
                    next_attempt_at = ?,
                    locked_at = NULL,
                    updated_at = ?,
                    last_error = ?
                WHERE task_id = ?
                  AND status = 'running'
                """,
                (
                    safe_retry_at,
                    safe_updated_at,
                    self._truncate_text(str(error_text), self._max_message_chars),
                    task_id,
                ),
            )
            connection.commit()
        return updated.rowcount == 1

    def mark_scheduled_task_failed(
        self,
        *,
        task_id: str,
        finished_at: str,
        error_text: str,
    ) -> bool:
        safe_finished_at = self._normalize_utc_iso(finished_at)
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'failed',
                    finished_at = ?,
                    locked_at = NULL,
                    updated_at = ?,
                    last_error = ?
                WHERE task_id = ?
                  AND status = 'running'
                """,
                (
                    safe_finished_at,
                    safe_finished_at,
                    self._truncate_text(str(error_text), self._max_message_chars),
                    task_id,
                ),
            )
            connection.commit()
        return updated.rowcount == 1

    def get_scheduled_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_scheduled_tasks(
        self,
        *,
        limit: int = 20,
        statuses: list[str] | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 100)
        filters = []
        params: list[Any] = []
        if statuses:
            normalized = [str(status).strip().lower() for status in statuses if str(status).strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                filters.append(f"status IN ({placeholders})")
                params.extend(normalized)
        if user_id is not None and str(user_id).strip():
            filters.append("user_id = ?")
            params.append(str(user_id))

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = (
            "SELECT * FROM scheduled_tasks "
            f"{where_clause} "
            "ORDER BY "
            "CASE WHEN status IN ('pending', 'retrying', 'running') THEN 0 ELSE 1 END, "
            "next_attempt_at ASC, updated_at DESC "
            "LIMIT ?"
        )
        params.append(safe_limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def update_scheduled_task(
        self,
        *,
        task_id: str,
        updated_at: str,
        message: str | None = None,
        scheduled_for: str | None = None,
        scheduled_timezone: str | None = None,
        notify_email_to: str | None = None,
        recurrence_pattern: str | None = None,
        max_attempts: int | None = None,
        task_type: str | None = None,
    ) -> bool:
        set_clauses = ["updated_at = ?"]
        params: list[Any] = [self._normalize_utc_iso(updated_at)]
        if message is not None:
            clean_message = str(message).strip()
            if not clean_message:
                raise ValueError("Scheduled task message cannot be empty")
            set_clauses.append("message = ?")
            params.append(self._truncate_text(clean_message, self._max_message_chars))
        if scheduled_for is not None:
            normalized_scheduled_for = self._normalize_utc_iso(scheduled_for)
            set_clauses.append("scheduled_for = ?")
            set_clauses.append("next_attempt_at = ?")
            set_clauses.append("last_success_at = NULL")
            params.extend([normalized_scheduled_for, normalized_scheduled_for])
        if scheduled_timezone is not None:
            safe_scheduled_timezone = str(scheduled_timezone).strip()
            if not safe_scheduled_timezone:
                raise ValueError("scheduled_timezone cannot be empty")
            set_clauses.append("scheduled_timezone = ?")
            params.append(self._truncate_text(safe_scheduled_timezone, 64))
        if notify_email_to is not None:
            set_clauses.append("notify_email_to = ?")
            params.append(self._truncate_text(str(notify_email_to).strip(), 320))
        if recurrence_pattern is not None:
            set_clauses.append("recurrence_pattern = ?")
            params.append(self._normalize_recurrence_pattern(recurrence_pattern))
            set_clauses.append("last_success_at = NULL")
        if max_attempts is not None:
            safe_max_attempts = max(1, int(max_attempts))
            set_clauses.append("max_attempts = ?")
            params.append(safe_max_attempts)
        if task_type is not None:
            set_clauses.append("task_type = ?")
            params.append(str(task_type).strip().lower() or "general")

        if len(set_clauses) == 1:
            return False

        params.append(task_id)
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                f"""
                UPDATE scheduled_tasks
                SET {", ".join(set_clauses)}
                WHERE task_id = ?
                  AND status IN ('pending', 'retrying')
                """,
                tuple(params),
            )
            connection.commit()
        return updated.rowcount == 1

    def cancel_scheduled_task(
        self,
        *,
        task_id: str,
        cancelled_at: str,
        reason: str = "",
    ) -> bool:
        safe_cancelled_at = self._normalize_utc_iso(cancelled_at)
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE scheduled_tasks
                SET
                    status = 'cancelled',
                    finished_at = ?,
                    locked_at = NULL,
                    updated_at = ?,
                    last_error = ?
                WHERE task_id = ?
                  AND status IN ('pending', 'retrying', 'running')
                """,
                (
                    safe_cancelled_at,
                    safe_cancelled_at,
                    self._truncate_text(
                        str(reason or "Cancelled by user"),
                        self._max_message_chars,
                    ),
                    task_id,
                ),
            )
            connection.commit()
        return updated.rowcount == 1

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_utc_iso(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Expected UTC ISO timestamp")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _shift_utc_iso(base_timestamp: str, delta_seconds: int) -> str:
        normalized = ConversationMemoryStore._normalize_utc_iso(base_timestamp)
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        shifted = parsed + timedelta(seconds=int(delta_seconds))
        return shifted.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _resolve_task_due_at(self, task: dict[str, Any], now_utc: str) -> str | None:
        status = str(task.get("status", "")).strip().lower()
        try:
            recurrence = self._normalize_recurrence_pattern(task.get("recurrence_pattern"))
        except ValueError:
            return None
        safe_now_utc = self._normalize_utc_iso(now_utc)

        if status == "retrying":
            retry_at = self._normalize_utc_iso(str(task.get("next_attempt_at", "")))
            return retry_at if retry_at <= safe_now_utc else None

        if recurrence == "none":
            if status != "pending":
                return None
            next_attempt_at = self._normalize_utc_iso(str(task.get("next_attempt_at", "")))
            return next_attempt_at if next_attempt_at <= safe_now_utc else None

        if status not in {"pending", "failed"}:
            return None
        try:
            occurrence_start = self._compute_current_occurrence_start_utc(
                base_scheduled_for=str(task.get("scheduled_for", "")),
                recurrence_pattern=recurrence,
                timezone_name=str(task.get("scheduled_timezone", "UTC")),
                reference_utc=safe_now_utc,
            )
        except ValueError:
            return None
        if occurrence_start is None:
            return None

        last_success_raw = task.get("last_success_at")
        if last_success_raw not in (None, ""):
            last_success_at = self._normalize_utc_iso(str(last_success_raw))
        else:
            last_success_at = ""
        if last_success_at and last_success_at >= occurrence_start:
            return None
        if status == "failed":
            finished_raw = task.get("finished_at")
            if finished_raw not in (None, ""):
                finished_at = self._normalize_utc_iso(str(finished_raw))
            else:
                finished_at = ""
            if finished_at and finished_at >= occurrence_start:
                return None
        return occurrence_start

    @staticmethod
    def _resolve_timezone_name(timezone_value: str):
        requested = str(timezone_value or "").strip() or "UTC"
        gmt_match = re.fullmatch(r"(?:GMT|UTC)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", requested, re.IGNORECASE)
        if gmt_match:
            signal = 1 if gmt_match.group(1) == "+" else -1
            hours = int(gmt_match.group(2))
            minutes = int(gmt_match.group(3) or 0)
            if hours > 23 or minutes > 59:
                raise ValueError("Invalid GMT/UTC offset timezone")
            offset = signal * timedelta(hours=hours, minutes=minutes)
            label = f"UTC{gmt_match.group(1)}{hours:02d}:{minutes:02d}"
            return timezone(offset, name=label)
        return ZoneInfo(requested)

    @staticmethod
    def _add_months_preserving_day(base_datetime: datetime, months_to_add: int) -> datetime:
        target_index = (base_datetime.month - 1) + int(months_to_add)
        year = base_datetime.year + target_index // 12
        month = (target_index % 12) + 1
        last_day = calendar.monthrange(year, month)[1]
        day = min(base_datetime.day, last_day)
        return base_datetime.replace(year=year, month=month, day=day)

    @staticmethod
    def _is_same_recurrence_period(base_local: datetime, reference_local: datetime, recurrence_pattern: str) -> bool:
        if recurrence_pattern == "daily":
            return base_local.date() == reference_local.date()
        if recurrence_pattern == "weekly":
            return base_local.isocalendar()[:2] == reference_local.isocalendar()[:2]
        if recurrence_pattern == "monthly":
            return (base_local.year, base_local.month) == (reference_local.year, reference_local.month)
        return False

    def _compute_current_occurrence_start_utc(
        self,
        *,
        base_scheduled_for: str,
        recurrence_pattern: str,
        timezone_name: str,
        reference_utc: str,
    ) -> str | None:
        normalized_base = self._normalize_utc_iso(base_scheduled_for)
        normalized_reference = self._normalize_utc_iso(reference_utc)
        base = datetime.fromisoformat(normalized_base.replace("Z", "+00:00"))
        reference = datetime.fromisoformat(normalized_reference.replace("Z", "+00:00"))
        schedule_timezone = self._resolve_timezone_name(timezone_name)
        base_local = base.astimezone(schedule_timezone)
        reference_local = reference.astimezone(schedule_timezone)
        if reference_local < base_local:
            return None

        candidate = base_local
        if recurrence_pattern == "daily":
            step = max((reference_local.date() - base_local.date()).days, 0)
            candidate = base_local + timedelta(days=step)
            if candidate > reference_local:
                candidate -= timedelta(days=1)
        elif recurrence_pattern == "weekly":
            weeks = max((reference_local.date() - base_local.date()).days // 7, 0)
            candidate = base_local + timedelta(days=weeks * 7)
            if candidate > reference_local:
                candidate -= timedelta(days=7)
        elif recurrence_pattern == "monthly":
            months = max((reference_local.year - base_local.year) * 12 + (reference_local.month - base_local.month), 0)
            candidate = self._add_months_preserving_day(base_local, months)
            if candidate > reference_local:
                candidate = self._add_months_preserving_day(candidate, -1)
        else:
            raise ValueError("Unsupported recurrence_pattern")

        return candidate.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        marker = "... [truncated]"
        if limit <= len(marker):
            return marker[:limit]
        return f"{text[: limit - len(marker)]}{marker}"

    def _prune_conversation_messages(self, connection: sqlite3.Connection, session_id: str) -> None:
        connection.execute(
            """
            DELETE FROM conversation_messages
            WHERE session_id = ?
              AND id NOT IN (
                SELECT id
                FROM conversation_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
              )
            """,
            (
                session_id,
                session_id,
                self._max_messages_per_session,
            ),
        )

    def _prune_tool_calls(self, connection: sqlite3.Connection, session_id: str) -> None:
        connection.execute(
            """
            DELETE FROM tool_calls
            WHERE session_id = ?
              AND id NOT IN (
                SELECT id
                FROM tool_calls
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
              )
            """,
            (
                session_id,
                session_id,
                self._max_tool_calls_per_session,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_conversation_messages_session
                    ON conversation_messages (session_id, id);

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_tool_calls_session
                    ON tool_calls (session_id, id);

                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT,
                    message TEXT NOT NULL,
                    scheduled_timezone TEXT NOT NULL DEFAULT 'UTC',
                    notify_email_to TEXT NOT NULL DEFAULT '',
                    recurrence_pattern TEXT NOT NULL DEFAULT 'none',
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    scheduled_for TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    locked_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    last_response TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT 'general',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status_next_attempt
                    ON scheduled_tasks (status, next_attempt_at);

                CREATE TABLE IF NOT EXISTS metabolism_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    measured_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'assistant',
                    bmr REAL NOT NULL,
                    tdee REAL,
                    activity_factor REAL,
                    weight_kg REAL,
                    height_cm REAL,
                    age INTEGER,
                    sex TEXT,
                    body_fat_percentage REAL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );

                CREATE INDEX IF NOT EXISTS idx_metabolism_history_user_measured_at
                    ON metabolism_history (user_id, measured_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS user_credentials (
                    telegram_user_id TEXT NOT NULL,
                    credential_key   TEXT NOT NULL,
                    credential_value TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    PRIMARY KEY (telegram_user_id, credential_key)
                );

                CREATE TABLE IF NOT EXISTS memory_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    chars_written INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                );

                CREATE INDEX IF NOT EXISTS idx_memory_audit_log_user
                    ON memory_audit_log (user_id, created_at DESC);
                """
            )
            self._ensure_scheduled_tasks_migrations(connection)
            self._ensure_metabolism_history_migrations(connection)
            connection.commit()

    @staticmethod
    def _ensure_scheduled_tasks_migrations(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
        }
        if "scheduled_timezone" not in columns:
            connection.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN scheduled_timezone TEXT NOT NULL DEFAULT 'UTC'"
            )
        if "notify_email_to" not in columns:
            connection.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN notify_email_to TEXT NOT NULL DEFAULT ''"
            )
        if "recurrence_pattern" not in columns:
            connection.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN recurrence_pattern TEXT NOT NULL DEFAULT 'none'"
            )
        if "last_success_at" not in columns:
            connection.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN last_success_at TEXT"
            )
        if "task_type" not in columns:
            connection.execute(
                "ALTER TABLE scheduled_tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'general'"
            )

    @staticmethod
    def _ensure_metabolism_history_migrations(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(metabolism_history)").fetchall()
        }
        if not columns:
            return
        if "source" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN source TEXT NOT NULL DEFAULT 'assistant'"
            )
        if "tdee" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN tdee REAL"
            )
        if "activity_factor" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN activity_factor REAL"
            )
        if "weight_kg" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN weight_kg REAL"
            )
        if "height_cm" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN height_cm REAL"
            )
        if "age" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN age INTEGER"
            )
        if "sex" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN sex TEXT"
            )
        if "body_fat_percentage" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN body_fat_percentage REAL"
            )
        if "notes" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN notes TEXT NOT NULL DEFAULT ''"
            )
        if "created_at" not in columns:
            connection.execute(
                "ALTER TABLE metabolism_history ADD COLUMN created_at TEXT"
            )
        connection.execute(
            """
            UPDATE metabolism_history
            SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE created_at IS NULL OR TRIM(created_at) = ''
            """
        )
        connection.execute(
            """
            UPDATE metabolism_history
            SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at)
            WHERE created_at GLOB '????-??-?? ??:??:??'
            """
        )

    @staticmethod
    def _normalize_recurrence_pattern(value: object | None) -> str:
        normalized = str(value or "none").strip().lower() or "none"
        if normalized not in SUPPORTED_RECURRENCE_PATTERNS:
            raise ValueError("Unsupported recurrence_pattern. Use none, daily, weekly or monthly.")
        return normalized
