import os
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timezone

from assistant_connector.memory_store import ConversationMemoryStore
from assistant_connector.models import ChatResponse
from assistant_connector.scheduler import AssistantScheduledTaskRunner
from assistant_connector.service import AssistantService


class _FakeLogger:
    def exception(self, *_args, **_kwargs):
        return None


class _FakeRuntime:
    def __init__(self, memory_store, *, should_fail=False):
        self._memory_store = memory_store
        self._should_fail = should_fail
        self.calls = []

    def process_user_message(self, **kwargs):
        self.calls.append(kwargs)
        if self._should_fail:
            raise RuntimeError("planned failure")
        return ChatResponse(text="scheduled-ok")

    def reset_session(self, **_kwargs):
        return None


class TestAssistantScheduler(unittest.TestCase):
    def test_run_scheduled_task_retries_then_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=True)
            service = AssistantService(runtime=runtime)
            task_id = service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id=None,
                message="retry me",
                scheduled_for="2026-01-01T10:00:00Z",
                max_attempts=2,
            )

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-01T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)
            first_state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(first_state["status"], "retrying")
            self.assertEqual(first_state["attempt_count"], 1)
            self.assertEqual(first_state["next_attempt_at"], "2026-01-01T10:01:00Z")

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-01T10:01:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)
            second_state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(second_state["status"], "failed")
            self.assertEqual(second_state["attempt_count"], 2)

    def test_runner_executes_in_background_thread(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=False)
            service = AssistantService(runtime=runtime)
            task_id = service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id="g1",
                message="hello from scheduler",
                scheduled_for=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                max_attempts=1,
            )

            runner = AssistantScheduledTaskRunner(
                assistant_service_factory=lambda: service,
                project_logger=_FakeLogger(),
                poll_interval_seconds=0.1,
            )
            runner.start()
            try:
                deadline = time.time() + 2
                while time.time() < deadline:
                    state = memory_store.get_scheduled_task(task_id)
                    if state["status"] == "succeeded":
                        break
                    time.sleep(0.05)
                final_state = memory_store.get_scheduled_task(task_id)
                self.assertEqual(final_state["status"], "succeeded")
                self.assertEqual(len(runtime.calls), 1)
                self.assertIn("hello from scheduler", runtime.calls[0]["message"])
                self.assertIn("execução automática de tarefa agendada", runtime.calls[0]["message"])
                self.assertIn(":scheduled:", runtime.calls[0]["session_id"])
                self.assertTrue(runtime.calls[0]["session_id"].endswith(task_id))
            finally:
                runner.stop()

    def test_run_scheduled_task_requeues_when_success_status_update_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=False)
            service = AssistantService(runtime=runtime)
            task_id = service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id=None,
                message="run and requeue",
                scheduled_for="2026-01-01T10:00:00Z",
                max_attempts=3,
            )

            with unittest.mock.patch.object(
                memory_store,
                "mark_scheduled_task_succeeded",
                side_effect=RuntimeError("write failed"),
            ):
                processed = service.run_scheduled_tasks_once(
                    now_utc="2026-01-01T10:00:00Z",
                    retry_base_seconds=60,
                    retry_max_seconds=60,
                )

            self.assertEqual(processed, 1)
            state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(state["status"], "retrying")
            self.assertIn("post_execution_status_update_failed", state["last_error"])

    def test_runner_calls_success_callback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=False)
            service = AssistantService(runtime=runtime)
            service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id="g1",
                message="deliver me",
                scheduled_for=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                max_attempts=1,
            )
            callback = mock.Mock()
            runner = AssistantScheduledTaskRunner(
                assistant_service_factory=lambda: service,
                project_logger=_FakeLogger(),
                poll_interval_seconds=0.1,
                on_task_succeeded=callback,
            )
            runner.start()
            try:
                deadline = time.time() + 2
                while time.time() < deadline and callback.call_count == 0:
                    time.sleep(0.05)
            finally:
                runner.stop()
            self.assertEqual(callback.call_count, 1)

    def test_run_scheduled_task_daily_recurrence_runs_once_per_day(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=False)
            service = AssistantService(runtime=runtime)
            task_id = service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id=None,
                message="daily task",
                scheduled_for="2026-01-01T10:00:00Z",
                recurrence_pattern="daily",
                max_attempts=1,
            )

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-01T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)
            state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(state["status"], "pending")
            self.assertEqual(state["scheduled_for"], "2026-01-01T10:00:00Z")
            self.assertEqual(state["last_success_at"], "2026-01-01T10:00:00Z")
            self.assertEqual(state["attempt_count"], 0)

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-01T18:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 0)

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-02T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)
            self.assertEqual(len(runtime.calls), 2)

    def test_run_scheduled_task_weekly_recurrence_runs_by_rule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=False)
            service = AssistantService(runtime=runtime)
            task_id = service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id=None,
                message="weekly task",
                scheduled_for="2026-01-01T10:00:00Z",
                recurrence_pattern="weekly",
                max_attempts=1,
            )

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-01T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)
            state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(state["status"], "pending")
            self.assertEqual(state["scheduled_for"], "2026-01-01T10:00:00Z")
            self.assertEqual(state["last_success_at"], "2026-01-01T10:00:00Z")

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-07T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 0)
            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-08T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)

    def test_run_scheduled_task_monthly_recurrence_runs_by_rule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=False)
            service = AssistantService(runtime=runtime)
            task_id = service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id=None,
                message="monthly task",
                scheduled_for="2026-01-31T10:00:00Z",
                recurrence_pattern="monthly",
                max_attempts=1,
            )

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-31T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)
            state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(state["status"], "pending")
            self.assertEqual(state["scheduled_for"], "2026-01-31T10:00:00Z")
            self.assertEqual(state["last_success_at"], "2026-01-31T10:00:00Z")

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-02-27T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 0)
            processed = service.run_scheduled_tasks_once(
                now_utc="2026-02-28T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)

    def test_recurring_task_failed_cycle_runs_again_next_period(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_store = ConversationMemoryStore(os.path.join(temp_dir, "assistant_memory.sqlite3"))
            runtime = _FakeRuntime(memory_store, should_fail=True)
            service = AssistantService(runtime=runtime)
            task_id = service.schedule_chat(
                user_id="u1",
                channel_id="c1",
                guild_id=None,
                message="daily retry cycle",
                scheduled_for="2026-01-01T10:00:00Z",
                recurrence_pattern="daily",
                max_attempts=1,
            )

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-01T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)
            first_state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(first_state["status"], "failed")

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-01T11:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 0)

            processed = service.run_scheduled_tasks_once(
                now_utc="2026-01-02T10:00:00Z",
                retry_base_seconds=60,
                retry_max_seconds=60,
            )
            self.assertEqual(processed, 1)


if __name__ == "__main__":
    unittest.main()
