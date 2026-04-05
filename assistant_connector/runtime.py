from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv

from assistant_connector.memory_store import ConversationMemoryStore
from assistant_connector.models import AgentDefinition, ChatResponse, ResponseAttachments, ToolExecutionContext
from assistant_connector.tool_registry import ToolRegistry
from utils.timezone_utils import build_time_context

BLOCKED_SCHEDULED_EXECUTION_TOOLS = {
    "create_scheduled_task",
    "edit_scheduled_task",
    "cancel_scheduled_task",
    "list_scheduled_tasks",
    "run_copilot_task",
    "restart_bot_service",
}


def _load_memories_from_dir(*, memories_dir: str, agent_memory_file: str, user_memory_file: str):
    """Load agent memory text and per-user memory files from a directory.

    Returns (agent_memory_text, user_memories_dict).
    """
    if not memories_dir or not os.path.isdir(memories_dir):
        return "", {}

    files = sorted(
        file_name
        for file_name in os.listdir(memories_dir)
        if file_name.lower().endswith(".md")
    )
    agent_file_name = str(agent_memory_file or "personal-assistant.md").strip()
    user_priority_file_name = str(user_memory_file or "about-me.md").strip()

    agent_memory_text = ""
    user_memories: dict[str, str] = {}
    for file_name in files:
        if file_name.lower() == "readme.md":
            continue
        full_path = os.path.join(memories_dir, file_name)
        with open(full_path, "r", encoding="utf-8") as memory_file:
            content = memory_file.read().strip()
        if not content:
            continue
        if file_name == agent_file_name:
            agent_memory_text = content
            continue
        user_memories[file_name] = content

    if user_priority_file_name in user_memories:
        priority_content = user_memories.pop(user_priority_file_name)
        user_memories = {user_priority_file_name: priority_content, **user_memories}

    return agent_memory_text, user_memories


class AssistantRuntime:
    def __init__(
        self,
        *,
        agent: AgentDefinition,
        tool_registry: ToolRegistry,
        memory_store: ConversationMemoryStore,
        project_logger,
        available_agents: list[dict[str, str]],
        max_history_chars: int = 12000,
        max_tool_output_chars: int = 8000,
        agent_memory_text: str = "",
        user_memories: dict[str, str] | None = None,
        memories_dir: str | None = None,
        agent_memory_file: str = "personal-assistant.md",
        user_memory_file: str = "about-me.md",
        max_user_memory_chars: int = 3000,
        openai_client=None,
        user_credential_store=None,
        file_store=None,
    ):
        self._agent = agent
        self._tool_registry = tool_registry
        self._memory_store = memory_store
        self._project_logger = project_logger
        self._available_agents = available_agents
        self._max_history_chars = max(1000, int(max_history_chars))
        self._max_tool_output_chars = max(1000, int(max_tool_output_chars))
        self._agent_memory_text = str(agent_memory_text or "").strip()
        # Static user_memories are kept for backward compatibility when memories_dir is not set
        self._static_user_memories = {
            key: str(value).strip()
            for key, value in (user_memories or {}).items()
            if str(value).strip()
        }
        self._memories_dir = str(memories_dir or "").strip() or None
        self._agent_memory_file = str(agent_memory_file or "personal-assistant.md").strip()
        self._user_memory_file = str(user_memory_file or "about-me.md").strip()
        self._max_user_memory_chars = max(500, int(max_user_memory_chars))
        self._openai_client = openai_client or self._create_openai_client()
        self._user_credential_store = user_credential_store
        self._file_store = file_store

    def process_user_message(
        self,
        *,
        session_id: str,
        user_id: str,
        channel_id: str,
        guild_id: str | None,
        message: str,
    ) -> ChatResponse:
        clean_message = str(message).strip()
        if not clean_message:
            raise ValueError("User message cannot be empty")

        user_memories = self._resolve_user_memories(user_id)

        self._memory_store.append_message(session_id, "user", clean_message)
        history = self._memory_store.get_recent_messages(
            session_id=session_id,
            limit=max(self._agent.memory_window, 1),
        )
        history = self._trim_history_by_chars(history)

        available_tools = self._tool_registry.describe_tools(self._agent.tools)
        user_memories_dir = self._resolve_user_memories_dir(user_id)
        response_attachments = ResponseAttachments()
        context = ToolExecutionContext(
            session_id=session_id,
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
            project_logger=self._project_logger,
            agent=self._agent,
            available_tools=available_tools,
            available_agents=self._available_agents,
            user_credential_store=self._user_credential_store,
            memories_dir=user_memories_dir,
            file_store=self._file_store,
            response_attachments=response_attachments,
        )
        openai_tools = self._tool_registry.get_openai_tools(self._agent.tools)
        response = self._openai_client.responses.create(
            model=self._agent.model,
            input=self._build_input_messages(history, clean_message, user_memories),
            tools=openai_tools,
        )
        write_confirmation_granted = False

        for _ in range(max(self._agent.max_tool_rounds, 1)):
            function_calls = self._extract_function_calls(response)
            if not function_calls:
                final_text = self._extract_text_response(response)
                self._memory_store.append_message(session_id, "assistant", final_text)
                return ChatResponse(
                    text=final_text,
                    image_paths=list(response_attachments.images),
                )

            tool_outputs = []
            for function_call in function_calls:
                tool_name = function_call["name"]
                raw_arguments = function_call["arguments"]
                try:
                    arguments = self._parse_tool_arguments(raw_arguments)
                except ValueError as error:
                    arguments = {"_raw_arguments": str(raw_arguments)}
                    result = {
                        "error": "invalid_tool_arguments",
                        "tool_name": tool_name,
                        "details": str(error),
                    }
                else:
                    tool_definition = self._tool_registry.get_tool_definition(tool_name)
                    if tool_definition.write_operation and (write_confirmation_granted or tool_definition.auto_confirm):
                        if not bool(arguments.get("confirmed", False)):
                            arguments = {**arguments, "confirmed": True}
                            warn_log = getattr(context.project_logger, "warning", None)
                            if callable(warn_log):
                                warn_log(
                                    "Auto-injected confirmation after prior model confirmation: tool=%s session_id=%s user_id=%s",
                                    tool_name,
                                    context.session_id,
                                    context.user_id,
                                )
                    if tool_definition.write_operation and bool(arguments.get("confirmed", False)):
                        write_confirmation_granted = True
                    result = self._execute_tool_call(tool_name, arguments, context)
                self._memory_store.log_tool_call(
                    session_id=session_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                )
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": function_call["call_id"] or f"missing-call-id-{tool_name}",
                        "output": self._serialize_tool_output(result),
                    }
                )

            next_input = list(tool_outputs)
            dynamic_guidance_message = self._build_dynamic_tool_guidance_message(function_calls)
            if dynamic_guidance_message is not None:
                next_input.append(dynamic_guidance_message)

            response = self._openai_client.responses.create(
                model=self._agent.model,
                previous_response_id=self._item_get(response, "id"),
                input=next_input,
                tools=openai_tools,
            )

        fallback_message = (
            "Não consegui concluir com segurança agora. "
            "Tente reformular ou dividir em passos menores."
        )
        self._memory_store.append_message(session_id, "assistant", fallback_message)
        return ChatResponse(
            text=fallback_message,
            image_paths=list(response_attachments.images),
        )

    def reset_session(self, *, session_id: str) -> None:
        self._memory_store.clear_session(session_id)

    def _build_input_messages(
        self,
        history: list[dict[str, str]],
        user_message: str,
        user_memories: dict[str, str],
    ) -> list[dict[str, str]]:
        system_message = (
            f"{self._agent.system_prompt}\n\n"
            "Se o usuário perguntar quais tools existem, use list_available_tools para listar nomes e finalidade.\n\n"
            "Formato para resposta no Telegram:\n"
            "- Sempre responda em Markdown.\n"
            "- Use estrutura (títulos H2, listas, emojis) apenas quando isso organiza a informação de verdade: listas de tarefas, agenda, análises com múltiplos itens. Para respostas curtas, conversacionais, motivacionais ou diretas, escreva em prosa — sem título forçado, sem bullet points desnecessários.\n"
            "- Quando usar emojis, prefira os que têm função visual clara: ✅ concluído, ⚠️ atenção, 🔴 urgente, 🟡 médio, 🟢 baixo, 📅 data, 💡 dica, 🎯 foco.\n"
            "- Use negrito (**) para destacar o que é realmente importante, não para decorar.\n"
            "- Use blockquote (> texto) para alertas ou observações que merecem destaque.\n"
            "- Mire em respostas com até 1500 caracteres e, sempre que possível, não ultrapasse 1800.\n"
            "- Nunca responda em JSON bruto.\n\n"
            f"{self._build_email_style_guidance()}\n\n"
            f"{self._build_time_context_guidance()}"
        )
        if self._agent_memory_text:
            system_message = (
                f"{system_message}\n\n"
                "Memória persistente do agente (estilo, tom e prioridades operacionais):\n"
                f"{self._truncate_text(self._agent_memory_text, 2000)}"
            )

        messages = [{"role": "system", "content": system_message}]
        user_memory_context = self._select_user_memory_context(user_message, user_memories)
        if user_memory_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Contexto persistente do usuário (use somente quando for relevante para a solicitação):\n"
                        f"{user_memory_context}"
                    ),
                }
            )
        return messages + [
            {"role": message["role"], "content": message["content"]}
            for message in history
        ]

    def _resolve_user_memories_dir(self, user_id: str) -> str | None:
        """Return the resolved per-user memories directory path, or None if not configured."""
        if not self._memories_dir:
            return None
        # Sanitize user_id: Telegram user IDs are integers, so only digits are valid
        safe_user_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(user_id))
        if not safe_user_id:
            return None
        base = os.path.realpath(self._memories_dir)
        user_dir = os.path.realpath(os.path.join(base, safe_user_id))
        # Ensure path stays inside memories_dir (guards against symlink escape)
        if not user_dir.startswith(base + os.sep) and user_dir != base:
            return None
        if os.path.isdir(user_dir):
            return user_dir
        # Fallback: use root memories dir if no user-specific subfolder exists yet
        if os.path.isdir(self._memories_dir):
            return self._memories_dir
        # Keep configured path when base dir does not exist yet so write tools can create it.
        return self._memories_dir

    def _resolve_user_memories(self, user_id: str) -> dict[str, str]:
        """Load memory files for the given user from their memories subfolder.

        Resolution order:
        1. memories/{user_id}/ — per-user subfolder
        2. memories/ — root fallback if no user subfolder exists
        3. Static memories passed at runtime init (legacy)
        """
        user_dir = self._resolve_user_memories_dir(user_id)
        if user_dir:
            _, user_memories = _load_memories_from_dir(
                memories_dir=user_dir,
                agent_memory_file=self._agent_memory_file,
                user_memory_file=self._user_memory_file,
            )
            return user_memories
        return self._static_user_memories

    def _select_user_memory_context(self, user_message: str, user_memories: dict[str, str]) -> str:
        if not user_memories:
            return ""

        query = str(user_message or "").lower()
        tokens = set(re.findall(r"[a-z0-9à-ÿ_]{3,}", query))
        scored = []
        for file_name, content in user_memories.items():
            sample = f"{file_name.lower()} {content[:1200].lower()}"
            score = 0
            if file_name.lower() in ("about-me.md", "about_me.md", "about-user.md", "about_user.md"):
                score += 1
            score += sum(1 for token in tokens if token in sample)
            if score > 0:
                scored.append((score, file_name, content))

        if not scored:
            first_name = next(iter(user_memories))
            scored = [(1, first_name, user_memories[first_name])]

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[:2]
        chunks = [
            f"### {file_name}\n{self._truncate_text(content, 1400)}"
            for _, file_name, content in selected
        ]
        return self._truncate_text("\n\n".join(chunks), self._max_user_memory_chars)

    def _build_dynamic_tool_guidance_message(self, function_calls: list[dict[str, str]]) -> dict[str, str] | None:
        seen_tool_names = set()
        guidance_entries: list[tuple[int, str, str]] = []
        for function_call in function_calls:
            tool_name = str(function_call.get("name", "")).strip()
            if not tool_name or tool_name in seen_tool_names:
                continue
            seen_tool_names.add(tool_name)
            try:
                tool_definition = self._tool_registry.get_tool_definition(tool_name)
            except ValueError:
                continue
            guidance = str(tool_definition.prompt_guidance or "").strip()
            if not guidance:
                continue
            guidance_entries.append(
                (
                    int(tool_definition.guidance_priority),
                    tool_name,
                    guidance,
                )
            )
        if not guidance_entries:
            return None

        guidance_entries.sort(key=lambda item: (item[0], item[1]))
        content_blocks = [
            "Contexto adicional das ferramentas usadas neste turno (aplique somente se relevante):"
        ]
        for _, tool_name, guidance in guidance_entries:
            content_blocks.append(f"### {tool_name}\n{guidance}")
        return {"role": "system", "content": "\n\n".join(content_blocks)}

    @staticmethod
    def _build_time_context_guidance() -> str:
        time_context = build_time_context()
        return (
            "Contexto temporal operacional:\n"
            f"- Timezone operacional: {time_context['timezone_name']} (UTC offset {time_context['local_utc_offset']})\n"
            f"- Data local atual: {time_context['local_date_iso']}\n"
            f"- Horário local atual (ISO-8601): {time_context['local_now_iso']}\n"
            f"- Horário UTC atual (ISO-8601): {time_context['utc_now_iso']}\n"
            "- Regra: interprete termos relativos de tempo (hoje, amanhã, agora, esta semana) sempre no timezone operacional."
        )

    def _execute_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        logger = getattr(context, "project_logger", None)
        warn_log = getattr(logger, "warning", None)
        if self._is_scheduled_execution_session(context.session_id):
            if tool_name in BLOCKED_SCHEDULED_EXECUTION_TOOLS:
                if callable(warn_log):
                    warn_log(
                        "Blocked scheduled-session tool call: tool=%s session_id=%s user_id=%s",
                        tool_name,
                        context.session_id,
                        context.user_id,
                    )
                return {
                    "error": "tool_not_allowed_during_scheduled_execution",
                    "tool_name": tool_name,
                    "details": (
                        "Scheduled task management tools are blocked during scheduled task execution. "
                        "Execute the scheduled payload instead of modifying schedules."
                    ),
                }
        tool_definition = self._tool_registry.get_tool_definition(tool_name)
        if tool_definition.write_operation and not tool_definition.auto_confirm and not bool(arguments.get("confirmed", False)):
            if callable(warn_log):
                warn_log(
                    "Blocked write tool without confirmation: tool=%s session_id=%s user_id=%s",
                    tool_name,
                    context.session_id,
                    context.user_id,
                )
            return {
                "error": "confirmation_required",
                "message": (
                    "Esta ação altera dados externos. "
                    "Peça confirmação explícita do usuário e use confirmed=true."
                ),
            }
        try:
            result = self._tool_registry.execute_tool(tool_name, arguments, context)
            if isinstance(result, dict) and result.get("error") and callable(warn_log):
                warn_log(
                    "Tool returned error payload: tool=%s error=%s session_id=%s user_id=%s",
                    tool_name,
                    str(result.get("error")),
                    context.session_id,
                    context.user_id,
                )
            return result
        except Exception as error:
            context.project_logger.exception(
                "Tool execution failed: %s (session_id=%s user_id=%s)",
                tool_name,
                context.session_id,
                context.user_id,
            )
            return {
                "error": "tool_execution_failed",
                "tool_name": tool_name,
                "details": str(error),
            }

    @staticmethod
    def _is_scheduled_execution_session(session_id: str) -> bool:
        return ":scheduled:" in str(session_id or "")

    def _extract_function_calls(self, response) -> list[dict[str, str]]:
        output_items = self._item_get(response, "output", []) or []
        calls = []
        for item in output_items:
            if self._item_get(item, "type") != "function_call":
                continue
            calls.append(
                {
                    "name": self._item_get(item, "name", ""),
                    "arguments": self._item_get(item, "arguments", "{}"),
                    "call_id": self._item_get(item, "call_id", ""),
                }
            )
        return calls

    def _extract_text_response(self, response) -> str:
        output_text = self._item_get(response, "output_text")
        if output_text:
            return output_text

        output_items = self._item_get(response, "output", []) or []
        for item in output_items:
            if self._item_get(item, "type") != "message":
                continue
            content_items = self._item_get(item, "content", []) or []
            for content_item in content_items:
                content_type = self._item_get(content_item, "type")
                if content_type in ("output_text", "text"):
                    text = self._item_get(content_item, "text")
                    if text:
                        return str(text)

        return "Desculpe, não consegui gerar uma resposta."

    def _parse_tool_arguments(self, raw_arguments) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return raw_arguments
        text = str(raw_arguments or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("Tool arguments are not valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("Tool arguments must be a JSON object")
        return payload

    def _trim_history_by_chars(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        if not history:
            return history

        selected = []
        total_chars = 0
        for message in reversed(history):
            content = str(message.get("content", ""))
            message_size = len(content)
            if selected and (total_chars + message_size) > self._max_history_chars:
                break
            if not selected and message_size > self._max_history_chars:
                truncated_content = self._truncate_text(content, self._max_history_chars)
                selected.append(
                    {
                        "role": message.get("role", "user"),
                        "content": truncated_content,
                    }
                )
                break
            selected.append(
                {
                    "role": message.get("role", "user"),
                    "content": content,
                }
            )
            total_chars += message_size
        selected.reverse()
        return selected

    def _serialize_tool_output(self, result: dict[str, Any]) -> str:
        payload_json = json.dumps(result, ensure_ascii=False)
        if len(payload_json) <= self._max_tool_output_chars:
            return payload_json
        preview_limit = max(200, self._max_tool_output_chars - 200)
        truncated = {
            "truncated": True,
            "limit_chars": self._max_tool_output_chars,
            "preview": payload_json[:preview_limit],
        }
        return json.dumps(truncated, ensure_ascii=False)

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        marker = "... [truncated]"
        if limit <= len(marker):
            return marker[:limit]
        return f"{text[: limit - len(marker)]}{marker}"

    @staticmethod
    def _build_email_style_guidance() -> str:
        tone = str(os.getenv("EMAIL_ASSISTANT_TONE", "")).strip()
        signature = str(os.getenv("EMAIL_ASSISTANT_SIGNATURE", "")).strip()
        style_guide = str(os.getenv("EMAIL_ASSISTANT_STYLE_GUIDE", "")).strip()
        if not any((tone, signature, style_guide)):
            return (
                "Preferências de email: use tom profissional, claro e cordial.\n"
                "Ao usar send_email, defina o assunto você mesmo e só envie se o destinatário "
                "tiver sido informado explicitamente pelo usuário.\n"
                "No corpo do email, seja estritamente fiel ao pedido do usuário: não adicione "
                "contexto, justificativas, cumprimentos ou fechamento não solicitados."
            )

        lines = ["Preferências de email do usuário:"]
        if tone:
            lines.append(f"- Tom de voz: {tone}")
        if style_guide:
            lines.append(f"- Guia de estilo: {style_guide}")
        if signature:
            lines.append(
                "- A assinatura de email é aplicada automaticamente pela ferramenta send_email; "
                "não inclua assinatura manualmente no corpo."
            )
        lines.append(
            "- Ao usar send_email, defina o assunto você mesmo e só envie se o destinatário "
            "tiver sido informado explicitamente pelo usuário."
        )
        lines.append(
            "- No corpo do email, seja estritamente fiel ao pedido do usuário e não adicione "
            "texto extra não solicitado."
        )
        return "\n".join(lines)

    @staticmethod
    def _item_get(payload, key: str, default=None):
        if isinstance(payload, dict):
            return payload.get(key, default)
        return getattr(payload, key, default)

    @staticmethod
    def _create_openai_client():
        load_dotenv()
        openai_api_key = os.getenv("OPENAI_KEY")
        if not openai_api_key:
            raise ValueError("Missing required environment variable: OPENAI_KEY")
        import openai  # local import to keep module import lightweight for tests

        return openai.OpenAI(api_key=openai_api_key)
