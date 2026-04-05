from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os

from dotenv import load_dotenv

from assistant_connector.config_loader import load_assistant_configuration
from assistant_connector.file_store import FileStore, ACCEPTED_EXTENSIONS, ACCEPTED_EXTENSIONS_DISPLAY
from assistant_connector.memory_store import ConversationMemoryStore
from assistant_connector.models import ChatResponse
from assistant_connector.runtime import AssistantRuntime, _load_memories_from_dir
from assistant_connector.tool_registry import ToolRegistry


class AssistantService:
    def __init__(self, runtime: AssistantRuntime, file_store: FileStore | None = None):
        self._runtime = runtime
        self._file_store = file_store

    def chat(
        self,
        *,
        user_id: str,
        channel_id: str,
        guild_id: str | None,
        message: str,
    ) -> ChatResponse:
        session_id = self.build_session_id(
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
        )
        return self._runtime.process_user_message(
            session_id=session_id,
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
            message=message,
        )

    def reset_chat(
        self,
        *,
        user_id: str,
        channel_id: str,
        guild_id: str | None,
    ) -> None:
        session_id = self.build_session_id(
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
        )
        self._runtime.reset_session(session_id=session_id)

    def handle_file_upload(
        self,
        *,
        user_id: str,
        channel_id: str,
        guild_id: str | None,
        filename: str,
        file_bytes: bytes,
        mime_type: str = "",
        caption: str = "",
    ) -> ChatResponse:
        """
        Validate and store an uploaded file, then notify the assistant so it can
        acknowledge the upload in context.

        Returns the assistant's ChatResponse, or an error ChatResponse if the format
        is not accepted.
        """
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ACCEPTED_EXTENSIONS:
            return ChatResponse(
                text=(
                    f"❌ O formato *{ext or 'desconhecido'}* não é aceito pelo sistema.\n"
                    f"Formatos suportados: {ACCEPTED_EXTENSIONS_DISPLAY}"
                )
            )

        if self._file_store is None:
            return ChatResponse(text="❌ O gerenciamento de arquivos não está configurado neste ambiente.")

        try:
            record = self._file_store.save_file(
                user_id=str(user_id),
                original_name=filename,
                file_bytes=file_bytes,
                mime_type=mime_type,
                context_description=caption,
            )
        except ValueError as exc:
            return ChatResponse(text=f"❌ {exc}")

        context_note = f" Contexto informado: \"{caption}\"." if caption.strip() else ""
        notification = (
            f"[Sistema] Arquivo '{filename}' foi enviado pelo usuário e armazenado com sucesso. "
            f"ID do arquivo: {record['file_id']}. Tamanho: {record['file_size']} bytes.{context_note} "
            f"Confirme o recebimento para o usuário e informe o que pode fazer com ele."
        )
        return self.chat(
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
            message=notification,
        )

    def schedule_chat(
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
    ) -> str:
        return self._runtime._memory_store.create_scheduled_task(
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
            message=message,
            scheduled_for=scheduled_for,
            scheduled_timezone=scheduled_timezone,
            notify_email_to=notify_email_to,
            recurrence_pattern=recurrence_pattern,
            max_attempts=max_attempts,
        )

    def _log_exception(self, message: str, *args) -> None:
        logger = getattr(self._runtime, "_project_logger", None)
        if logger is None:
            return
        logger.exception(message, *args)

    def run_scheduled_tasks_once(
        self,
        *,
        now_utc: str | None = None,
        stale_running_after_seconds: int = 300,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 900,
    ) -> int:
        outcome = self.execute_next_scheduled_task(
            now_utc=now_utc,
            stale_running_after_seconds=stale_running_after_seconds,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )
        return 1 if outcome["processed"] else 0

    def execute_next_scheduled_task(
        self,
        *,
        now_utc: str | None = None,
        stale_running_after_seconds: int = 300,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 900,
    ) -> dict[str, object]:
        current_time = now_utc or _utc_now_iso()
        safe_retry_base = max(1, int(retry_base_seconds))
        safe_retry_max = max(safe_retry_base, int(retry_max_seconds))
        task = self._runtime._memory_store.claim_next_scheduled_task(
            now_utc=current_time,
            stale_running_after_seconds=stale_running_after_seconds,
        )
        if task is None:
            return {"processed": False}
        task_id = str(task["task_id"])
        scheduled_session_id = self.build_scheduled_session_id(
            task_id=task_id,
            user_id=str(task["user_id"]),
            channel_id=str(task["channel_id"]),
            guild_id=str(task["guild_id"]) if task["guild_id"] else None,
        )
        try:
            chat_response = self._runtime.process_user_message(
                session_id=scheduled_session_id,
                user_id=str(task["user_id"]),
                channel_id=str(task["channel_id"]),
                guild_id=str(task["guild_id"]) if task["guild_id"] else None,
                message=_build_scheduled_execution_message(
                    str(task["message"]),
                    task_type=str(task.get("task_type", "general")),
                ),
            )
            response_text = chat_response.text
        except Exception as error:
            attempt_count = int(task["attempt_count"])
            max_attempts = int(task["max_attempts"])
            finished_at = current_time
            try:
                if attempt_count >= max_attempts:
                    self._runtime._memory_store.mark_scheduled_task_failed(
                        task_id=task_id,
                        finished_at=finished_at,
                        error_text=str(error),
                    )
                    return {"processed": True, "status": "failed", "task": task}
                delay_seconds = min(safe_retry_base * (2 ** max(attempt_count - 1, 0)), safe_retry_max)
                retry_at = _shift_utc_iso(current_time, delay_seconds)
                self._runtime._memory_store.mark_scheduled_task_retrying(
                    task_id=task_id,
                    retry_at=retry_at,
                    updated_at=finished_at,
                    error_text=str(error),
                )
            except Exception as status_error:
                self._log_exception(
                    "Failed to update scheduled task %s status after execution error: %s",
                    task_id,
                    status_error,
                )
            return {"processed": True, "status": "retrying", "task": task}

        try:
            recurrence_pattern = str(task.get("recurrence_pattern", "none")).strip().lower() or "none"
            if recurrence_pattern == "none":
                self._runtime._memory_store.mark_scheduled_task_succeeded(
                    task_id=task_id,
                    finished_at=current_time,
                    response_text=response_text,
                )
            else:
                self._runtime._memory_store.mark_scheduled_task_recurring_succeeded(
                    task_id=task_id,
                    finished_at=current_time,
                    response_text=response_text,
                )
            persisted = self._runtime._memory_store.get_scheduled_task(task_id)
            return {
                "processed": True,
                "status": "succeeded",
                "task": persisted or task,
                "response_text": response_text,
            }
        except Exception as status_error:
            self._log_exception(
                "Failed to mark scheduled task %s as succeeded: %s",
                task_id,
                status_error,
            )
            retry_at = _shift_utc_iso(current_time, safe_retry_base)
            try:
                self._runtime._memory_store.mark_scheduled_task_retrying(
                    task_id=task_id,
                    retry_at=retry_at,
                    updated_at=current_time,
                    error_text=f"post_execution_status_update_failed: {status_error}",
                )
            except Exception as retry_error:
                self._log_exception(
                    "Failed to requeue scheduled task %s after success update error: %s",
                    task_id,
                    retry_error,
                )
        return {"processed": True, "status": "retrying", "task": task}

    def list_scheduled_tasks(
        self,
        *,
        limit: int = 20,
        statuses: list[str] | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, str]]:
        return self._runtime._memory_store.list_scheduled_tasks(
            limit=limit,
            statuses=statuses,
            user_id=user_id,
        )

    def edit_scheduled_task(
        self,
        *,
        task_id: str,
        message: str | None = None,
        scheduled_for: str | None = None,
        scheduled_timezone: str | None = None,
        notify_email_to: str | None = None,
        recurrence_pattern: str | None = None,
        max_attempts: int | None = None,
        task_type: str | None = None,
    ) -> bool:
        return self._runtime._memory_store.update_scheduled_task(
            task_id=task_id,
            updated_at=_utc_now_iso(),
            message=message,
            scheduled_for=scheduled_for,
            scheduled_timezone=scheduled_timezone,
            notify_email_to=notify_email_to,
            recurrence_pattern=recurrence_pattern,
            max_attempts=max_attempts,
            task_type=task_type,
        )

    def cancel_scheduled_task(self, *, task_id: str, reason: str = "") -> bool:
        return self._runtime._memory_store.cancel_scheduled_task(
            task_id=task_id,
            cancelled_at=_utc_now_iso(),
            reason=reason,
        )

    def get_scheduled_task(self, *, task_id: str) -> dict[str, str] | None:
        return self._runtime._memory_store.get_scheduled_task(task_id)

    @staticmethod
    def build_session_id(*, user_id: str, channel_id: str, guild_id: str | None) -> str:
        return f"{guild_id or 'dm'}:{channel_id}:{user_id}"

    @staticmethod
    def build_scheduled_session_id(*, task_id: str, user_id: str, channel_id: str, guild_id: str | None) -> str:
        return f"{AssistantService.build_session_id(user_id=user_id, channel_id=channel_id, guild_id=guild_id)}:scheduled:{task_id}"


def create_assistant_service(
    *,
    project_logger,
    config_path: str | None = None,
    memory_path: str | None = None,
    agent_id: str | None = None,
    openai_client=None,
    user_credential_store=None,
) -> AssistantService:
    load_dotenv()
    configuration = load_assistant_configuration(config_path=config_path)

    selected_agent_id = agent_id or os.getenv("ASSISTANT_AGENT_ID", "personal_assistant")
    selected_agent = configuration.get_agent(selected_agent_id)
    model_override = str(os.getenv("LLM_MODEL", "")).strip()
    if model_override:
        selected_agent = replace(selected_agent, model=model_override)

    default_memory_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "assistant_memory.sqlite3")
    )
    resolved_memory_path = memory_path or os.getenv("ASSISTANT_MEMORY_PATH", default_memory_path)
    max_messages_per_session = _get_env_int("ASSISTANT_MAX_MESSAGES_PER_SESSION", 300, minimum=1)
    max_tool_calls_per_session = _get_env_int("ASSISTANT_MAX_TOOL_CALLS_PER_SESSION", 300, minimum=1)
    max_message_chars = _get_env_int("ASSISTANT_MAX_STORED_MESSAGE_CHARS", 4000, minimum=200)
    max_tool_payload_chars = _get_env_int("ASSISTANT_MAX_STORED_TOOL_PAYLOAD_CHARS", 12000, minimum=500)
    max_history_chars = _get_env_int("ASSISTANT_MAX_HISTORY_CHARS", 12000, minimum=1000)
    max_tool_output_chars = _get_env_int("ASSISTANT_MAX_TOOL_OUTPUT_CHARS", 8000, minimum=1000)
    max_user_memory_chars = _get_env_int("ASSISTANT_MAX_USER_MEMORY_CHARS", 3000, minimum=500)
    memories_dir = os.getenv(
        "ASSISTANT_MEMORIES_DIR",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "memories")),
    )
    agent_memory_file = os.getenv("ASSISTANT_AGENT_MEMORY_FILE", "personal-assistant.md")
    user_memory_file = os.getenv("ASSISTANT_USER_MEMORY_FILE", "about-me.md")
    agent_memory_text, _ = _load_memories_from_dir(
        memories_dir=memories_dir,
        agent_memory_file=agent_memory_file,
        user_memory_file=user_memory_file,
    )

    memory_store = ConversationMemoryStore(
        resolved_memory_path,
        max_messages_per_session=max_messages_per_session,
        max_tool_calls_per_session=max_tool_calls_per_session,
        max_message_chars=max_message_chars,
        max_tool_payload_chars=max_tool_payload_chars,
    )
    default_files_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "files")
    )
    files_dir = os.getenv("ASSISTANT_FILES_DIR", default_files_dir)
    max_file_size_mb = _get_env_int("ASSISTANT_MAX_FILE_SIZE_MB", 20, minimum=1)
    file_store = FileStore(
        db_path=resolved_memory_path,
        files_dir=files_dir,
        max_file_size_bytes=max_file_size_mb * 1024 * 1024,
    )
    tool_registry = ToolRegistry(configuration.tools)
    runtime = AssistantRuntime(
        agent=selected_agent,
        tool_registry=tool_registry,
        memory_store=memory_store,
        project_logger=project_logger,
        available_agents=_build_agent_summaries(configuration.get_agent_summaries(), model_override),
        max_history_chars=max_history_chars,
        max_tool_output_chars=max_tool_output_chars,
        agent_memory_text=agent_memory_text,
        memories_dir=memories_dir,
        agent_memory_file=agent_memory_file,
        user_memory_file=user_memory_file,
        max_user_memory_chars=max_user_memory_chars,
        openai_client=openai_client,
        user_credential_store=user_credential_store,
        file_store=file_store,
    )
    return AssistantService(runtime=runtime, file_store=file_store)


def _build_agent_summaries(agent_summaries, model_override):
    if not model_override:
        return agent_summaries
    return [
        {
            **agent_summary,
            "model": model_override,
        }
        for agent_summary in agent_summaries
    ]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _shift_utc_iso(base_timestamp: str, delta_seconds: int) -> str:
    normalized = str(base_timestamp or "").strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    shifted = parsed.astimezone(timezone.utc) + timedelta(seconds=int(delta_seconds))
    return shifted.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _get_env_int(name: str, default: int, *, minimum: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(parsed, minimum)


def _build_scheduled_execution_message(task_message: str, *, task_type: str = "general") -> str:
    message = str(task_message or "").strip()
    base = (
        "Contexto: execução automática de tarefa agendada.\n"
        "Ação obrigatória: execute o pedido abaixo agora e devolva o resultado final para o usuário.\n"
        "Regra: não criar, editar, listar ou cancelar tarefas agendadas durante esta execução.\n\n"
    )
    if task_type == "logging_reminder":
        base += (
            "Instrução adicional: antes de responder, use a ferramenta check_daily_logging_status para "
            "verificar se refeições e/ou exercícios já foram registrados hoje.\n"
            "- Se já houver registros suficientes, parabenize o usuário pelo comprometimento como reforço positivo.\n"
            "- Se NÃO houver registros (ou faltarem registros importantes), cobre o preenchimento de forma direta e motivadora.\n"
            "Baseie sua resposta nos dados retornados pela ferramenta.\n\n"
        )
    base += f"Pedido agendado:\n{message}"
    return base
