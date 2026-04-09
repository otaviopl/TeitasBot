import unittest
import tempfile
import json

from assistant_connector.config_loader import load_assistant_configuration


class TestAssistantConfigLoader(unittest.TestCase):
    def test_load_default_configuration_contains_personal_assistant(self):
        configuration = load_assistant_configuration()
        agent = configuration.get_agent("personal_assistant")
        self.assertEqual(agent.agent_id, "personal_assistant")
        self.assertIn("list_available_tools", agent.tools)
        self.assertIn("list_calendar_events", agent.tools)
        self.assertIn("list_bills", agent.tools)
        self.assertIn("pay_bill", agent.tools)
        self.assertIn("analyze_bills", agent.tools)
        self.assertIn("register_meal", agent.tools)
        self.assertIn("analyze_meals", agent.tools)
        self.assertIn("register_exercise", agent.tools)
        self.assertIn("edit_exercise", agent.tools)
        self.assertIn("analyze_exercises", agent.tools)
        self.assertIn("calculate_metabolism_profile", agent.tools)
        self.assertIn("register_metabolism_profile", agent.tools)
        self.assertIn("get_metabolism_history", agent.tools)
        self.assertIn("list_scheduled_tasks", agent.tools)
        self.assertIn("create_scheduled_task", agent.tools)
        self.assertIn("edit_scheduled_task", agent.tools)
        self.assertIn("cancel_scheduled_task", agent.tools)
        self.assertIn("get_application_hardware_status", agent.tools)
        self.assertIn("list_tech_news", agent.tools)
        self.assertIn("list_news", agent.tools)
        self.assertIn("search_emails", agent.tools)
        self.assertIn("read_email", agent.tools)
        self.assertIn("search_email_attachments", agent.tools)
        self.assertIn("analyze_email_attachment", agent.tools)
        self.assertIn("Preserve fidelidade ao pedido do usuário", agent.system_prompt)
        self.assertNotIn("orientações incisivas e diretas", agent.system_prompt)
        self.assertIn(
            "orientações incisivas e diretas",
            configuration.tools["analyze_meals"].prompt_guidance,
        )
        self.assertIn(
            "todos os alimentos da refeição de uma só vez",
            configuration.tools["register_meal"].prompt_guidance,
        )
        self.assertIn(
            "duplicate_exercise_found",
            configuration.tools["register_exercise"].prompt_guidance,
        )
        self.assertIn(
            "parâmetro date",
            configuration.tools["analyze_expenses"].prompt_guidance,
        )

    def test_load_configuration_reads_prompt_guidance_and_priority(self):
        config = {
            "tools": [
                {
                    "name": "tool_a",
                    "description": "d",
                    "handler": "assistant_connector.tools.meta_tools:list_available_tools",
                    "prompt_guidance": "Use esta tool para listar recursos.",
                    "guidance_priority": 5,
                }
            ],
            "agents": [
                {"id": "agent_a", "description": "a", "system_prompt": "p", "tools": ["tool_a"]}
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True, encoding="utf-8") as temp_config:
            json.dump(config, temp_config)
            temp_config.flush()
            loaded = load_assistant_configuration(temp_config.name)

        self.assertEqual(
            loaded.tools["tool_a"].prompt_guidance,
            "Use esta tool para listar recursos.",
        )
        self.assertEqual(loaded.tools["tool_a"].guidance_priority, 5)

    def test_write_tools_flagged_as_write_operations(self):
        configuration = load_assistant_configuration()
        self.assertTrue(configuration.tools["create_task"].write_operation)
        self.assertTrue(configuration.tools["pay_bill"].write_operation)
        self.assertTrue(configuration.tools["register_meal"].write_operation)
        self.assertTrue(configuration.tools["register_exercise"].write_operation)
        self.assertTrue(configuration.tools["edit_exercise"].write_operation)
        self.assertTrue(configuration.tools["register_metabolism_profile"].write_operation)
        self.assertTrue(configuration.tools["create_scheduled_task"].write_operation)
        self.assertTrue(configuration.tools["edit_scheduled_task"].write_operation)
        self.assertTrue(configuration.tools["cancel_scheduled_task"].write_operation)
        self.assertTrue(configuration.tools["create_calendar_event"].write_operation)
        self.assertTrue(configuration.tools["send_email"].write_operation)

    def test_duplicate_tool_names_raise_error(self):
        config = {
            "tools": [
                {"name": "tool_a", "description": "d", "handler": "assistant_connector.tools.meta_tools:list_available_tools"},
                {"name": "tool_a", "description": "d2", "handler": "assistant_connector.tools.meta_tools:list_available_agents"},
            ],
            "agents": [
                {"id": "agent_a", "description": "a", "system_prompt": "p", "tools": ["tool_a"]}
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True, encoding="utf-8") as temp_config:
            json.dump(config, temp_config)
            temp_config.flush()
            with self.assertRaises(ValueError):
                load_assistant_configuration(temp_config.name)

    def test_unknown_tool_reference_raises_error(self):
        config = {
            "tools": [
                {"name": "tool_a", "description": "d", "handler": "assistant_connector.tools.meta_tools:list_available_tools"}
            ],
            "agents": [
                {"id": "agent_a", "description": "a", "system_prompt": "p", "tools": ["tool_missing"]}
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True, encoding="utf-8") as temp_config:
            json.dump(config, temp_config)
            temp_config.flush()
            with self.assertRaises(ValueError):
                load_assistant_configuration(temp_config.name)

    def test_invalid_limits_raise_error(self):
        config = {
            "tools": [
                {"name": "tool_a", "description": "d", "handler": "assistant_connector.tools.meta_tools:list_available_tools"}
            ],
            "agents": [
                {
                    "id": "agent_a",
                    "description": "a",
                    "system_prompt": "p",
                    "tools": ["tool_a"],
                    "max_tool_rounds": 0,
                }
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True, encoding="utf-8") as temp_config:
            json.dump(config, temp_config)
            temp_config.flush()
            with self.assertRaises(ValueError):
                load_assistant_configuration(temp_config.name)

    def test_duplicate_agent_ids_raise_error(self):
        config = {
            "tools": [
                {"name": "tool_a", "description": "d", "handler": "assistant_connector.tools.meta_tools:list_available_tools"}
            ],
            "agents": [
                {"id": "agent_a", "description": "a1", "system_prompt": "p", "tools": ["tool_a"]},
                {"id": "agent_a", "description": "a2", "system_prompt": "p", "tools": ["tool_a"]},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True, encoding="utf-8") as temp_config:
            json.dump(config, temp_config)
            temp_config.flush()
            with self.assertRaises(ValueError):
                load_assistant_configuration(temp_config.name)


if __name__ == "__main__":
    unittest.main()
