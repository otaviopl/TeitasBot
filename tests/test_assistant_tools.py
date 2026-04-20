import unittest
import os
import tempfile
from unittest.mock import MagicMock, patch

from assistant_connector.models import AgentDefinition, ToolExecutionContext
from assistant_connector.tools import (
    calendar_tools,
    contacts_tools,
    email_tools,
    metabolism_tools,
    meta_tools,
    news_tools,
    scheduled_task_tools,
    system_tools,
)


class _FakeLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def exception(self, *_args, **_kwargs):
        return None


def _build_context(memories_dir=None, user_credential_store=None):
    agent = AgentDefinition(
        agent_id="personal_assistant",
        description="desc",
        model="model",
        system_prompt="prompt",
        tools=[],
    )
    return ToolExecutionContext(
        session_id="session",
        user_id="user",
        channel_id="channel",
        guild_id="guild",
        project_logger=_FakeLogger(),
        agent=agent,
        available_tools=[{"name": "list_tasks"}],
        available_agents=[{"id": "personal_assistant"}],
        memories_dir=memories_dir,
        user_credential_store=user_credential_store,
    )


class TestAssistantTools(unittest.TestCase):
    @patch("assistant_connector.tools.news_tools.urlopen")
    def test_list_tech_news_returns_google_news_and_hn_when_enabled(self, mock_urlopen):
        class _Response:
            def __init__(self, content):
                self._content = content

            def read(self):
                return self._content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        rss_payload = b"""<?xml version="1.0"?>
        <rss><channel>
          <item><title>AI launch</title><link>https://example.com/a</link><pubDate>Mon, 01 Mar 2032 20:00:00 GMT</pubDate><description>startup and innovation</description><source>TechCrunch</source></item>
        </channel></rss>"""
        top_ids_payload = b"[1001]"
        hn_item_payload = (
            b'{"id":1001,"title":"AI startup raises Series A",'
            b'"url":"https://news.ycombinator.com/item?id=1001","time":2000000000}'
        )
        mock_urlopen.side_effect = [
            _Response(rss_payload),
            _Response(top_ids_payload),
            _Response(hn_item_payload),
        ]

        result = news_tools.list_tech_news(
            {"query": "startup AI", "limit": 3, "include_hacker_news": True, "max_age_hours": 999},
            _build_context(),
        )

        self.assertEqual(result["returned"], 2)
        self.assertIn("TechCrunch", result["sources"])
        self.assertTrue(any(item["source"] == "Hacker News" for item in result["news"]))
        self.assertEqual(mock_urlopen.call_count, 3)

    @patch("assistant_connector.tools.news_tools.urlopen")
    def test_list_tech_news_applies_requested_age_cutoff(self, mock_urlopen):
        class _Response:
            def __init__(self, content):
                self._content = content

            def read(self):
                return self._content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        rss_payload = b"""<?xml version="1.0"?>
        <rss><channel>
          <item><title>Tech recap</title><link>https://example.com/old</link><pubDate>Mon, 01 Mar 2021 20:00:00 GMT</pubDate><description>technology</description></item>
        </channel></rss>"""
        mock_urlopen.side_effect = [_Response(rss_payload)]

        result = news_tools.list_tech_news(
            {"limit": 5, "max_age_hours": 6},
            _build_context(),
        )

        self.assertEqual(result["returned"], 0)

    def test_list_tech_news_rejects_invalid_limit(self):
        with self.assertRaisesRegex(ValueError, "limit must be a valid integer"):
            news_tools.list_tech_news({"limit": "many"}, _build_context())

    @patch("assistant_connector.tools.news_tools.urlopen")
    def test_list_news_alias_uses_same_handler(self, mock_urlopen):
        class _Response:
            def __init__(self, content):
                self._content = content

            def read(self):
                return self._content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        rss_payload = b"""<?xml version="1.0"?>
        <rss><channel>
          <item><title>Market update</title><link>https://example.com/market</link><pubDate>Mon, 01 Mar 2032 20:00:00 GMT</pubDate><description>economia global</description></item>
        </channel></rss>"""
        mock_urlopen.side_effect = [_Response(rss_payload)]

        result = news_tools.list_news({"query": "economia"}, _build_context())

        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["query"], "economia")

    def test_calculate_metabolism_profile_uses_mifflin_st_jeor(self):
        result = metabolism_tools.calculate_metabolism_profile(
            {
                "peso_kg": 80,
                "altura_cm": 180,
                "idade": 33,
                "sexo": "masculino",
                "nivel_atividade": "moderado",
            },
            _build_context(),
        )

        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result["formula"], "mifflin_st_jeor")
        self.assertGreater(result["bmr"], 0)
        self.assertGreater(result["tdee"], result["bmr"])

    def test_calculate_metabolism_profile_rejects_invalid_bmr_result(self):
        with self.assertRaisesRegex(ValueError, "Calculated BMR"):
            metabolism_tools.calculate_metabolism_profile(
                {
                    "peso_kg": 1,
                    "altura_cm": 1,
                    "idade": 200,
                    "sexo": "feminino",
                    "nivel_atividade": "sedentario",
                },
                _build_context(),
            )

    def test_register_metabolism_profile_and_read_history(self):
        context = _build_context()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            with patch.dict(
                os.environ,
                {"ASSISTANT_MEMORY_PATH": db_path},
                clear=False,
            ):
                created = metabolism_tools.register_metabolism_profile(
                    {
                        "peso_kg": 82,
                        "altura_cm": 180,
                        "idade": 33,
                        "sexo": "masculino",
                        "fator_atividade": 1.55,
                        "notas": "Primeiro registro",
                    },
                    context,
                )
                history = metabolism_tools.get_metabolism_history({"limit": 5}, context)

        self.assertEqual(created["status"], "created")
        self.assertEqual(created["calculation"]["formula"], "mifflin_st_jeor")
        self.assertEqual(history["total"], 1)
        self.assertIsNotNone(history["latest"])
        self.assertEqual(history["latest"]["notes"], "Primeiro registro")

    def test_register_metabolism_profile_treats_blank_reference_date_as_now(self):
        context = _build_context()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            with patch.dict(
                os.environ,
                {"ASSISTANT_MEMORY_PATH": db_path},
                clear=False,
            ):
                created = metabolism_tools.register_metabolism_profile(
                    {
                        "peso_kg": 75,
                        "altura_cm": 175,
                        "idade": 30,
                        "sexo": "masculino",
                        "fator_atividade": 1.2,
                        "data_referencia": "   ",
                    },
                    context,
                )

        self.assertEqual(created["status"], "created")
        self.assertTrue(created["entry"]["measured_at"].endswith("Z"))

    def test_list_calendar_events_rejects_non_integer_max_results(self):
        with self.assertRaisesRegex(ValueError, "max_results must be a valid integer"):
            calendar_tools.list_calendar_events({"max_results": "muitos"}, _build_context())

    @patch("assistant_connector.tools.calendar_tools.calendar_connector.list_week_events")
    def test_list_calendar_events_clamps_max_results(self, mock_list_events):
        mock_list_events.return_value = [{"id": "1"}]

        result = calendar_tools.list_calendar_events(
            {"max_results": 500},
            _build_context(),
        )

        mock_list_events.assert_called_once_with(project_logger=unittest.mock.ANY, max_results=100, user_id=unittest.mock.ANY, credential_store=None)
        self.assertEqual(result["total"], 1)

    @patch("assistant_connector.tools.calendar_tools.calendar_connector.create_calendar_event")
    def test_create_calendar_event_passes_arguments(self, mock_create_event):
        mock_create_event.return_value = {"id": "event-1"}

        result = calendar_tools.create_calendar_event(
            {
                "summary": "Reunião",
                "start_datetime": "2026-03-03T10:00",
                "end_datetime": "2026-03-03T11:00",
                "description": "Kickoff",
                "timezone": "America/Sao_Paulo",
            },
            _build_context(),
        )

        self.assertEqual(result["id"], "event-1")
        mock_create_event.assert_called_once()

    def test_create_calendar_event_requires_fields(self):
        with self.assertRaises(ValueError):
            calendar_tools.create_calendar_event(
                {"summary": "", "start_datetime": "2026-03-03T10:00", "end_datetime": "2026-03-03T11:00"},
                _build_context(),
            )

    def test_meta_tools_return_context_catalogs(self):
        context = _build_context()
        tools_payload = meta_tools.list_available_tools({}, context)
        agents_payload = meta_tools.list_available_agents({}, context)

        self.assertEqual(tools_payload["agent_id"], "personal_assistant")
        self.assertEqual(agents_payload["active_agent_id"], "personal_assistant")

    def test_scheduled_task_tools_create_list_edit_cancel(self):
        context = _build_context()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            with patch.dict(
                os.environ,
                {"ASSISTANT_MEMORY_PATH": db_path, "TIMEZONE": "America/Sao_Paulo"},
                clear=False,
            ):
                created = scheduled_task_tools.create_scheduled_task(
                    {
                        "message": "Enviar resumo no fim do dia",
                        "scheduled_for": "2026-03-05T20:00:00",
                        "recurrence": "weekly",
                        "max_attempts": 2,
                        "notify_email_to": "user@example.com",
                    },
                    context,
                )
                task_id = created["task"]["task_id"]
                self.assertEqual(created["status"], "created")
                self.assertEqual(created["task"]["status"], "pending")
                self.assertEqual(created["task"]["scheduled_for"], "2026-03-05T23:00:00Z")
                self.assertEqual(created["task"]["scheduled_timezone"], "America/Sao_Paulo")
                self.assertEqual(created["task"]["notify_email_to"], "user@example.com")
                self.assertEqual(created["task"]["recurrence_pattern"], "weekly")

                listed = scheduled_task_tools.list_scheduled_tasks({"limit": 10}, context)
                self.assertGreaterEqual(listed["total"], 1)
                self.assertTrue(any(task["task_id"] == task_id for task in listed["tasks"]))

                edited = scheduled_task_tools.edit_scheduled_task(
                    {
                        "task_id": task_id,
                        "message": "Enviar resumo e próximos passos",
                        "scheduled_for": "2026-03-05T21:00:00",
                        "timezone": "UTC",
                        "notify_email_to": "",
                        "recurrence": "monthly",
                    },
                    context,
                )
                self.assertEqual(edited["status"], "updated")
                self.assertIn("próximos passos", edited["task"]["message"])
                self.assertEqual(edited["task"]["scheduled_for"], "2026-03-05T21:00:00Z")
                self.assertEqual(edited["task"]["scheduled_timezone"], "UTC")
                self.assertEqual(edited["task"]["notify_email_to"], "")
                self.assertEqual(edited["task"]["recurrence_pattern"], "monthly")

                cancelled = scheduled_task_tools.cancel_scheduled_task({"task_id": task_id}, context)
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(cancelled["task"]["status"], "cancelled")

    def test_scheduled_task_tools_allow_authorized_user_to_cancel_any_task(self):
        context = _build_context()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            with patch.dict(
                os.environ,
                {
                    "ASSISTANT_MEMORY_PATH": db_path,
                    "TELEGRAM_ALLOWED_USER_ID": "user",
                },
                clear=False,
            ):
                created = scheduled_task_tools.create_scheduled_task(
                    {
                        "message": "Executar ação",
                        "scheduled_for": "2026-03-05T20:00:00Z",
                        "user_id": "other-user",
                    },
                    context,
                )
                task_id = created["task"]["task_id"]
                cancelled = scheduled_task_tools.cancel_scheduled_task({"task_id": task_id}, context)
                self.assertEqual(cancelled["status"], "cancelled")

    def test_scheduled_task_tools_create_uses_context_when_ids_are_empty(self):
        context = _build_context()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            with patch.dict(os.environ, {"ASSISTANT_MEMORY_PATH": db_path}, clear=False):
                created = scheduled_task_tools.create_scheduled_task(
                    {
                        "message": "Executar ação",
                        "scheduled_for": "2026-03-05T20:00:00Z",
                        "user_id": "",
                        "channel_id": "",
                        "guild_id": "",
                    },
                    context,
                )
                task = created["task"]
                self.assertEqual(task["user_id"], "user")
                self.assertEqual(task["channel_id"], "channel")
                self.assertEqual(task["guild_id"], "guild")

    def test_scheduled_task_tools_list_returns_orphan_for_authorized_user(self):
        context = _build_context()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "assistant_memory.sqlite3")
            with patch.dict(
                os.environ,
                {
                    "ASSISTANT_MEMORY_PATH": db_path,
                    "TELEGRAM_ALLOWED_USER_ID": "user",
                },
                clear=False,
            ):
                memory_store = scheduled_task_tools._build_memory_store()
                task_id = memory_store.create_scheduled_task(
                    user_id="",
                    channel_id="channel",
                    guild_id="guild",
                    message="Executar ação",
                    scheduled_for="2026-03-05T20:00:00Z",
                )
                listed = scheduled_task_tools.list_scheduled_tasks({}, context)
                self.assertTrue(any(task["task_id"] == task_id for task in listed["tasks"]))

    def test_get_application_hardware_status_returns_expected_fields(self):
        with patch.object(
            system_tools.app_health,
            "get_health_snapshot",
            return_value={
                "bot_status": "online",
                "task_checker_status": "running",
                "uptime_seconds": 123,
            },
        ):
            with patch.object(system_tools, "_get_process_rss_bytes", return_value=50 * 1024 * 1024):
                payload = system_tools.get_application_hardware_status({}, _build_context())
        self.assertEqual(payload["bot_status"], "online")
        self.assertEqual(payload["task_checker_status"], "running")
        self.assertEqual(payload["uptime_seconds"], 123)
        self.assertEqual(payload["memory_total_mb"], 50.0)

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_applies_signature_and_prefix(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-1"}
        with patch.dict(
            os.environ,
            {
                "EMAIL_ASSISTANT_SIGNATURE": "Carlos",
                "EMAIL_ASSISTANT_SUBJECT_PREFIX": "[Assistente]",
                "EMAIL_ASSISTANT_TONE": "direto",
            },
            clear=False,
        ):
            result = email_tools.send_email(
                {
                    "subject": "Atualização semanal",
                    "body": "Segue status.",
                    "recipient_email": "x@example.com",
                },
                _build_context(),
            )

        self.assertEqual(result["status"], "sent")
        self.assertTrue(result["signature_applied"])
        self.assertEqual(result["subject"], "[Assistente] Atualização semanal")
        sent_body = mock_send_custom_email.call_args.kwargs["body_text"]
        self.assertIn("Segue status.", sent_body)
        self.assertIn("Carlos", sent_body)

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_always_applies_signature_even_when_flag_is_false(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-1"}
        with patch.dict(os.environ, {"EMAIL_ASSISTANT_SIGNATURE": "Carlos"}, clear=False):
            email_tools.send_email(
                {
                    "subject": "Atualização",
                    "body": "Sem assinatura.",
                    "recipient_email": "x@example.com",
                    "include_signature": False,
                },
                _build_context(),
            )

        sent_body = mock_send_custom_email.call_args.kwargs["body_text"]
        self.assertIn("Carlos", sent_body)

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_does_not_duplicate_existing_signature_in_body(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-1"}
        with patch.dict(os.environ, {"EMAIL_ASSISTANT_SIGNATURE": "Carlos"}, clear=False):
            email_tools.send_email(
                {
                    "subject": "Atualização",
                    "body": "Status do dia.\n\nCarlos",
                    "recipient_email": "x@example.com",
                },
                _build_context(),
            )

        sent_body = mock_send_custom_email.call_args.kwargs["body_text"]
        self.assertEqual(sent_body.count("Carlos"), 1)

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_forwards_reply_to_message_id(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-2", "thread_id": "thread-1"}

        email_tools.send_email(
            {
                "subject": "Re: Atualização",
                "body": "Respondendo no mesmo fio.",
                "recipient_email": "x@example.com",
                "reply_to_message_id": "orig-msg-id",
            },
            _build_context(),
        )

        self.assertEqual(
            mock_send_custom_email.call_args.kwargs["reply_to_message_id"],
            "orig-msg-id",
        )

    def test_send_email_requires_subject_and_body(self):
        with self.assertRaises(ValueError):
            email_tools.send_email(
                {"recipient_email": "x@example.com", "subject": "", "body": "abc"},
                _build_context(),
            )
        with self.assertRaises(ValueError):
            email_tools.send_email(
                {"recipient_email": "x@example.com", "subject": "abc", "body": ""},
                _build_context(),
            )

    def test_send_email_requires_recipient(self):
        with self.assertRaises(ValueError):
            email_tools.send_email(
                {"subject": "abc", "body": "conteúdo"},
                _build_context(),
            )

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_uses_default_recipient_from_env(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-3"}
        with patch.dict(os.environ, {"EMAIL_TO": "default@example.com"}, clear=False):
            email_tools.send_email(
                {"subject": "abc", "body": "conteúdo"},
                _build_context(),
            )
        self.assertEqual(mock_send_custom_email.call_args.kwargs["email_to"], "default@example.com")

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_uses_user_credential_recipient(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-4"}
        credential_store = MagicMock()
        credential_store.get_credential.return_value = "from-store@example.com"

        email_tools.send_email(
            {"subject": "abc", "body": "conteúdo"},
            _build_context(user_credential_store=credential_store),
        )
        self.assertEqual(mock_send_custom_email.call_args.kwargs["email_to"], "from-store@example.com")

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_resolves_contact_alias_to_email(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-5"}
        with tempfile.TemporaryDirectory() as temp_dir:
            contacts_path = os.path.join(temp_dir, "contacts.csv")
            with open(contacts_path, "w", encoding="utf-8") as contacts_file:
                contacts_file.write("Nome,email,telefone,relacionamento\n")
                contacts_file.write("Contato pessoal,pessoal@example.com,16999999999,meu contato pessoal\n")
                contacts_file.write("Contato profissional,work@example.com,16999999998,meu contato profissional\n")

            email_tools.send_email(
                {
                    "recipient_email": "meu email pessoal",
                    "subject": "abc",
                    "body": "conteúdo",
                },
                _build_context(memories_dir=temp_dir),
            )

        self.assertEqual(mock_send_custom_email.call_args.kwargs["email_to"], "pessoal@example.com")

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_raises_when_contact_alias_is_ambiguous(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-6"}
        with tempfile.TemporaryDirectory() as temp_dir:
            contacts_path = os.path.join(temp_dir, "contacts.csv")
            with open(contacts_path, "w", encoding="utf-8") as contacts_file:
                contacts_file.write("Nome,email,telefone,relacionamento\n")
                contacts_file.write("Casa 1,pessoal1@example.com,16999999999,meu contato pessoal\n")
                contacts_file.write("Casa 2,pessoal2@example.com,16999999998,meu contato pessoal\n")

            with self.assertRaisesRegex(ValueError, "ambiguous"):
                email_tools.send_email(
                    {
                        "recipient_email": "meu email pessoal",
                        "subject": "abc",
                        "body": "conteúdo",
                    },
                    _build_context(memories_dir=temp_dir),
                )
        mock_send_custom_email.assert_not_called()

    @patch("assistant_connector.tools.email_tools.gmail_connector.send_custom_email")
    def test_send_email_uses_personal_contact_as_default_recipient(self, mock_send_custom_email):
        mock_send_custom_email.return_value = {"id": "msg-7"}
        with tempfile.TemporaryDirectory() as temp_dir:
            contacts_path = os.path.join(temp_dir, "contacts.csv")
            with open(contacts_path, "w", encoding="utf-8") as contacts_file:
                contacts_file.write("Nome,email,telefone,relacionamento\n")
                contacts_file.write("Contato pessoal,pessoal@example.com,16999999999,meu contato pessoal\n")

            with patch.dict(os.environ, {"EMAIL_TO": ""}, clear=False):
                email_tools.send_email(
                    {"subject": "abc", "body": "conteúdo"},
                    _build_context(memories_dir=temp_dir),
                )

        self.assertEqual(mock_send_custom_email.call_args.kwargs["email_to"], "pessoal@example.com")

    @patch("assistant_connector.tools.email_tools.gmail_connector.search_emails")
    def test_search_emails_passes_filters(self, mock_search_emails):
        mock_search_emails.return_value = {"returned": 0, "emails": []}

        email_tools.search_emails(
            {"query": "from:ana@example.com", "max_results": 5, "include_body": True},
            _build_context(),
        )

        kwargs = mock_search_emails.call_args.kwargs
        self.assertEqual(kwargs["query"], "from:ana@example.com")
        self.assertEqual(kwargs["max_results"], 5)
        self.assertTrue(kwargs["include_body"])

    @patch("assistant_connector.tools.email_tools.gmail_connector.read_email")
    def test_read_email_requires_message_id(self, mock_read_email):
        mock_read_email.return_value = {"id": "m1"}

        with self.assertRaises(ValueError):
            email_tools.read_email({}, _build_context())

    @patch("assistant_connector.tools.email_tools.gmail_connector.search_email_attachments")
    def test_search_email_attachments_passes_filters(self, mock_search_attachments):
        mock_search_attachments.return_value = {"returned": 0, "attachments": []}

        email_tools.search_email_attachments(
            {"query": "from:ana@example.com", "filename_contains": ".pdf", "max_results": 8},
            _build_context(),
        )

        kwargs = mock_search_attachments.call_args.kwargs
        self.assertEqual(kwargs["query"], "from:ana@example.com")
        self.assertEqual(kwargs["filename_contains"], ".pdf")
        self.assertEqual(kwargs["max_results"], 8)

    @patch("assistant_connector.tools.email_tools.gmail_connector.analyze_email_attachment")
    def test_analyze_email_attachment_requires_attachment_selector(self, mock_analyze_attachment):
        mock_analyze_attachment.return_value = {"content_preview": "ok"}

        with self.assertRaises(ValueError):
            email_tools.analyze_email_attachment(
                {"message_id": "m1"},
                _build_context(),
            )

        email_tools.analyze_email_attachment(
            {"message_id": "m1", "attachment_id": "att-1", "max_chars": 900},
            _build_context(),
        )
        kwargs = mock_analyze_attachment.call_args.kwargs
        self.assertEqual(kwargs["message_id"], "m1")
        self.assertEqual(kwargs["attachment_id"], "att-1")
        self.assertEqual(kwargs["max_chars"], 900)

    def test_search_contacts_rejects_non_integer_limit(self):
        csv_content = (
            "Nome, email, telefone, relacionamento\n"
            "Maria,maria@example.com,11999990000,amiga\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = os.path.join(temp_dir, "contacts.csv")
            with open(csv_path, "w", encoding="utf-8") as csv_file:
                csv_file.write(csv_content)

            with patch("assistant_connector.tools.contacts_tools.CONTACTS_CSV_PATH", csv_path):
                with self.assertRaisesRegex(ValueError, "limit must be a valid integer"):
                    contacts_tools.search_contacts({"query": "maria", "limit": "many"}, _build_context())

    def test_search_contacts_filters_by_query(self):
        csv_content = (
            "Nome, email, telefone, relacionamento\n"
            "Maria,maria@example.com,11999990000,amiga\n"
            "Joao,joao@example.com,21988887777,trabalho\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = os.path.join(temp_dir, "contacts.csv")
            with open(csv_path, "w", encoding="utf-8") as csv_file:
                csv_file.write(csv_content)

            with patch("assistant_connector.tools.contacts_tools.CONTACTS_CSV_PATH", csv_path):
                result = contacts_tools.search_contacts({"query": "maria"}, _build_context())

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["contacts"][0]["email"], "maria@example.com")

    def test_search_contacts_raises_for_missing_required_column(self):
        csv_content = "Nome,email,telefone\nMaria,maria@example.com,11999990000\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = os.path.join(temp_dir, "contacts.csv")
            with open(csv_path, "w", encoding="utf-8") as csv_file:
                csv_file.write(csv_content)

            with patch("assistant_connector.tools.contacts_tools.CONTACTS_CSV_PATH", csv_path):
                with self.assertRaises(ValueError):
                    contacts_tools.search_contacts({"query": "maria"}, _build_context())

    def test_register_contact_memory_writes_csv_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = contacts_tools.register_contact_memory(
                {
                    "name": "Maria Silva",
                    "email": "maria@example.com",
                    "phone": "11999990000",
                    "relationship": "amiga",
                },
                _build_context(memories_dir=temp_dir),
            )

            self.assertEqual(result["status"], "ok")
            self.assertTrue(os.path.isfile(os.path.join(temp_dir, "contacts.csv")))
            self.assertNotIn("contacts_md_path", result)

            search_result = contacts_tools.search_contacts({"query": "maria"}, _build_context(memories_dir=temp_dir))
            self.assertEqual(search_result["total"], 1)
            self.assertEqual(search_result["contacts"][0]["email"], "maria@example.com")

    def test_register_contact_memory_requires_email_or_phone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "email or phone is required"):
                contacts_tools.register_contact_memory(
                    {"name": "Maria Silva"},
                    _build_context(memories_dir=temp_dir),
                )


class TestBuildScheduledExecutionMessage(unittest.TestCase):
    def test_plain_message_has_no_logging_instruction(self):
        from assistant_connector.service import _build_scheduled_execution_message

        result = _build_scheduled_execution_message("Enviar relatório financeiro")

        self.assertIn("Pedido agendado:", result)
        self.assertIn("Enviar relatório financeiro", result)
        self.assertNotIn("check_daily_logging_status", result)

    def test_general_task_type_has_no_logging_instruction(self):
        from assistant_connector.service import _build_scheduled_execution_message

        result = _build_scheduled_execution_message(
            "Lembrar de registrar refeições", task_type="general"
        )
        self.assertNotIn("check_daily_logging_status", result)

    def test_logging_reminder_triggers_logging_instruction(self):
        from assistant_connector.service import _build_scheduled_execution_message

        for msg in [
            "Lembrar de registrar refeições",
            "Cobrar preenchimento de alimentação",
            "Verificar se já registrou o almoço",
            "Verificar se já registrou o treino",
        ]:
            result = _build_scheduled_execution_message(msg, task_type="logging_reminder")
            self.assertIn("check_daily_logging_status", result, f"Failed for: {msg}")
            self.assertIn("parabenize", result, f"Failed for: {msg}")

    def test_default_task_type_is_general(self):
        from assistant_connector.service import _build_scheduled_execution_message

        result = _build_scheduled_execution_message("Cobrar registro de exercícios")
        self.assertNotIn("check_daily_logging_status", result)


if __name__ == "__main__":
    unittest.main()
