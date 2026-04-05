import asyncio
import os
import re
from html import escape as _html_escape

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from assistant_connector import app_health, create_assistant_service
from assistant_connector.models import ChatResponse
from assistant_connector.scheduler import AssistantScheduledTaskRunner
from gmail_connector import gmail_connector
from openai_connector import llm_api
from utils import create_logger

MAX_TELEGRAM_MESSAGE_LENGTH = 4096
TELEGRAM_CHUNK_TARGET = 3800
ACCESS_DENIED_MESSAGE = (
    "🔒 Access denied: this assistant is restricted to an authorized user."
)


def _truncate_text(text, limit):
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _get_env_int(name, default, *, minimum=1):
    raw_value = str(os.getenv(name, "")).strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return max(parsed, minimum)


def _is_scheduler_enabled():
    raw_value = str(os.getenv("ASSISTANT_SCHEDULER_ENABLED", "1")).strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _is_authorized_telegram_user(user_id: int, allowed_user_ids: set) -> bool:
    """Return True if user_id is in the allowed set. Fail-closed if empty."""
    if not allowed_user_ids:
        return False
    return int(user_id) in allowed_user_ids


def _split_telegram_message_chunks(text, chunk_size=TELEGRAM_CHUNK_TARGET):
    clean_text = str(text or "")
    if len(clean_text) <= chunk_size:
        return [clean_text]

    chunks = []
    remaining = clean_text
    while len(remaining) > chunk_size:
        split_idx = remaining.rfind("\n\n", 0, chunk_size)
        if split_idx <= 0:
            split_idx = remaining.rfind("\n", 0, chunk_size)
        if split_idx <= 0:
            split_idx = remaining.rfind(" ", 0, chunk_size)
        if split_idx <= 0:
            split_idx = chunk_size
        chunk = remaining[:split_idx].strip()
        if not chunk:
            chunk = remaining[:chunk_size]
            split_idx = chunk_size
        chunks.append(chunk)
        remaining = remaining[split_idx:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def _send_telegram_text(send_callable, text, **send_kwargs):
    chunks = _split_telegram_message_chunks(text)
    for chunk in chunks:
        await send_callable(chunk, **send_kwargs)


def _convert_inline_to_html(text):
    """Apply inline Markdown formatting to an already HTML-escaped string segment."""
    parts = re.split(r'(`[^`\n]+`)', text)
    result = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            result.append(f"<code>{_html_escape(part[1:-1])}</code>")
        else:
            s = _html_escape(part)
            s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
            s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
            s = re.sub(r"(?<![_\w])_([^_\n]+)_(?![_\w])", r"<i>\1</i>", s)
            s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
            result.append(s)
    return "".join(result)


def _convert_markdown_text_to_html(text):
    """Convert a plain-text (non-code-block) Markdown section to Telegram HTML."""
    result_lines = []
    for line in text.split("\n"):
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            result_lines.append(f"<b>{_convert_inline_to_html(m.group(1))}</b>")
            continue
        m = re.match(r"^>\s+(.*)", line)
        if m:
            result_lines.append(f"<blockquote>{_convert_inline_to_html(m.group(1))}</blockquote>")
            continue
        m = re.match(r"^(\s*)[*\-]\s+(.*)", line)
        if m:
            indent = len(m.group(1))
            bullet = "\u00a0" * (indent * 2) + "•"
            result_lines.append(f"{bullet} {_convert_inline_to_html(m.group(2))}")
            continue
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)", line)
        if m:
            result_lines.append(
                f"{m.group(1)}{m.group(2)}. {_convert_inline_to_html(m.group(3))}"
            )
            continue
        result_lines.append(_convert_inline_to_html(line))
    return "\n".join(result_lines)


def _markdown_to_telegram_html(text):
    """Convert LLM Markdown output to Telegram HTML.

    Supports headings, **bold**, *italic*, _italic_, `inline code`,
    fenced code blocks, bullet/numbered lists, and ~~strikethrough~~.
    HTML special characters outside code spans are escaped automatically.
    """
    segments = []
    remaining = str(text or "")
    code_block_re = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
    last_end = 0
    for match in code_block_re.finditer(remaining):
        if match.start() > last_end:
            segments.append(("text", remaining[last_end : match.start()]))
        segments.append(("code_block", match.group(1).strip(), match.group(2).rstrip("\n")))
        last_end = match.end()
    if last_end < len(remaining):
        segments.append(("text", remaining[last_end:]))

    parts = []
    for segment in segments:
        if segment[0] == "code_block":
            parts.append(f"<pre><code>{_html_escape(segment[2])}</code></pre>")
        else:
            parts.append(_convert_markdown_text_to_html(segment[1]))
    return "".join(parts)


async def _send_formatted_response(send_callable, markdown_text, **send_kwargs):
    """Chunk Markdown text and send each chunk as Telegram HTML."""
    chunks = _split_telegram_message_chunks(markdown_text)
    for chunk in chunks:
        await send_callable(
            _markdown_to_telegram_html(chunk), parse_mode=ParseMode.HTML, **send_kwargs
        )


def _is_markdown_formatted(text):
    stripped = str(text or "").lstrip()
    return (
        stripped.startswith("#")
        or stripped.startswith("- ")
        or stripped.startswith("* ")
        or stripped.startswith("1. ")
        or "```" in stripped
    )


def _ensure_markdown_response(text):
    clean_text = str(text or "").strip()
    if not clean_text:
        clean_text = "Desculpe, não consegui responder agora."
    if _is_markdown_formatted(clean_text):
        return clean_text
    return f"## Assistente pessoal\n\n{clean_text}"


def build_bot_response(answer):
    return _ensure_markdown_response(answer)


async def _send_chat_response(reply_to_message, chat_response: ChatResponse) -> None:
    """Send a ChatResponse to the user: text first, then any chart images.

    Args:
        reply_to_message: A Telegram Message object whose reply_* methods will be used.
        chat_response: The structured response from AssistantService.
    """
    await _send_formatted_response(
        reply_to_message.reply_text,
        build_bot_response(chat_response.text),
    )
    for image_path in chat_response.image_paths:
        try:
            with open(image_path, "rb") as img_file:
                await reply_to_message.reply_photo(photo=img_file)
        except Exception as exc:  # noqa: BLE001
            # Log and skip — a failed chart delivery must not break the whole response
            import logging
            logging.getLogger(__name__).warning(
                "Failed to deliver chart image %s: %s", image_path, exc
            )


def build_new_chat_response():
    return "## 🔄 Nova conversa iniciada\n\nPronto! Limpei o histórico e vou responder sem contexto anterior."


def build_error_response(_error=None):
    return "⚠️ Ocorreu um erro ao processar sua solicitação. Tente novamente."


def _resolve_scheduled_delivery_chat_id(task):
    task_payload = task or {}
    channel_id = str(task_payload.get("channel_id", "")).strip()
    if channel_id:
        return channel_id
    return str(task_payload.get("user_id", "")).strip()


def _build_setup_trigger_message(user_id: str, credential_store) -> str:
    """Build the trigger message sent to the LLM when the user runs /setup."""
    from assistant_connector.user_credential_store import _INTEGRATION_REQUIREMENTS
    from assistant_connector.tools.user_credential_tools import _KEY_DESCRIPTIONS

    integrations = credential_store.check_integrations(str(user_id))
    configured_keys = set(credential_store.list_configured_keys(str(user_id)))
    all_required: set[str] = {k for keys in _INTEGRATION_REQUIREMENTS.values() for k in keys}

    integration_lines = []
    for name, active in integrations.items():
        if active:
            integration_lines.append(f"- {name}: ✅ ativa")
        else:
            missing = [k for k in _INTEGRATION_REQUIREMENTS.get(name, []) if k not in configured_keys]
            integration_lines.append(f"- {name}: ❌ inativa (faltam: {', '.join(missing)})")

    optional_missing = sorted(
        k for k in _KEY_DESCRIPTIONS if k not in all_required and k not in configured_keys
    )

    parts = [
        "[SETUP] Status atual das minhas integrações:",
        "\n".join(integration_lines),
    ]
    if optional_missing:
        parts.append(f"Configurações opcionais não definidas: {', '.join(optional_missing)}")
    parts.append("Por favor, me guie pelo processo de configuração.")

    return "\n\n".join(parts)


def create_telegram_application(project_logger=None):
    from assistant_connector.user_credential_store import UserCredentialStore
    from google_auth_server import GoogleOAuthCallbackServer

    logger = project_logger or create_logger.create_logger()

    raw_ids = str(os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")).strip()
    allowed_user_ids: set[int] = {
        int(uid.strip())
        for uid in raw_ids.split(",")
        if uid.strip().isdigit()
    }
    if not allowed_user_ids:
        raise ValueError(
            "TELEGRAM_ALLOWED_USER_IDS is not configured. "
            "Set at least one authorized Telegram user ID."
        )

    bot_token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    transcribe_model = str(os.getenv("AUDIO_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")).strip()
    google_callback_url = str(os.getenv("GOOGLE_OAUTH_CALLBACK_URL", "")).strip()
    google_auth_port = int(os.getenv("GOOGLE_AUTH_SERVER_PORT", "8080"))

    default_memory_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "assistant_memory.sqlite3")
    )
    memory_path = os.getenv("ASSISTANT_MEMORY_PATH", default_memory_path)
    credential_store = UserCredentialStore(db_path=memory_path)
    google_oauth_server = GoogleOAuthCallbackServer(
        credential_store=credential_store,
        port=google_auth_port,
        callback_url=google_callback_url or "http://localhost/auth/google/callback",
        bot_token=bot_token,
        project_logger=logger,
    )

    assistant_service = None
    scheduler_runner = None
    event_loop_holder = [None]

    def get_assistant_service():
        nonlocal assistant_service
        if assistant_service is None:
            assistant_service = create_assistant_service(
                project_logger=logger,
                user_credential_store=credential_store,
            )
        return assistant_service

    def _init_scheduler_runner(application):
        nonlocal scheduler_runner
        if scheduler_runner is not None:
            return scheduler_runner
        if not _is_scheduler_enabled():
            app_health.set_task_checker_status("disabled")
            return None
        scheduler_runner = AssistantScheduledTaskRunner(
            assistant_service_factory=get_assistant_service,
            project_logger=logger,
            poll_interval_seconds=_get_env_int("ASSISTANT_SCHEDULER_POLL_SECONDS", 5, minimum=1),
            stale_running_after_seconds=_get_env_int(
                "ASSISTANT_SCHEDULER_STALE_SECONDS", 300, minimum=1
            ),
            retry_base_seconds=_get_env_int("ASSISTANT_SCHEDULER_RETRY_BASE_SECONDS", 30, minimum=1),
            retry_max_seconds=_get_env_int("ASSISTANT_SCHEDULER_RETRY_MAX_SECONDS", 900, minimum=1),
            on_task_succeeded=lambda outcome: _handle_scheduled_task_success(outcome, application),
        )
        return scheduler_runner

    def _get_scheduler_runner():
        return scheduler_runner

    async def _run_personal_assistant_chat(user_id, chat_id, input_text):
        service = get_assistant_service()
        answer = await asyncio.to_thread(
            service.chat,
            user_id=str(user_id),
            channel_id=str(chat_id),
            guild_id=None,
            message=input_text,
        )
        return answer

    async def _dispatch_scheduled_task_delivery(outcome, application):
        task = outcome.get("task") or {}
        response_text = str(outcome.get("response_text", "")).strip()
        if not response_text:
            return
        notify_chat_id = _resolve_scheduled_delivery_chat_id(task)
        if not notify_chat_id:
            return

        await _send_formatted_response(
            lambda text, **kw: application.bot.send_message(
                chat_id=notify_chat_id, text=text, **kw
            ),
            f"## ⏰ Resultado de tarefa agendada\n\n{build_bot_response(response_text)}",
        )

        email_to = str(task.get("notify_email_to", "")).strip()
        if not email_to:
            return
        allowed_email = str(os.getenv("EMAIL_TO", "")).strip().lower()
        if email_to.lower() != allowed_email:
            logger.warning(
                "Blocked scheduled task email to unauthorized address: %s (allowed: %s)",
                email_to,
                allowed_email,
            )
            return
        task_message = str(task.get("message", "")).strip() or "Tarefa agendada"
        await asyncio.to_thread(
            gmail_connector.send_custom_email,
            project_logger=logger,
            subject=f"[Agendado] {task_message[:80]}",
            body_text=build_bot_response(response_text),
            email_to=email_to,
            body_subtype="plain",
        )

    def _handle_scheduled_task_success(outcome, application):
        loop = event_loop_holder[0]
        if not loop:
            logger.error("Event loop not available for scheduled task delivery")
            return
        future = asyncio.run_coroutine_threadsafe(
            _dispatch_scheduled_task_delivery(outcome, application), loop
        )

        def _on_done(done_future):
            try:
                done_future.result()
            except Exception as error:
                logger.exception("Error delivering scheduled task output: %s", error)

        future.add_done_callback(_on_done)

    async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.is_bot:
            return
        if not _is_authorized_telegram_user(user.id, allowed_user_ids):
            logger.warning("Unauthorized message attempt by user_id=%s username=@%s", user.id, user.username)
            await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return

        input_text = str(update.effective_message.text or "").strip()
        if not input_text:
            return

        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            answer = await _run_personal_assistant_chat(
                user_id=user.id,
                chat_id=update.effective_chat.id,
                input_text=input_text,
            )
            await _send_chat_response(update.effective_message, answer)
        except Exception as error:
            logger.exception("Error running assistant chat")
            await update.effective_message.reply_text(build_error_response(error))

    async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.is_bot:
            return
        if not _is_authorized_telegram_user(user.id, allowed_user_ids):
            logger.warning("Unauthorized audio attempt by user_id=%s username=@%s", user.id, user.username)
            await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return

        message = update.effective_message
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            if message.voice:
                file = await context.bot.get_file(message.voice.file_id)
                filename = "voice_message.ogg"
                mime_type = "audio/ogg"
            elif message.audio:
                file = await context.bot.get_file(message.audio.file_id)
                filename = message.audio.file_name or "audio_file"
                mime_type = message.audio.mime_type or "audio/mpeg"
            else:
                return

            audio_bytes = bytes(await file.download_as_bytearray())
            input_text = await asyncio.to_thread(
                llm_api.transcribe_audio_input,
                audio_bytes,
                filename,
                mime_type,
                logger,
            )
        except Exception as error:
            logger.exception("Error transcribing audio message")
            await message.reply_text(build_error_response(error))
            return

        if not input_text:
            return

        try:
            answer = await _run_personal_assistant_chat(
                user_id=user.id,
                chat_id=update.effective_chat.id,
                input_text=input_text,
            )
            await _send_chat_response(message, answer)
        except Exception as error:
            logger.exception("Error running assistant chat for audio input")
            await message.reply_text(build_error_response(error))

    async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.is_bot:
            return
        if not _is_authorized_telegram_user(user.id, allowed_user_ids):
            logger.warning("Unauthorized document upload by user_id=%s username=@%s", user.id, user.username)
            await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return

        message = update.effective_message
        document = message.document
        if not document:
            return

        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            tg_file = await context.bot.get_file(document.file_id)
            file_bytes = bytes(await tg_file.download_as_bytearray())
        except Exception as error:
            logger.exception("Error downloading document")
            await message.reply_text(build_error_response(error))
            return

        filename = document.file_name or "arquivo"
        mime_type = document.mime_type or ""
        caption = message.caption or ""

        try:
            service = get_assistant_service()
            answer = await asyncio.to_thread(
                service.handle_file_upload,
                user_id=str(user.id),
                channel_id=str(update.effective_chat.id),
                guild_id=None,
                filename=filename,
                file_bytes=file_bytes,
                mime_type=mime_type,
                caption=caption,
            )
            await _send_chat_response(message, answer)
        except Exception as error:
            logger.exception("Error handling document upload")
            await message.reply_text(build_error_response(error))

    async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.is_bot:
            return
        if not _is_authorized_telegram_user(user.id, allowed_user_ids):
            logger.warning("Unauthorized /reset attempt by user_id=%s username=@%s", user.id, user.username)
            await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return

        try:
            service = get_assistant_service()
            await asyncio.to_thread(
                service.reset_chat,
                user_id=str(user.id),
                channel_id=str(update.effective_chat.id),
                guild_id=None,
            )
            await _send_formatted_response(
                update.effective_message.reply_text, build_new_chat_response()
            )
        except Exception as error:
            logger.exception("Error running /reset command")
            await update.effective_message.reply_text(build_error_response(error))

    async def setup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.is_bot:
            return
        if not _is_authorized_telegram_user(user.id, allowed_user_ids):
            logger.warning("Unauthorized /setup attempt by user_id=%s username=@%s", user.id, user.username)
            await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return

        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action=ChatAction.TYPING
            )
            trigger = _build_setup_trigger_message(str(user.id), credential_store)
            answer = await _run_personal_assistant_chat(
                user_id=user.id,
                chat_id=update.effective_chat.id,
                input_text=trigger,
            )
            await _send_chat_response(update.effective_message, answer)
        except Exception as error:
            logger.exception("Error running /setup command")
            await update.effective_message.reply_text(build_error_response(error))

    async def google_auth_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.is_bot:
            return
        if not _is_authorized_telegram_user(user.id, allowed_user_ids):
            logger.warning("Unauthorized /google_auth attempt by user_id=%s", user.id)
            await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return

        if not google_callback_url:
            await update.effective_message.reply_text(
                "⚠️ Integração Google não configurada no servidor. "
                "O administrador precisa definir GOOGLE_OAUTH_CALLBACK_URL no .env."
            )
            return

        try:
            auth_url = google_oauth_server.start_flow(str(user.id))
            msg = (
                "## 🔐 Autorizar conta Google\n\n"
                "Clique no link abaixo para autorizar acesso ao Gmail e Google Calendar:\n\n"
                f"{auth_url}\n\n"
                "O link expira em **10 minutos**. "
                "Após autorizar no navegador, o bot confirmará automaticamente por aqui."
            )
            await _send_formatted_response(update.effective_message.reply_text, msg)
        except ValueError as error:
            await update.effective_message.reply_text(f"⚠️ {error}")
        except Exception as error:
            logger.exception("Error starting Google OAuth flow")
            await update.effective_message.reply_text(build_error_response(error))

    async def post_init(application: Application) -> None:
        event_loop_holder[0] = asyncio.get_running_loop()
        runner = _init_scheduler_runner(application)
        if runner is not None:
            runner.start()
            app_health.set_task_checker_status("running" if runner.is_running() else "stopped")
        else:
            app_health.set_task_checker_status("disabled")
        if google_callback_url:
            google_oauth_server.start()
        app_health.set_bot_status("online")
        commands = [
            ("setup", "Ver e configurar integrações"),
            ("reset", "Iniciar nova conversa"),
            ("google_auth", "Autorizar conta Google (Gmail + Calendar)"),
        ]
        await application.bot.set_my_commands(commands)
        logger.info("Telegram bot online")

        owner_id = os.getenv("COPILOT_OWNER_USER_ID", "").strip()
        if owner_id:
            try:
                await application.bot.send_message(
                    chat_id=int(owner_id),
                    text="✅ Bot is now <b>ONLINE</b> and ready.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                logger.warning("Failed to send startup notification: %s", exc)

    async def post_shutdown(application: Application) -> None:
        if scheduler_runner is not None:
            scheduler_runner.stop()
            app_health.set_task_checker_status("stopped")
        google_oauth_server.stop()
        app_health.set_bot_status("stopped")

    application = (
        Application.builder()
        .token(bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("reset", reset_handler))
    application.add_handler(CommandHandler("new_chat", reset_handler))
    application.add_handler(CommandHandler("setup", setup_handler))
    application.add_handler(CommandHandler("google_auth", google_auth_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, audio_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.assistant_scheduler_runner_getter = _get_scheduler_runner
    return application


def run_telegram_bot():
    load_dotenv()
    app_health.mark_app_started()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise ValueError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
    application = create_telegram_application()
    application.run_polling(drop_pending_updates=True)
