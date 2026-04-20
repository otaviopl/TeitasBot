from __future__ import annotations

import sqlite3
import threading
from typing import Callable

from assistant_connector.service import AssistantService


class AssistantScheduledTaskRunner:
    def __init__(
        self,
        *,
        assistant_service_factory: Callable[[], AssistantService],
        project_logger,
        poll_interval_seconds: float = 5.0,
        stale_running_after_seconds: int = 300,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 900,
        on_task_succeeded: Callable[[dict], None] | None = None,
    ):
        self._assistant_service_factory = assistant_service_factory
        self._project_logger = project_logger
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._stale_running_after_seconds = max(1, int(stale_running_after_seconds))
        self._retry_base_seconds = max(1, int(retry_base_seconds))
        self._retry_max_seconds = max(self._retry_base_seconds, int(retry_max_seconds))
        self._on_task_succeeded = on_task_succeeded
        self._stop_event = threading.Event()
        self._thread = None
        self._start_stop_lock = threading.Lock()

    def start(self) -> None:
        with self._start_stop_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="assistant-scheduled-task-runner",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        with self._start_stop_lock:
            thread = self._thread
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=max(float(timeout_seconds), 0.1))

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop_event.is_set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                service = self._assistant_service_factory()
                if hasattr(service, "execute_next_scheduled_task"):
                    outcome = service.execute_next_scheduled_task(
                        stale_running_after_seconds=self._stale_running_after_seconds,
                        retry_base_seconds=self._retry_base_seconds,
                        retry_max_seconds=self._retry_max_seconds,
                    )
                    processed = bool(outcome.get("processed"))
                    if (
                        processed
                        and outcome.get("status") == "succeeded"
                        and self._on_task_succeeded is not None
                    ):
                        self._on_task_succeeded(outcome)
                else:
                    processed = service.run_scheduled_tasks_once(
                        stale_running_after_seconds=self._stale_running_after_seconds,
                        retry_base_seconds=self._retry_base_seconds,
                        retry_max_seconds=self._retry_max_seconds,
                    )
                if processed:
                    continue
            except (RuntimeError, ValueError, sqlite3.Error) as error:
                if self._project_logger is not None:
                    self._project_logger.exception("Scheduled task polling failed: %s", error)
            except Exception as callback_error:
                if self._project_logger is not None:
                    self._project_logger.exception("Scheduled task callback failed: %s", callback_error)
            self._stop_event.wait(self._poll_interval_seconds)
