import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from assistant_connector.memory_store import ConversationMemoryStore


class TestConversationMemoryStore(unittest.TestCase):
    @staticmethod
    def _utc_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def test_append_and_read_recent_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)

            memory_store.append_message("session-1", "user", "olá")
            memory_store.append_message("session-1", "assistant", "oi, tudo bem?")

            messages = memory_store.get_recent_messages("session-1", limit=10)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[1]["role"], "assistant")

    def test_log_tool_call_persists_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)

            memory_store.log_tool_call(
                session_id="session-1",
                tool_name="list_available_tools",
                arguments={"foo": "bar"},
                result={"ok": True},
            )

            with sqlite3.connect(db_path) as connection:
                cursor = connection.execute(
                    "SELECT tool_name, arguments_json, result_json FROM tool_calls"
                )
                row = cursor.fetchone()

            self.assertEqual(row[0], "list_available_tools")
            self.assertIn('"foo": "bar"', row[1])
            self.assertIn('"ok": true', row[2].lower())

    def test_prunes_old_messages_per_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(
                db_path,
                max_messages_per_session=2,
            )
            memory_store.append_message("session-1", "user", "m1")
            memory_store.append_message("session-1", "assistant", "m2")
            memory_store.append_message("session-1", "user", "m3")

            messages = memory_store.get_recent_messages("session-1", limit=10)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["content"], "m2")
            self.assertEqual(messages[1]["content"], "m3")

    def test_prunes_old_tool_calls_per_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(
                db_path,
                max_tool_calls_per_session=2,
            )
            memory_store.log_tool_call("session-1", "tool", {"n": 1}, {"ok": True})
            memory_store.log_tool_call("session-1", "tool", {"n": 2}, {"ok": True})
            memory_store.log_tool_call("session-1", "tool", {"n": 3}, {"ok": True})

            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT arguments_json FROM tool_calls WHERE session_id='session-1' ORDER BY id"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertIn('"n": 2', rows[0][0])
            self.assertIn('"n": 3', rows[1][0])

    def test_truncates_long_message_and_tool_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(
                db_path,
                max_message_chars=200,
                max_tool_payload_chars=500,
            )
            memory_store.append_message("session-1", "user", "x" * 500)
            memory_store.log_tool_call("session-1", "tool", {"text": "a" * 2000}, {"ok": "b" * 2000})

            messages = memory_store.get_recent_messages("session-1", limit=1)
            self.assertLessEqual(len(messages[0]["content"]), 200)

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    "SELECT arguments_json, result_json FROM tool_calls ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self.assertLessEqual(len(row[0]), 500)
            self.assertLessEqual(len(row[1]), 500)

    def test_clear_session_removes_messages_and_tool_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)
            memory_store.append_message("session-1", "user", "oi")
            memory_store.log_tool_call("session-1", "tool", {"a": 1}, {"ok": True})

            memory_store.clear_session("session-1")

            self.assertEqual(memory_store.get_recent_messages("session-1", limit=10), [])
            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM tool_calls WHERE session_id='session-1'"
                ).fetchone()
            self.assertEqual(row[0], 0)

    def test_scheduled_task_lifecycle_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)
            now = self._utc_iso()
            task_id = memory_store.create_scheduled_task(
                user_id="user-1",
                channel_id="channel-1",
                guild_id="guild-1",
                message="Ping assistant",
                scheduled_for=now,
                max_attempts=2,
            )

            claimed = memory_store.claim_next_scheduled_task(
                now_utc=now,
                stale_running_after_seconds=60,
            )
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["task_id"], task_id)
            self.assertEqual(claimed["status"], "running")
            self.assertEqual(claimed["attempt_count"], 1)

            self.assertTrue(
                memory_store.mark_scheduled_task_succeeded(
                    task_id=task_id,
                    finished_at=now,
                    response_text="Done",
                )
            )
            persisted = memory_store.get_scheduled_task(task_id)
            self.assertEqual(persisted["status"], "succeeded")
            self.assertEqual(persisted["last_response"], "Done")
            self.assertEqual(persisted["scheduled_timezone"], "UTC")
            self.assertEqual(persisted["notify_email_to"], "")
            self.assertEqual(persisted["recurrence_pattern"], "none")

    def test_claim_recovers_stale_running_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)
            task_id = memory_store.create_scheduled_task(
                user_id="user-1",
                channel_id="channel-1",
                guild_id=None,
                message="Ping assistant",
                scheduled_for="2026-01-01T10:00:00Z",
                max_attempts=3,
            )

            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'running', locked_at = '2026-01-01T09:00:00Z', attempt_count = 1
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )
                connection.commit()

            claimed = memory_store.claim_next_scheduled_task(
                now_utc="2026-01-01T10:10:00Z",
                stale_running_after_seconds=60,
            )
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["task_id"], task_id)
            self.assertEqual(claimed["status"], "running")
            self.assertEqual(claimed["attempt_count"], 2)

    def test_list_update_and_cancel_scheduled_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)
            task_id = memory_store.create_scheduled_task(
                user_id="user-1",
                channel_id="channel-1",
                guild_id=None,
                message="Mensagem inicial",
                scheduled_for="2026-01-01T10:00:00Z",
                notify_email_to="user@example.com",
                max_attempts=3,
            )

            listed = memory_store.list_scheduled_tasks(limit=10, user_id="user-1")
            self.assertTrue(any(task["task_id"] == task_id for task in listed))

            updated = memory_store.update_scheduled_task(
                task_id=task_id,
                updated_at="2026-01-01T09:00:00Z",
                message="Mensagem editada",
                scheduled_for="2026-01-01T11:00:00Z",
                scheduled_timezone="America/Sao_Paulo",
                notify_email_to="new@example.com",
                max_attempts=4,
                recurrence_pattern="weekly",
            )
            self.assertTrue(updated)
            persisted = memory_store.get_scheduled_task(task_id)
            self.assertEqual(persisted["message"], "Mensagem editada")
            self.assertEqual(persisted["scheduled_for"], "2026-01-01T11:00:00Z")
            self.assertEqual(persisted["scheduled_timezone"], "America/Sao_Paulo")
            self.assertEqual(persisted["notify_email_to"], "new@example.com")
            self.assertEqual(persisted["max_attempts"], 4)
            self.assertEqual(persisted["recurrence_pattern"], "weekly")

            cancelled = memory_store.cancel_scheduled_task(
                task_id=task_id,
                cancelled_at="2026-01-01T09:10:00Z",
            )
            self.assertTrue(cancelled)
            cancelled_task = memory_store.get_scheduled_task(task_id)
            self.assertEqual(cancelled_task["status"], "cancelled")

    def test_recurring_task_claims_once_per_period(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)
            task_id = memory_store.create_scheduled_task(
                user_id="user-1",
                channel_id="channel-1",
                guild_id=None,
                message="Mensagem recorrente",
                scheduled_for="2026-01-01T10:00:00Z",
                recurrence_pattern="daily",
            )
            memory_store.claim_next_scheduled_task(
                now_utc="2026-01-01T10:00:00Z",
                stale_running_after_seconds=60,
            )
            memory_store.mark_scheduled_task_recurring_succeeded(
                task_id=task_id,
                finished_at="2026-01-01T10:00:00Z",
                response_text="ok",
            )
            first_state = memory_store.get_scheduled_task(task_id)
            self.assertEqual(first_state["status"], "pending")
            self.assertEqual(first_state["scheduled_for"], "2026-01-01T10:00:00Z")
            self.assertEqual(first_state["last_success_at"], "2026-01-01T10:00:00Z")

            second_claim = memory_store.claim_next_scheduled_task(
                now_utc="2026-01-01T18:00:00Z",
                stale_running_after_seconds=60,
            )
            self.assertIsNone(second_claim)

            third_claim = memory_store.claim_next_scheduled_task(
                now_utc="2026-01-02T10:00:00Z",
                stale_running_after_seconds=60,
            )
            self.assertIsNotNone(third_claim)
            self.assertEqual(third_claim["task_id"], task_id)

    def test_create_recurring_task_in_past_skips_current_elapsed_cycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)
            with unittest.mock.patch.object(
                memory_store,
                "_utc_now_iso",
                return_value="2026-03-05T11:50:00Z",
            ):
                task_id = memory_store.create_scheduled_task(
                    user_id="user-1",
                    channel_id="channel-1",
                    guild_id=None,
                    message="Resumo diário",
                    scheduled_for="2026-03-05T10:00:00Z",
                    recurrence_pattern="daily",
                )

            persisted = memory_store.get_scheduled_task(task_id)
            self.assertEqual(persisted["last_success_at"], "2026-03-05T10:00:00Z")
            no_claim_today = memory_store.claim_next_scheduled_task(
                now_utc="2026-03-05T11:50:00Z",
                stale_running_after_seconds=60,
            )
            self.assertIsNone(no_claim_today)
            next_claim = memory_store.claim_next_scheduled_task(
                now_utc="2026-03-06T10:00:00Z",
                stale_running_after_seconds=60,
            )
            self.assertIsNotNone(next_claim)
            self.assertEqual(next_claim["task_id"], task_id)

    def test_metabolism_history_records_and_returns_latest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            memory_store = ConversationMemoryStore(db_path)

            first = memory_store.create_metabolism_record(
                user_id="user-1",
                bmr=1700,
                activity_factor=1.55,
                tdee=2635,
                weight_kg=80,
                height_cm=180,
                age=33,
                sex="male",
                measured_at="2026-03-01T10:00:00Z",
                source="assistant",
            )
            second = memory_store.create_metabolism_record(
                user_id="user-1",
                bmr=1680,
                activity_factor=1.55,
                tdee=2604,
                weight_kg=79,
                height_cm=180,
                age=33,
                sex="male",
                measured_at="2026-03-08T10:00:00Z",
                source="assistant",
                notes="After 1 week",
            )

            history = memory_store.list_metabolism_history(user_id="user-1", limit=10)
            latest = memory_store.get_latest_metabolism_record(user_id="user-1")

            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["id"], second["id"])
            self.assertEqual(history[1]["id"], first["id"])
            self.assertEqual(latest["id"], second["id"])
            self.assertEqual(latest["notes"], "After 1 week")

    def test_metabolism_migration_normalizes_created_at_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE metabolism_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        measured_at TEXT NOT NULL,
                        bmr REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO metabolism_history (user_id, measured_at, bmr)
                    VALUES ('user-1', '2026-03-01T10:00:00Z', 1700)
                    """
                )
                connection.commit()

            memory_store = ConversationMemoryStore(db_path)
            row = memory_store.list_metabolism_history(user_id="user-1", limit=1)[0]

            self.assertIn("T", row["created_at"])
            self.assertTrue(row["created_at"].endswith("Z"))


class TestMemoryAuditLogStore(unittest.TestCase):
    def test_log_memory_edit_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            store = ConversationMemoryStore(db_path)

            store.log_memory_edit(
                user_id="user-1",
                file_name="health.md",
                action="appended",
                chars_written=42,
                source="user",
            )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM memory_audit_log WHERE user_id = ?", ("user-1",)
            ).fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["file_name"], "health.md")
            self.assertEqual(rows[0]["action"], "appended")
            self.assertEqual(rows[0]["chars_written"], 42)
            self.assertEqual(rows[0]["source"], "user")
            self.assertTrue(rows[0]["created_at"].endswith("Z"))

    def test_log_memory_edit_multiple_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            store = ConversationMemoryStore(db_path)

            store.log_memory_edit("u1", "a.md", "replaced", 10)
            store.log_memory_edit("u1", "b.md", "appended", 20)
            store.log_memory_edit("u2", "a.md", "appended", 5)

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM memory_audit_log").fetchone()[0]
            conn.close()
            self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
