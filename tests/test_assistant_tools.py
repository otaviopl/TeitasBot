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
    notion_tools,
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
        available_tools=[{"name": "list_notion_tasks"}],
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

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_tasks_from_control_panel")
    def test_list_notion_tasks_clamps_inputs(self, mock_collect_tasks):
        mock_collect_tasks.return_value = [{"name": f"Task {i}"} for i in range(60)]

        result = notion_tools.list_notion_tasks(
            {"n_days": -4, "limit": 100},
            _build_context(),
        )

        mock_collect_tasks.assert_called_once_with(n_days=0, project_logger=unittest.mock.ANY, user_id=unittest.mock.ANY, credential_store=None)
        self.assertEqual(result["returned"], 50)
        self.assertEqual(len(result["tasks"]), 50)

    @patch("assistant_connector.tools.notion_tools.notion_connector.create_task_in_control_panel")
    def test_create_notion_task_uses_defaults_and_cleans_tags(self, mock_create_task):
        mock_create_task.return_value = {"id": "task-1"}

        result = notion_tools.create_notion_task(
            {
                "task_name": "  Revisar proposta  ",
                "project": "  ",
                "tags": [" FAST ", "", "FUP"],
            },
            _build_context(),
        )

        self.assertEqual(result["id"], "task-1")
        payload = mock_create_task.call_args.args[0]
        self.assertEqual(payload["task_name"], "Revisar proposta")
        self.assertEqual(payload["project"], "Pessoal")
        self.assertEqual(payload["tags"], ["FAST", "FUP"])

    def test_create_notion_task_rejects_invalid_tags(self):
        with self.assertRaises(ValueError):
            notion_tools.create_notion_task(
                {"task_name": "Task", "tags": "FAST"},
                _build_context(),
            )

    def test_create_notion_task_rejects_invalid_due_date(self):
        with self.assertRaisesRegex(ValueError, "due_date must be a valid ISO date"):
            notion_tools.create_notion_task(
                {"task_name": "Task", "due_date": "31/12/2026"},
                _build_context(),
            )

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_notes_around_today")
    def test_list_notion_notes_clamps_inputs(self, mock_collect_notes):
        mock_collect_notes.return_value = [{"name": f"Note {i}"} for i in range(120)]

        result = notion_tools.list_notion_notes(
            {"days_back": -3, "days_forward": -1, "limit": 999},
            _build_context(),
        )

        mock_collect_notes.assert_called_once_with(days_back=0, days_forward=0, project_logger=unittest.mock.ANY, user_id=unittest.mock.ANY, credential_store=None)
        self.assertEqual(result["returned"], 100)
        self.assertEqual(len(result["notes"]), 100)

    @patch("assistant_connector.tools.notion_tools.notion_connector.create_note_in_notes_db")
    def test_create_notion_note_accepts_rich_observations(self, mock_create_note):
        mock_create_note.return_value = {"id": "note-1"}

        rich_observations = (
            "Resumo completo:\n"
            "- Contexto\n"
            "- Decisões\n"
            "- Próximos passos\n\n"
            "Detalhes adicionais com múltiplos parágrafos."
        )
        result = notion_tools.create_notion_note(
            {
                "note_name": "Reunião produto",
                "tag": "MEETING",
                "observations": rich_observations,
                "url": "https://example.com/doc",
            },
            _build_context(),
        )

        self.assertEqual(result["id"], "note-1")
        payload = mock_create_note.call_args.args[0]
        self.assertEqual(payload["note_name"], "Reunião produto")
        self.assertEqual(payload["tag"], "MEETING")
        self.assertEqual(payload["observations"], rich_observations)
        self.assertEqual(payload["url"], "https://example.com/doc")

    def test_create_notion_note_requires_name(self):
        with self.assertRaises(ValueError):
            notion_tools.create_notion_note(
                {"note_name": "   ", "observations": "conteúdo"},
                _build_context(),
            )

    def test_register_financial_expense_rejects_non_numeric_amount(self):
        with self.assertRaisesRegex(ValueError, "amount must be a valid number"):
            notion_tools.register_financial_expense(
                {"description": "Almoço", "amount": "abc"},
                _build_context(),
            )

    def test_register_financial_expense_rejects_invalid_date(self):
        with self.assertRaisesRegex(ValueError, "expense_date must be a valid ISO date"):
            notion_tools.register_financial_expense(
                {"description": "Almoço", "amount": "50", "expense_date": "31/12/2026"},
                _build_context(),
            )

    @patch("assistant_connector.tools.notion_tools.notion_connector.create_expense_in_expenses_db")
    def test_register_financial_expense_creates_expense_page(self, mock_create_expense):
        mock_create_expense.return_value = {"id": "expense-1"}

        result = notion_tools.register_financial_expense(
            {"description": "Uber casa", "amount": 45.5, "expense_date": "2026-03-10"},
            _build_context(),
        )

        self.assertEqual(result["status"], "created")
        payload = mock_create_expense.call_args.args[0]
        self.assertEqual(payload["name"], "Despesa 2026-03-10")
        self.assertEqual(payload["date"], "2026-03-10")
        self.assertEqual(payload["category"], "Transporte")
        self.assertEqual(payload["amount"], 45.5)

    @patch("assistant_connector.tools.notion_tools.notion_connector.create_expense_in_expenses_db")
    def test_register_financial_expense_normalizes_category_aliases(self, mock_create_expense):
        mock_create_expense.return_value = {"id": "expense-2"}

        notion_tools.register_financial_expense(
            {"description": "Consulta médica", "amount": 120, "category": "saude", "expense_date": "2026-03-11"},
            _build_context(),
        )

        payload = mock_create_expense.call_args.args[0]
        self.assertEqual(payload["category"], "Saúde")

    @patch("assistant_connector.tools.notion_tools.notion_connector.create_meal_in_meals_db")
    def test_register_notion_meal_creates_entry_with_required_fields(self, mock_create_meal):
        mock_create_meal.return_value = {
            "id": "meal-1",
            "food": "Arroz branco",
            "meal_type": "ALMOÇO",
            "quantity": "150 g",
            "date": "2026-03-10",
            "calories": 195.0,
            "calorie_estimation_method": "llm_estimate",
        }

        result = notion_tools.register_notion_meal(
            {
                "alimento": "Arroz branco",
                "refeicao": "almoço",
                "quantidade": "150 g",
                "data": "2026-03-10",
                "calorias_estimadas": 190,
            },
            _build_context(),
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["meal"]["id"], "meal-1")
        payload = mock_create_meal.call_args.args[0]
        self.assertEqual(payload["food"], "Arroz branco")
        self.assertEqual(payload["meal_type"], "ALMOÇO")
        self.assertEqual(payload["quantity"], "150 g")
        self.assertEqual(payload["date"], "2026-03-10")
        self.assertEqual(payload["estimated_calories"], 190)

    def test_register_notion_meal_rejects_invalid_date(self):
        with self.assertRaisesRegex(ValueError, "date must be a valid ISO date"):
            notion_tools.register_notion_meal(
                {"alimento": "Frango", "refeicao": "almoço", "quantidade": "100 g", "data": "10/03/2026", "calorias_estimadas": 200},
                _build_context(),
            )

    def test_register_notion_meal_requires_fields(self):
        with self.assertRaises(ValueError):
            notion_tools.register_notion_meal({"refeicao": "ALMOÇO", "quantidade": "100 g"}, _build_context())
        with self.assertRaises(ValueError):
            notion_tools.register_notion_meal({"alimento": "Frango", "quantidade": "100 g"}, _build_context())
        with self.assertRaises(ValueError):
            notion_tools.register_notion_meal({"alimento": "Frango", "refeicao": "ALMOÇO"}, _build_context())
        with self.assertRaises(ValueError):
            notion_tools.register_notion_meal(
                {"alimento": "Frango", "refeicao": "ALMOÇO", "quantidade": "100 g"},
                _build_context(),
            )
        with self.assertRaises(ValueError):
            notion_tools.register_notion_meal(
                {"alimento": "Frango", "refeicao": "CEIA", "quantidade": "100 g"},
                _build_context(),
            )

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_meals_from_database")
    def test_analyze_notion_meals_returns_totals_and_insights(self, mock_collect_meals):
        mock_collect_meals.return_value = [
            {
                "id": "meal-1",
                "food": "Arroz branco",
                "meal_type": "ALMOÇO",
                "quantity": "200 g",
                "date": "2026-03-04",
                "calories": 260.0,
                "created_time": "2026-03-04T12:00:00Z",
            },
            {
                "id": "meal-2",
                "food": "Bolo de chocolate",
                "meal_type": "JANTAR",
                "quantity": "1 fatia",
                "date": "2026-03-04",
                "calories": 800.0,
                "created_time": "2026-03-04T20:00:00Z",
            },
        ]

        result = notion_tools.analyze_notion_meals({"days_back": 7, "limit": 50}, _build_context())

        self.assertEqual(result["total_entries"], 2)
        self.assertEqual(result["returned_entries"], 2)
        self.assertEqual(result["total_calories"], 1060.0)
        self.assertGreaterEqual(len(result["insights"]), 1)
        self.assertEqual(result["meal_breakdown"][0]["meal_type"], "JANTAR")
        self.assertTrue(any("regra mínima" in insight for insight in result["insights"]))
        self.assertTrue(any("itens açucarados" in insight for insight in result["insights"]))

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_exercises_from_database")
    @patch("assistant_connector.tools.notion_tools.notion_connector.create_exercise_in_exercises_db")
    def test_register_notion_exercise_creates_entry_with_required_fields(self, mock_create_exercise, mock_collect):
        mock_collect.return_value = []
        mock_create_exercise.return_value = {
            "id": "exercise-1",
            "activity": "Corrida",
            "date": "2999-03-10",
            "calories": 300.0,
            "observations": "Leve",
            "done": False,
        }

        result = notion_tools.register_notion_exercise(
            {
                "atividade": "Corrida",
                "calorias": 300,
                "data": "2999-03-10",
                "observacoes": "Leve",
            },
            _build_context(),
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["exercise"]["id"], "exercise-1")
        payload = mock_create_exercise.call_args.args[0]
        self.assertEqual(payload["activity"], "Corrida")
        self.assertEqual(payload["calories"], 300.0)
        self.assertEqual(payload["date"], "2999-03-10")
        self.assertFalse(payload["done"])

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_exercises_from_database")
    def test_register_notion_exercise_detects_duplicate(self, mock_collect):
        mock_collect.return_value = [
            {
                "id": "existing-exercise-1",
                "activity": "Corrida",
                "date": "2026-03-10",
                "calories": 200.0,
                "done": False,
                "page_url": "https://notion.so/existing-exercise-1",
            },
        ]

        result = notion_tools.register_notion_exercise(
            {
                "atividade": "Corrida",
                "calorias": 350,
                "data": "2026-03-10",
                "done": True,
            },
            _build_context(),
        )

        self.assertEqual(result["error"], "duplicate_exercise_found")
        self.assertEqual(result["existing_exercise"]["id"], "existing-exercise-1")

    def test_register_notion_exercise_rejects_invalid_date(self):
        with self.assertRaisesRegex(ValueError, "date must be a valid ISO date"):
            notion_tools.register_notion_exercise(
                {"atividade": "Corrida", "calorias": 300, "data": "10/03/2026"},
                _build_context(),
            )

    def test_register_notion_exercise_rejects_non_numeric_calories(self):
        with self.assertRaisesRegex(ValueError, "calorias must be a valid number"):
            notion_tools.register_notion_exercise(
                {"atividade": "Corrida", "calorias": "muitas", "data": "2026-03-10"},
                _build_context(),
            )

    @patch("assistant_connector.tools.notion_tools.notion_connector.update_exercise_in_exercises_db")
    def test_edit_notion_exercise_updates_payload(self, mock_update_exercise):
        mock_update_exercise.return_value = {"id": "exercise-1", "updated_fields": ["calories"]}

        result = notion_tools.edit_notion_exercise(
            {"page_id": "exercise-id", "calorias": 420, "done": True},
            _build_context(),
        )

        self.assertEqual(result["id"], "exercise-1")
        self.assertEqual(mock_update_exercise.call_args.args[0], "exercise-id")
        self.assertEqual(mock_update_exercise.call_args.kwargs["calories"], 420.0)
        self.assertTrue(mock_update_exercise.call_args.kwargs["done"])

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_meals_from_database")
    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_exercises_from_database")
    def test_analyze_notion_exercises_correlates_meals(self, mock_collect_exercises, mock_collect_meals):
        mock_collect_exercises.return_value = [
            {
                "id": "exercise-1",
                "activity": "Corrida",
                "date": "2026-03-04",
                "calories": 320.0,
                "done": True,
                "created_time": "2026-03-04T07:30:00Z",
            },
            {
                "id": "exercise-2",
                "activity": "Musculação",
                "date": "2026-03-05",
                "calories": 280.0,
                "done": False,
                "created_time": "2026-03-05T18:00:00Z",
            },
        ]
        mock_collect_meals.return_value = [
            {"id": "meal-1", "calories": 700.0},
            {"id": "meal-2", "calories": 500.0},
        ]

        result = notion_tools.analyze_notion_exercises({"days_back": 7, "limit": 50}, _build_context())

        self.assertEqual(result["total_entries"], 2)
        self.assertEqual(result["returned_entries"], 2)
        self.assertEqual(result["totals"]["total_exercise_calories"], 320.0)
        self.assertEqual(result["totals"]["total_planned_calories"], 280.0)
        self.assertEqual(result["totals"]["completed_entries"], 1)
        self.assertEqual(result["totals"]["pending_entries"], 1)
        self.assertEqual(result["totals"]["total_meal_calories"], 1200.0)
        self.assertEqual(result["totals"]["net_calorie_balance"], 880.0)
        self.assertEqual(result["breakdown_by_activity"][0]["activity"], "Corrida")

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

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_expenses_from_expenses_db")
    def test_analyze_monthly_expenses_returns_totals_breakdown_and_top_expense(self, mock_collect_expenses):
        mock_collect_expenses.return_value = [
            {
                "id": "expense-1",
                "date": "2026-03-02",
                "amount": 50.00,
                "category": "Transporte",
                "description": "Uber ida",
            },
            {
                "id": "expense-2",
                "date": "2026-03-03",
                "amount": 120.00,
                "category": "Alimentação",
                "description": "Mercado",
            }
        ]

        result = notion_tools.analyze_monthly_expenses({"month": "2026-03"}, _build_context())

        self.assertEqual(result["month"], "2026-03")
        self.assertEqual(result["total_spent"], 170.0)
        self.assertEqual(result["expenses_count"], 2)
        self.assertEqual(result["breakdown_by_category"][0]["category"], "Alimentação")
        self.assertEqual(result["top_expense"]["amount"], 120.0)
        self.assertEqual(result["selected_expenses_count"], 2)
        self.assertEqual(result["returned_count"], 2)
        self.assertEqual(result["expenses"][0]["date"], "2026-03-02")

    def test_analyze_monthly_expenses_validates_month_format(self):
        with self.assertRaises(ValueError):
            notion_tools.analyze_monthly_expenses({"month": "03-2026"}, _build_context())

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_expenses_from_expenses_db")
    def test_analyze_monthly_expenses_supports_day_filter(self, mock_collect_expenses):
        mock_collect_expenses.return_value = [
            {
                "id": "expense-1",
                "date": "2026-03-06",
                "amount": 50.00,
                "category": "Transporte",
                "description": "Uber ida",
            },
            {
                "id": "expense-2",
                "date": "2026-03-06",
                "amount": 180.00,
                "category": "Alimentação",
                "description": "Mercado",
            },
            {
                "id": "expense-3",
                "date": "2026-03-02",
                "amount": 30.00,
                "category": "Lazer",
                "description": "Café",
            },
        ]

        result = notion_tools.analyze_monthly_expenses(
            {"month": "2026-03", "date": "2026-03-06", "limit": 10},
            _build_context(),
        )

        self.assertEqual(result["month"], "2026-03")
        self.assertEqual(result["total_spent"], 260.0)
        self.assertEqual(result["applied_date_filter"], "2026-03-06")
        self.assertEqual(result["selected_total_spent"], 230.0)
        self.assertEqual(result["selected_expenses_count"], 2)
        self.assertEqual(result["selected_top_expense"]["amount"], 180.0)
        self.assertEqual(result["returned_count"], 2)
        self.assertTrue(all(expense["date"] == "2026-03-06" for expense in result["expenses"]))

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_expenses_from_expenses_db")
    def test_analyze_monthly_expenses_validates_day_filter_format(self, mock_collect_expenses):
        mock_collect_expenses.return_value = []
        with self.assertRaises(ValueError):
            notion_tools.analyze_monthly_expenses(
                {"month": "2026-03", "date": "06-03-2026"},
                _build_context(),
            )

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_monthly_bills_from_database")
    def test_list_unpaid_monthly_bills_returns_filtered_data(self, mock_collect_bills):
        mock_collect_bills.return_value = [
            {
                "id": "bill-1",
                "name": "Internet",
                "date": "2026-03-05",
                "paid": False,
                "category": "Casa",
                "budget": 120.0,
                "paid_amount": 0.0,
                "description": "",
            }
        ]

        result = notion_tools.list_unpaid_monthly_bills({"month": "2026-03", "limit": 10}, _build_context())

        self.assertEqual(result["month"], "2026-03")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["bills"][0]["name"], "Internet")

    def test_list_unpaid_monthly_bills_validates_month_format(self):
        with self.assertRaises(ValueError):
            notion_tools.list_unpaid_monthly_bills({"month": "03-2026"}, _build_context())

    @patch("assistant_connector.tools.notion_tools.notion_connector.update_monthly_bill_payment")
    def test_mark_monthly_bill_as_paid_updates_page(self, mock_update_bill):
        mock_update_bill.return_value = {"id": "bill-1", "paid": True, "paid_amount": 120.0, "payment_date": "2026-03-05"}

        result = notion_tools.mark_monthly_bill_as_paid(
            {"page_id": "bill-1", "paid_amount": 120, "payment_date": "2026-03-05"},
            _build_context(),
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["bill_id"], "bill-1")
        self.assertEqual(result["paid_amount"], 120.0)

    def test_mark_monthly_bill_as_paid_rejects_negative_amount(self):
        with self.assertRaises(ValueError):
            notion_tools.mark_monthly_bill_as_paid(
                {"page_id": "bill-1", "paid_amount": -1},
                _build_context(),
            )

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_monthly_bills_from_database")
    def test_analyze_monthly_bills_returns_totals(self, mock_collect_bills):
        mock_collect_bills.return_value = [
            {
                "id": "bill-1",
                "name": "Internet",
                "date": "2026-03-05",
                "paid": True,
                "category": "Casa",
                "budget": 120.0,
                "paid_amount": 120.0,
                "description": "",
            },
            {
                "id": "bill-2",
                "name": "Luz",
                "date": "2026-03-10",
                "paid": False,
                "category": "Casa",
                "budget": 200.0,
                "paid_amount": 0.0,
                "description": "",
            },
        ]

        result = notion_tools.analyze_monthly_bills({"month": "2026-03"}, _build_context())

        self.assertEqual(result["month"], "2026-03")
        self.assertEqual(result["total_bills"], 2)
        self.assertEqual(result["paid_count"], 1)
        self.assertEqual(result["unpaid_count"], 1)
        self.assertEqual(result["total_budget"], 320.0)
        self.assertEqual(result["pending_budget"], 200.0)

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_monthly_bills_from_database")
    def test_analyze_monthly_bills_empty_month(self, mock_collect_bills):
        mock_collect_bills.return_value = []

        result = notion_tools.analyze_monthly_bills({"month": "2026-03"}, _build_context())

        self.assertEqual(result["total_bills"], 0)
        self.assertEqual(result["pending_budget"], 0.0)

    @patch("assistant_connector.tools.notion_tools.notion_connector.update_notion_page")
    def test_edit_notion_item_updates_task_payload(self, mock_update_page):
        mock_update_page.return_value = {"id": "task-1", "updated_fields": ["task_name", "done"]}

        result = notion_tools.edit_notion_item(
            {
                "item_type": "task",
                "page_id": "https://www.notion.so/workspace/123456781234123412341234567890ab",
                "task_name": "  Fechar sprint ",
                "done": True,
            },
            _build_context(),
        )

        self.assertEqual(result["id"], "task-1")
        payload = mock_update_page.call_args.args[0]
        self.assertEqual(payload["item_type"], "task")
        self.assertEqual(payload["task_name"], "Fechar sprint")
        self.assertTrue(payload["done"])

    @patch("assistant_connector.tools.notion_tools.notion_connector.update_notion_page")
    def test_edit_notion_item_updates_card_payload(self, mock_update_page):
        mock_update_page.return_value = {"id": "card-1", "updated_fields": ["note_name", "date"]}

        result = notion_tools.edit_notion_item(
            {
                "item_type": "card",
                "page_id": "card-page-id",
                "note_name": "Retro semanal",
                "date": "2026-03-10",
            },
            _build_context(),
        )

        self.assertEqual(result["id"], "card-1")
        payload = mock_update_page.call_args.args[0]
        self.assertEqual(payload["item_type"], "card")
        self.assertEqual(payload["note_name"], "Retro semanal")
        self.assertEqual(payload["date"], "2026-03-10")

    def test_edit_notion_item_rejects_invalid_due_date(self):
        with self.assertRaisesRegex(ValueError, "due_date must be a valid ISO date"):
            notion_tools.edit_notion_item(
                {"item_type": "task", "page_id": "task-id", "due_date": "31/12/2026"},
                _build_context(),
            )

    def test_edit_notion_item_rejects_invalid_card_date(self):
        with self.assertRaisesRegex(ValueError, "date must be a valid ISO date"):
            notion_tools.edit_notion_item(
                {"item_type": "card", "page_id": "card-id", "date": "not-a-date"},
                _build_context(),
            )

    def test_edit_notion_item_requires_editable_fields(self):
        with self.assertRaises(ValueError):
            notion_tools.edit_notion_item(
                {"item_type": "task", "page_id": "task-id"},
                _build_context(),
            )

    @patch("assistant_connector.tools.notion_tools.notion_connector.update_notion_page")
    def test_edit_notion_item_ignores_empty_task_fields(self, mock_update_page):
        mock_update_page.return_value = {"id": "task-1", "updated_fields": ["done"]}

        result = notion_tools.edit_notion_item(
            {
                "item_type": "task",
                "page_id": "task-id",
                "task_name": "   ",
                "due_date": "",
                "done": True,
            },
            _build_context(),
        )

        self.assertEqual(result["id"], "task-1")
        payload = mock_update_page.call_args.args[0]
        self.assertEqual(payload, {"item_type": "task", "page_id": "task-id", "done": True})

    @patch("assistant_connector.tools.notion_tools.notion_connector.update_notion_page")
    def test_edit_notion_item_accepts_page_content_update(self, mock_update_page):
        mock_update_page.return_value = {"id": "task-1", "updated_fields": ["content"]}

        result = notion_tools.edit_notion_item(
            {
                "item_type": "task",
                "page_id": "task-id",
                "content": "# Novo conteúdo\n\n- item",
                "content_mode": "replace",
            },
            _build_context(),
        )

        self.assertEqual(result["id"], "task-1")
        payload = mock_update_page.call_args.args[0]
        self.assertEqual(payload["content_mode"], "replace")
        self.assertIn("Novo conteúdo", payload["content"])

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

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_exercises_from_database")
    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_meals_from_database")
    def test_check_daily_logging_status_returns_counts_when_logged(self, mock_meals, mock_exercises):
        mock_meals.return_value = [
            {"id": "m1", "food": "Arroz", "meal_type": "ALMOÇO", "calories": 300.0, "date": "2026-03-25"},
            {"id": "m2", "food": "Frango", "meal_type": "ALMOÇO", "calories": 200.0, "date": "2026-03-25"},
            {"id": "m3", "food": "Café", "meal_type": "CAFÉ DA MANHÃ", "calories": 50.0, "date": "2026-03-25"},
        ]
        mock_exercises.return_value = [
            {"id": "e1", "activity": "Corrida", "calories": 400.0, "date": "2026-03-25"},
        ]

        result = notion_tools.check_daily_logging_status({}, _build_context())

        self.assertTrue(result["meals_logged"])
        self.assertEqual(result["meal_count"], 3)
        self.assertIn("ALMOÇO", result["meal_types_logged"])
        self.assertIn("CAFÉ DA MANHÃ", result["meal_types_logged"])
        self.assertTrue(result["exercises_logged"])
        self.assertEqual(result["exercise_count"], 1)
        self.assertEqual(result["exercise_names"], ["Corrida"])

    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_exercises_from_database")
    @patch("assistant_connector.tools.notion_tools.notion_connector.collect_meals_from_database")
    def test_check_daily_logging_status_returns_false_when_empty(self, mock_meals, mock_exercises):
        mock_meals.return_value = []
        mock_exercises.return_value = []

        result = notion_tools.check_daily_logging_status({}, _build_context())

        self.assertFalse(result["meals_logged"])
        self.assertEqual(result["meal_count"], 0)
        self.assertEqual(result["meal_types_logged"], [])
        self.assertFalse(result["exercises_logged"])
        self.assertEqual(result["exercise_count"], 0)
        self.assertEqual(result["exercise_names"], [])


class TestBuildScheduledExecutionMessage(unittest.TestCase):
    def test_plain_message_has_no_logging_instruction(self):
        from assistant_connector.service import _build_scheduled_execution_message

        result = _build_scheduled_execution_message("Enviar relatório financeiro")

        self.assertIn("Pedido agendado:", result)
        self.assertIn("Enviar relatório financeiro", result)
        self.assertNotIn("check_daily_logging_status", result)

    def test_meal_keyword_triggers_logging_instruction(self):
        from assistant_connector.service import _build_scheduled_execution_message

        for msg in [
            "Lembrar de registrar refeições",
            "Cobrar preenchimento de alimentação",
            "Verificar se já registrou o almoço",
            "Registrou as refeições do jantar?",
        ]:
            result = _build_scheduled_execution_message(msg)
            self.assertIn("check_daily_logging_status", result, f"Failed for: {msg}")
            self.assertIn("parabenize", result, f"Failed for: {msg}")

    def test_exercise_keyword_triggers_logging_instruction(self):
        from assistant_connector.service import _build_scheduled_execution_message

        for msg in [
            "Lembrar de registrar exercícios",
            "Cobrar preenchimento de atividade física",
            "Verificar se já registrou o treino",
            "Registrou a musculação de hoje?",
        ]:
            result = _build_scheduled_execution_message(msg)
            self.assertIn("check_daily_logging_status", result, f"Failed for: {msg}")

    def test_is_logging_reminder_detection(self):
        from assistant_connector.service import _is_logging_reminder

        self.assertTrue(_is_logging_reminder("Cobrar registro de refeições"))
        self.assertTrue(_is_logging_reminder("Lembrete de exercício"))
        self.assertTrue(_is_logging_reminder("Hora do treino!"))
        self.assertTrue(_is_logging_reminder("Registrou a corrida?"))
        self.assertTrue(_is_logging_reminder("Cadê a caminhada?"))
        self.assertFalse(_is_logging_reminder("Enviar relatório financeiro"))
        self.assertFalse(_is_logging_reminder("Verificar notícias"))


if __name__ == "__main__":
    unittest.main()
