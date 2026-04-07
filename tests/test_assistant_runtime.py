import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from assistant_connector.memory_store import ConversationMemoryStore
from assistant_connector.models import AgentDefinition, ToolDefinition
from assistant_connector.runtime import AssistantRuntime
from assistant_connector.tool_registry import ToolRegistry


WRITE_TOOL_CALL_COUNT = 0


def _write_tool(_arguments, _context):
    global WRITE_TOOL_CALL_COUNT
    WRITE_TOOL_CALL_COUNT += 1
    return {"ok": True}


def _raising_tool(_arguments, _context):
    raise RuntimeError("tool exploded")


def _non_dict_tool(_arguments, _context):
    return "invalid"


def _large_payload_tool(_arguments, _context):
    return {"content": "x" * 5000}


def _guided_tool(_arguments, _context):
    return {"ok": True}


class _FakeLogger:
    def exception(self, *_args, **_kwargs):
        return None


class _FakeResponsesAPI:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._payloads:
            raise AssertionError("Unexpected OpenAI call without queued payload")
        return self._payloads.pop(0)


class _FakeOpenAIClient:
    def __init__(self, payloads):
        self.responses = _FakeResponsesAPI(payloads)


class TestAssistantRuntime(unittest.TestCase):
    def _build_runtime(
        self,
        *,
        temp_dir,
        payloads,
        tool_definitions,
        tool_names,
        max_tool_rounds=3,
        memory_window=20,
        max_history_chars=12000,
        max_tool_output_chars=8000,
        agent_memory_text="",
        user_memories=None,
    ):
        return AssistantRuntime(
            agent=AgentDefinition(
                agent_id="personal_assistant",
                description="assistant",
                model="gpt-4.1-mini",
                system_prompt="prompt",
                tools=tool_names,
                max_tool_rounds=max_tool_rounds,
                memory_window=memory_window,
            ),
            tool_registry=ToolRegistry(tool_definitions),
            memory_store=ConversationMemoryStore(os.path.join(temp_dir, "assistant.sqlite3")),
            project_logger=_FakeLogger(),
            available_agents=[{"id": "personal_assistant", "description": "assistant", "model": "gpt-4.1-mini"}],
            max_history_chars=max_history_chars,
            max_tool_output_chars=max_tool_output_chars,
            agent_memory_text=agent_memory_text,
            user_memories=user_memories,
            openai_client=_FakeOpenAIClient(payloads),
        )

    def test_runtime_executes_tool_and_returns_final_message(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "list_available_tools",
                        "arguments": "{}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Aqui estão as tools disponíveis.",
            },
        ]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )

            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="quais tools você tem?",
            )

            self.assertEqual(answer.text, "Aqui estão as tools disponíveis.")
            history = runtime._memory_store.get_recent_messages("guild:channel:user", 10)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[1]["role"], "assistant")

    def test_runtime_returns_direct_output_text_without_tool_calls(self):
        payloads = [{"id": "resp-1", "output": [], "output_text": "Resposta direta"}]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="oi",
            )

        self.assertEqual(answer.text, "Resposta direta")

    def test_runtime_injects_agent_and_user_memories(self):
        payloads = [{"id": "resp-1", "output": [], "output_text": "Resposta direta"}]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
                agent_memory_text="agent-memory-style",
                user_memories={"about-me.md": "Usuário focado em trabalho e família"},
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="Me lembre das prioridades da família",
            )

        first_call_input = runtime._openai_client.responses.calls[0]["input"]
        system_messages = [msg["content"] for msg in first_call_input if msg["role"] == "system"]
        self.assertTrue(any("agent-memory-style" in content for content in system_messages))
        self.assertTrue(any("família" in content for content in system_messages))

    def test_runtime_rejects_empty_message(self):
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=[],
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            with self.assertRaises(ValueError):
                runtime.process_user_message(
                    session_id="guild:channel:user",
                    user_id="user",
                    channel_id="channel",
                    guild_id="guild",
                    message="   ",
                )

    def test_runtime_blocks_write_tool_without_confirmation(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "dangerous_write_tool",
                        "arguments": "{\"task_name\":\"Nova tarefa\"}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Confirmação necessária.",
            },
        ]
        tool_definitions = {
            "dangerous_write_tool": ToolDefinition(
                name="dangerous_write_tool",
                description="Cria dado externo",
                input_schema={"type": "object", "properties": {"task_name": {"type": "string"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["dangerous_write_tool"],
            )

            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="crie a tarefa agora",
            )

        self.assertEqual(answer.text, "Confirmação necessária.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 0)
        self.assertIn("confirmation_required", runtime._openai_client.responses.calls[1]["input"][0]["output"])

    def test_runtime_executes_write_tool_with_confirmation(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "dangerous_write_tool",
                        "arguments": "{\"task_name\":\"Nova tarefa\",\"confirmed\":true}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Tarefa criada.",
            },
        ]
        tool_definitions = {
            "dangerous_write_tool": ToolDefinition(
                name="dangerous_write_tool",
                description="Cria dado externo",
                input_schema={"type": "object", "properties": {"task_name": {"type": "string"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["dangerous_write_tool"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="crie a tarefa com confirmação",
            )

        self.assertEqual(answer.text, "Tarefa criada.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 1)

    def test_runtime_blocks_scheduled_task_tools_during_scheduled_execution(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "create_scheduled_task",
                        "arguments": "{\"confirmed\":true}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Bloqueio aplicado.",
            },
        ]
        tool_definitions = {
            "create_scheduled_task": ToolDefinition(
                name="create_scheduled_task",
                description="Cria agendamento",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["create_scheduled_task"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user:scheduled:task-1",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="executar tarefa",
            )

        self.assertEqual(answer.text, "Bloqueio aplicado.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 0)
        self.assertIn(
            "tool_not_allowed_during_scheduled_execution",
            runtime._openai_client.responses.calls[1]["input"][0]["output"],
        )

    def test_runtime_executes_sensitive_write_tool_with_model_confirmation(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "send_email",
                        "arguments": "{\"confirmed\":true}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Email enviado.",
            },
        ]
        tool_definitions = {
            "send_email": ToolDefinition(
                name="send_email",
                description="Envia email",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["send_email"],
            )

            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="envia agora",
            )

        self.assertEqual(answer.text, "Email enviado.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 1)

    def test_runtime_executes_sensitive_write_tool_after_clear_confirmation(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "create_task",
                        "arguments": "{\"task_name\":\"Nova tarefa\",\"confirmed\":true}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Tarefa criada.",
            },
        ]
        tool_definitions = {
            "create_task": ToolDefinition(
                name="create_task",
                description="Cria tarefa",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["create_task"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="pode enviar",
            )

        self.assertEqual(answer.text, "Tarefa criada.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 1)

    def test_runtime_executes_sensitive_write_tool_with_natural_confirmation_word(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "create_task",
                        "arguments": "{\"task_name\":\"Nova tarefa\",\"confirmed\":true}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Tarefa criada.",
            },
        ]
        tool_definitions = {
            "create_task": ToolDefinition(
                name="create_task",
                description="Cria tarefa",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["create_task"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="confirmar",
            )

        self.assertEqual(answer.text, "Tarefa criada.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 1)

    def test_runtime_executes_sensitive_write_tool_with_sim_confirmation(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "create_task",
                        "arguments": "{\"task_name\":\"Nova tarefa\",\"confirmed\":true}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Tarefa criada.",
            },
        ]
        tool_definitions = {
            "create_task": ToolDefinition(
                name="create_task",
                description="Cria tarefa",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["create_task"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="sim",
            )

        self.assertEqual(answer.text, "Tarefa criada.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 1)

    def test_runtime_does_not_auto_inject_confirmation_from_user_message(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "dangerous_write_tool",
                        "arguments": "{\"task_name\":\"Nova tarefa\"}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Confirmação necessária.",
            },
        ]
        tool_definitions = {
            "dangerous_write_tool": ToolDefinition(
                name="dangerous_write_tool",
                description="Cria dado externo",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["dangerous_write_tool"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="confirmo, pode enviar",
            )

        self.assertEqual(answer.text, "Confirmação necessária.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 0)
        self.assertIn("confirmation_required", runtime._openai_client.responses.calls[1]["input"][0]["output"])

    def test_runtime_does_not_override_confirmed_false_from_user_message(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "dangerous_write_tool",
                        "arguments": "{\"task_name\":\"Nova tarefa\",\"confirmed\":false}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Confirmação necessária.",
            },
        ]
        tool_definitions = {
            "dangerous_write_tool": ToolDefinition(
                name="dangerous_write_tool",
                description="Cria dado externo",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["dangerous_write_tool"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="sim, pode salvar",
            )

        self.assertEqual(answer.text, "Confirmação necessária.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 0)
        self.assertIn("confirmation_required", runtime._openai_client.responses.calls[1]["input"][0]["output"])

    def test_runtime_handles_invalid_tool_arguments_json(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "list_available_tools",
                        "arguments": "{invalid",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Argumento inválido tratado.",
            },
        ]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="teste",
            )

            self.assertEqual(answer.text, "Argumento inválido tratado.")
            self.assertIn("invalid_tool_arguments", runtime._openai_client.responses.calls[1]["input"][0]["output"])

            with sqlite3.connect(runtime._memory_store.db_path) as connection:
                row = connection.execute(
                    "SELECT arguments_json FROM tool_calls ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self.assertIn("_raw_arguments", row[0])

    def test_runtime_reports_tool_execution_failure(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "failing_tool",
                        "arguments": "{}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Erro tratado.",
            },
        ]
        tool_definitions = {
            "failing_tool": ToolDefinition(
                name="failing_tool",
                description="Falha sempre",
                input_schema={"type": "object", "properties": {}},
                handler="tests.test_assistant_runtime:_raising_tool",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["failing_tool"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="teste",
            )

        self.assertEqual(answer.text, "Erro tratado.")
        self.assertIn("tool_execution_failed", runtime._openai_client.responses.calls[1]["input"][0]["output"])

    def test_runtime_reports_non_dict_tool_response(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "non_dict_tool",
                        "arguments": "{}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Erro tratado.",
            },
        ]
        tool_definitions = {
            "non_dict_tool": ToolDefinition(
                name="non_dict_tool",
                description="Retorno inválido",
                input_schema={"type": "object", "properties": {}},
                handler="tests.test_assistant_runtime:_non_dict_tool",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["non_dict_tool"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="teste",
            )

        self.assertEqual(answer.text, "Erro tratado.")
        self.assertIn("tool_execution_failed", runtime._openai_client.responses.calls[1]["input"][0]["output"])

    def test_runtime_uses_content_text_when_output_text_missing(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Resposta via content"}],
                    }
                ],
                "output_text": "",
            }
        ]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="oi",
            )

        self.assertEqual(answer.text, "Resposta via content")

    def test_runtime_uses_placeholder_call_id_when_missing(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "list_available_tools",
                        "arguments": "{}",
                        "call_id": "",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "ok",
            },
        ]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="oi",
            )

        self.assertTrue(
            runtime._openai_client.responses.calls[1]["input"][0]["call_id"].startswith(
                "missing-call-id-list_available_tools"
            )
        )

    def test_runtime_returns_fallback_after_max_tool_rounds(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {"type": "function_call", "name": "list_available_tools", "arguments": "{}", "call_id": "call-1"}
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [
                    {"type": "function_call", "name": "list_available_tools", "arguments": "{}", "call_id": "call-2"}
                ],
                "output_text": "",
            },
            {
                "id": "resp-3",
                "output": [
                    {"type": "function_call", "name": "list_available_tools", "arguments": "{}", "call_id": "call-3"}
                ],
                "output_text": "",
            },
        ]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
                max_tool_rounds=2,
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="oi",
            )

        self.assertIn("Não consegui concluir", answer.text)

    def test_runtime_respects_memory_window(self):
        payloads = [{"id": "resp-1", "output": [], "output_text": "ok"}]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
                memory_window=2,
            )
            runtime._memory_store.append_message("guild:channel:user", "user", "msg-1")
            runtime._memory_store.append_message("guild:channel:user", "assistant", "msg-2")
            runtime._memory_store.append_message("guild:channel:user", "user", "msg-3")

            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="msg-4",
            )

        sent_input = runtime._openai_client.responses.calls[0]["input"]
        self.assertEqual(len(sent_input), 3)
        self.assertEqual(sent_input[-2]["content"], "msg-3")
        self.assertEqual(sent_input[-1]["content"], "msg-4")

    def test_runtime_limits_history_by_char_budget(self):
        payloads = [{"id": "resp-1", "output": [], "output_text": "ok"}]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
                memory_window=10,
                max_history_chars=1000,
            )
            runtime._memory_store.append_message("guild:channel:user", "user", "x" * 600)
            runtime._memory_store.append_message("guild:channel:user", "assistant", "y" * 600)

            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="z" * 600,
            )

        sent_input = runtime._openai_client.responses.calls[0]["input"]
        self.assertEqual(len(sent_input), 2)
        self.assertEqual(sent_input[-1]["content"], "z" * 600)

    def test_runtime_truncates_large_tool_output_sent_to_llm(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "large_tool",
                        "arguments": "{}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "ok",
            },
        ]
        tool_definitions = {
            "large_tool": ToolDefinition(
                name="large_tool",
                description="Retorna payload grande",
                input_schema={"type": "object", "properties": {}},
                handler="tests.test_assistant_runtime:_large_payload_tool",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["large_tool"],
                max_tool_output_chars=500,
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="teste",
            )

        output_payload = runtime._openai_client.responses.calls[1]["input"][0]["output"]
        self.assertIn('"truncated": true', output_payload.lower())

    def test_runtime_injects_markdown_response_guidelines(self):
        payloads = [{"id": "resp-1", "output": [], "output_text": "ok"}]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="oi",
            )

        system_message = runtime._openai_client.responses.calls[0]["input"][0]["content"]
        self.assertIn("Sempre responda em Markdown", system_message)
        self.assertIn("não ultrapasse 1800", system_message)

    def test_runtime_injects_email_style_preferences_when_available(self):
        payloads = [{"id": "resp-1", "output": [], "output_text": "ok"}]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "EMAIL_ASSISTANT_TONE": "amigável e direto",
                "EMAIL_ASSISTANT_SIGNATURE": "Carlos",
                "EMAIL_ASSISTANT_STYLE_GUIDE": "Use bullets",
            },
            clear=False,
        ):
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="oi",
            )

        system_message = runtime._openai_client.responses.calls[0]["input"][0]["content"]
        self.assertIn("Tom de voz: amigável e direto", system_message)
        self.assertIn("assinatura de email é aplicada automaticamente", system_message.lower())
        self.assertIn("destinatário tiver sido informado explicitamente", system_message)

    def test_runtime_injects_timezone_context_guidance(self):
        payloads = [{"id": "resp-1", "output": [], "output_text": "ok"}]
        tool_definitions = {
            "list_available_tools": ToolDefinition(
                name="list_available_tools",
                description="Lista tools",
                input_schema={"type": "object", "properties": {}},
                handler="assistant_connector.tools.meta_tools:list_available_tools",
                write_operation=False,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "assistant_connector.runtime.build_time_context",
            return_value={
                "timezone_name": "America/Sao_Paulo",
                "local_now_iso": "2026-03-01T22:30:00-03:00",
                "local_date_iso": "2026-03-01",
                "local_utc_offset": "-03:00",
                "utc_now_iso": "2026-03-02T01:30:00Z",
                "utc_date_iso": "2026-03-02",
            },
        ):
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["list_available_tools"],
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="oi",
            )

        system_message = runtime._openai_client.responses.calls[0]["input"][0]["content"]
        self.assertIn("Timezone operacional: America/Sao_Paulo", system_message)
        self.assertIn("Data local atual: 2026-03-01", system_message)
        self.assertIn("interprete termos relativos de tempo", system_message)

    def test_runtime_injects_dynamic_tool_guidance_after_function_call(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "guided_tool",
                        "arguments": "{}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "ok",
            },
        ]
        tool_definitions = {
            "guided_tool": ToolDefinition(
                name="guided_tool",
                description="Tool com guidance",
                input_schema={"type": "object", "properties": {}},
                handler="tests.test_assistant_runtime:_guided_tool",
                write_operation=False,
                prompt_guidance="Aplique regra específica de execução desta tool.",
                guidance_priority=2,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["guided_tool"],
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="execute",
            )

        second_input = runtime._openai_client.responses.calls[1]["input"]
        self.assertEqual(second_input[0]["type"], "function_call_output")
        self.assertEqual(second_input[-1]["role"], "system")
        self.assertIn("### guided_tool", second_input[-1]["content"])
        self.assertIn("Aplique regra específica", second_input[-1]["content"])

    def test_runtime_deduplicates_dynamic_guidance_for_repeated_tool_calls(self):
        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {"type": "function_call", "name": "guided_tool", "arguments": "{}", "call_id": "call-1"},
                    {"type": "function_call", "name": "guided_tool", "arguments": "{}", "call_id": "call-2"},
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "ok",
            },
        ]
        tool_definitions = {
            "guided_tool": ToolDefinition(
                name="guided_tool",
                description="Tool com guidance",
                input_schema={"type": "object", "properties": {}},
                handler="tests.test_assistant_runtime:_guided_tool",
                write_operation=False,
                prompt_guidance="Guidance único por tool no round.",
                guidance_priority=2,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["guided_tool"],
            )
            runtime.process_user_message(
                session_id="guild:channel:user",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="execute duas vezes",
            )

        second_input = runtime._openai_client.responses.calls[1]["input"]
        self.assertEqual(second_input[0]["type"], "function_call_output")
        self.assertEqual(second_input[1]["type"], "function_call_output")
        guidance_blocks = [
            item["content"]
            for item in second_input
            if item.get("role") == "system"
        ]
        self.assertEqual(len(guidance_blocks), 1)
        self.assertEqual(guidance_blocks[0].count("### guided_tool"), 1)

    def test_runtime_blocks_auto_confirm_write_tool_during_scheduled_execution(self):
        global WRITE_TOOL_CALL_COUNT
        WRITE_TOOL_CALL_COUNT = 0

        payloads = [
            {
                "id": "resp-1",
                "output": [
                    {
                        "type": "function_call",
                        "name": "edit_scheduled_task",
                        "arguments": "{\"confirmed\":true}",
                        "call_id": "call-1",
                    }
                ],
                "output_text": "",
            },
            {
                "id": "resp-2",
                "output": [],
                "output_text": "Bloqueio de auto_confirm em scheduled.",
            },
        ]
        tool_definitions = {
            "edit_scheduled_task": ToolDefinition(
                name="edit_scheduled_task",
                description="Edita agendamento",
                input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
                handler="tests.test_assistant_runtime:_write_tool",
                write_operation=True,
                auto_confirm=True,
            )
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._build_runtime(
                temp_dir=temp_dir,
                payloads=payloads,
                tool_definitions=tool_definitions,
                tool_names=["edit_scheduled_task"],
            )
            answer = runtime.process_user_message(
                session_id="guild:channel:user:scheduled:task-2",
                user_id="user",
                channel_id="channel",
                guild_id="guild",
                message="executar edição",
            )

        self.assertEqual(answer.text, "Bloqueio de auto_confirm em scheduled.")
        self.assertEqual(WRITE_TOOL_CALL_COUNT, 0)
        self.assertIn(
            "tool_not_allowed_during_scheduled_execution",
            runtime._openai_client.responses.calls[1]["input"][0]["output"],
        )


class TestResolveUserMemoriesDir(unittest.TestCase):
    """Security tests for _resolve_user_memories_dir path traversal guards."""

    def _build_minimal_runtime(self, *, memories_dir: str | None, db_path: str):
        return AssistantRuntime(
            agent=AgentDefinition(
                agent_id="personal_assistant",
                description="assistant",
                model="gpt-4.1-mini",
                system_prompt="prompt",
                tools=[],
                max_tool_rounds=1,
                memory_window=10,
            ),
            tool_registry=ToolRegistry({}),
            memory_store=ConversationMemoryStore(db_path),
            project_logger=_FakeLogger(),
            available_agents=[],
            memories_dir=memories_dir,
            openai_client=_FakeOpenAIClient([]),
        )

    def test_valid_user_id_resolves_existing_dir(self):
        with tempfile.TemporaryDirectory() as base:
            user_dir = os.path.join(base, "12345")
            os.makedirs(user_dir)
            rt = self._build_minimal_runtime(memories_dir=base, db_path=os.path.join(base, "mem.sqlite3"))
            result = rt._resolve_user_memories_dir("12345")
            self.assertEqual(result, user_dir)

    def test_path_traversal_user_id_stripped_stays_in_base(self):
        """'../other' is sanitized to 'other' — resolved path stays inside base."""
        with tempfile.TemporaryDirectory() as base:
            rt = self._build_minimal_runtime(memories_dir=base, db_path=os.path.join(base, "mem.sqlite3"))
            result = rt._resolve_user_memories_dir("../other")
            # Sanitized to "other" subdir inside base, never escapes
            if result is not None:
                self.assertTrue(os.path.realpath(result).startswith(os.path.realpath(base)))

    def test_user_id_with_slash_stripped_stays_in_base(self):
        """'foo/bar' is sanitized to 'foobar' — path stays inside base."""
        with tempfile.TemporaryDirectory() as base:
            rt = self._build_minimal_runtime(memories_dir=base, db_path=os.path.join(base, "mem.sqlite3"))
            result = rt._resolve_user_memories_dir("foo/bar")
            if result is not None:
                self.assertTrue(os.path.realpath(result).startswith(os.path.realpath(base)))

    def test_dotdot_path_traversal_stays_in_base(self):
        """'../../etc/passwd' is sanitized to 'etcpasswd' — path stays inside base."""
        with tempfile.TemporaryDirectory() as base:
            rt = self._build_minimal_runtime(memories_dir=base, db_path=os.path.join(base, "mem.sqlite3"))
            result = rt._resolve_user_memories_dir("../../etc/passwd")
            if result is not None:
                self.assertTrue(os.path.realpath(result).startswith(os.path.realpath(base)))

    def test_no_memories_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as base:
            rt = self._build_minimal_runtime(memories_dir=None, db_path=os.path.join(base, "mem.sqlite3"))
            result = rt._resolve_user_memories_dir("12345")
            self.assertIsNone(result)

    def test_nonexistent_base_memories_dir_returns_user_subdir_path(self):
        with tempfile.TemporaryDirectory() as base:
            missing_base = os.path.join(base, "missing-memories")
            rt = self._build_minimal_runtime(memories_dir=missing_base, db_path=os.path.join(base, "mem.sqlite3"))
            result = rt._resolve_user_memories_dir("12345")
            # Always returns user-specific subdir — never falls back to root dir
            self.assertEqual(result, os.path.join(missing_base, "12345"))


class TestSelectUserMemoryContext(unittest.TestCase):
    def _build_minimal_runtime(self, db_path):
        return AssistantRuntime(
            agent=AgentDefinition(
                agent_id="test", description="test", model="gpt-4.1-mini",
                system_prompt="prompt", tools=[], max_tool_rounds=1, memory_window=5,
            ),
            tool_registry=ToolRegistry({}),
            memory_store=ConversationMemoryStore(db_path),
            project_logger=_FakeLogger(),
            available_agents=[],
            openai_client=_FakeOpenAIClient([]),
        )

    def test_health_query_ranks_health_file_above_about_me(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = self._build_minimal_runtime(os.path.join(tmp, "mem.sqlite3"))
            memories = {
                "about-me.md": "Sou Carlos, desenvolvedor",
                "health.md": "Peso 72 kg, meta 2050 kcal/dia, dieta low carb",
            }
            result = rt._select_user_memory_context("como está minha dieta", memories)
            # health.md should appear first due to synonym match (dieta → health group)
            health_pos = result.find("### health.md")
            about_pos = result.find("### about-me.md")
            self.assertGreater(about_pos, -1)
            self.assertGreater(health_pos, -1)
            self.assertLess(health_pos, about_pos)

    def test_synonym_expansion_matches_related_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = self._build_minimal_runtime(os.path.join(tmp, "mem.sqlite3"))
            memories = {
                "work.md": "Draiven, plataforma de IA para decisões de negócio",
                "health.md": "Peso 72 kg",
            }
            # "carreira" is a synonym for "trabalho" which maps to work
            result = rt._select_user_memory_context("como vai minha carreira", memories)
            self.assertIn("### work.md", result)

    def test_about_me_always_gets_baseline_bonus(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = self._build_minimal_runtime(os.path.join(tmp, "mem.sqlite3"))
            memories = {
                "about-me.md": "Sou Carlos",
                "random.md": "dados aleatórios",
            }
            result = rt._select_user_memory_context("qualquer coisa xyz", memories)
            self.assertIn("### about-me.md", result)

    def test_empty_memories_returns_empty_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = self._build_minimal_runtime(os.path.join(tmp, "mem.sqlite3"))
            result = rt._select_user_memory_context("teste", {})
            self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
