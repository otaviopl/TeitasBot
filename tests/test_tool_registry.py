import unittest

from assistant_connector.models import AgentDefinition, ToolDefinition, ToolExecutionContext
from assistant_connector.tool_registry import ToolRegistry


def _dict_handler(arguments, _context):
    return {"received": arguments}


def _non_dict_handler(_arguments, _context):
    return "invalid"


NON_CALLABLE_HANDLER = "not-callable"


class _FakeLogger:
    def exception(self, *_args, **_kwargs):
        return None


def _build_context():
    agent = AgentDefinition(
        agent_id="test-agent",
        description="desc",
        model="model",
        system_prompt="prompt",
        tools=["tool_a"],
    )
    return ToolExecutionContext(
        session_id="s",
        user_id="u",
        channel_id="c",
        guild_id="g",
        project_logger=_FakeLogger(),
        agent=agent,
        available_tools=[],
        available_agents=[],
    )


class TestToolRegistry(unittest.TestCase):
    def test_get_openai_tools_returns_expected_shape(self):
        registry = ToolRegistry(
            {
                "tool_a": ToolDefinition(
                    name="tool_a",
                    description="desc",
                    input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                    handler="tests.test_tool_registry:_dict_handler",
                )
            }
        )

        payload = registry.get_openai_tools(["tool_a"])
        self.assertEqual(payload[0]["type"], "function")
        self.assertEqual(payload[0]["name"], "tool_a")

    def test_describe_tools_includes_prompt_guidance(self):
        registry = ToolRegistry(
            {
                "tool_a": ToolDefinition(
                    name="tool_a",
                    description="desc",
                    input_schema={"type": "object", "properties": {}},
                    handler="tests.test_tool_registry:_dict_handler",
                    prompt_guidance="Use esta tool somente para dados públicos.",
                    guidance_priority=7,
                )
            }
        )

        payload = registry.describe_tools(["tool_a"])
        self.assertEqual(payload[0]["prompt_guidance"], "Use esta tool somente para dados públicos.")
        self.assertEqual(payload[0]["guidance_priority"], 7)

    def test_unknown_tool_raises(self):
        registry = ToolRegistry({})
        with self.assertRaises(ValueError):
            registry.get_tool_definition("missing")

    def test_invalid_handler_path_raises(self):
        registry = ToolRegistry(
            {
                "tool_a": ToolDefinition(
                    name="tool_a",
                    description="desc",
                    input_schema={"type": "object", "properties": {}},
                    handler="invalid-path-without-colon",
                )
            }
        )

        with self.assertRaises(ValueError):
            registry.execute_tool("tool_a", {}, _build_context())

    def test_non_callable_handler_raises(self):
        registry = ToolRegistry(
            {
                "tool_a": ToolDefinition(
                    name="tool_a",
                    description="desc",
                    input_schema={"type": "object", "properties": {}},
                    handler="tests.test_tool_registry:NON_CALLABLE_HANDLER",
                )
            }
        )

        with self.assertRaises(ValueError):
            registry.execute_tool("tool_a", {}, _build_context())

    def test_execute_tool_requires_dict_return(self):
        registry = ToolRegistry(
            {
                "tool_a": ToolDefinition(
                    name="tool_a",
                    description="desc",
                    input_schema={"type": "object", "properties": {}},
                    handler="tests.test_tool_registry:_non_dict_handler",
                )
            }
        )

        with self.assertRaises(ValueError):
            registry.execute_tool("tool_a", {}, _build_context())


if __name__ == "__main__":
    unittest.main()
