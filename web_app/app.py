from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from web_app.auth import create_access_token
from web_app.dependencies import get_assistant_service, get_credential_store, get_current_user, get_google_oauth, get_user_store
from web_app.user_store import WebUserStore

load_dotenv()

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

app = FastAPI(title="Personal Assistant PWA", docs_url="/docs", redoc_url=None)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ---- Request / Response models ----

class LoginRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    text: str
    image_urls: list[str] = []


class ConversationCreate(BaseModel):
    title: str = "Nova conversa"


class ConversationRename(BaseModel):
    title: str


class NoteCreate(BaseModel):
    title: str = "Nova anotação"
    content: str = ""


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None


# ---- Helpers ----

_MAX_CONVERSATIONS_PER_USER = 100
_MAX_MESSAGES_PER_CONVERSATION = 40  # 20 exchanges (user + assistant)

def _build_channel_id(username: str, conversation_id: str | None) -> str:
    if conversation_id:
        return f"web:{username}:{conversation_id}"
    return f"web:{username}"


def _build_session_id(username: str, conversation_id: str | None) -> str:
    from assistant_connector.service import AssistantService as AService
    user_id = f"web:{username}"
    channel_id = _build_channel_id(username, conversation_id)
    return AService.build_session_id(user_id=user_id, channel_id=channel_id, guild_id=None)


def _check_message_limit(service, username: str, conversation_id: str | None) -> None:
    """Raise 400 if the conversation has reached the message limit."""
    if not conversation_id:
        return
    try:
        session_id = _build_session_id(username, conversation_id)
        count = service._runtime._memory_store.count_messages(session_id)
        if isinstance(count, int) and count >= _MAX_MESSAGES_PER_CONVERSATION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Conversa atingiu o limite de {_MAX_MESSAGES_PER_CONVERSATION // 2} trocas de mensagens. Crie uma nova conversa.",
            )
    except HTTPException:
        raise
    except Exception:
        pass


def _extract_image_urls(image_paths: list[str]) -> list[str]:
    urls: list[str] = []
    for img_path in image_paths:
        if os.path.isfile(img_path):
            urls.append(f"/api/chat/images/{os.path.basename(img_path)}")
    return urls


# ---- Page routes ----

@app.get("/", response_class=HTMLResponse)
async def index_page():
    return _read_template("login.html")


@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    return _read_template("chat.html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(os.path.join(_STATIC_DIR, "manifest.json"), media_type="application/manifest+json")


# ---- Auth endpoints ----

@app.post("/api/auth/login")
async def login(req: LoginRequest, store: WebUserStore = Depends(get_user_store)):
    user = store.authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user_id=user["id"], username=user["username"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
        },
    }


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
    }


# ---- Conversation endpoints ----

@app.get("/api/conversations")
async def list_conversations(
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    conversations = store.list_conversations(user["id"])
    return {"conversations": conversations}


@app.post("/api/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    store.prune_oldest_conversations(user["id"], _MAX_CONVERSATIONS_PER_USER - 1)
    conv = store.create_conversation(user["id"], body.title)
    return conv


@app.patch("/api/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    body: ConversationRename,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    if not body.title.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title cannot be empty")
    updated = store.rename_conversation(conversation_id, user["id"], body.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "ok"}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
    service=Depends(get_assistant_service),
):
    conv = store.get_conversation(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Clear the session messages from the memory store
    channel_id = _build_channel_id(user["username"], conversation_id)
    user_id = f"web:{user['username']}"
    await asyncio.to_thread(
        service.reset_chat,
        user_id=user_id,
        channel_id=channel_id,
        guild_id=None,
    )

    store.delete_conversation(conversation_id, user["id"])
    return {"status": "ok"}


@app.get("/api/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
    service=Depends(get_assistant_service),
):
    conv = store.get_conversation(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    session_id = _build_session_id(user["username"], conversation_id)
    messages = service._runtime._memory_store.get_recent_messages(session_id, limit=200)
    return {
        "messages": messages,
        "message_count": len(messages),
        "message_limit": _MAX_MESSAGES_PER_CONVERSATION,
    }


# ---- Chat endpoints ----

@app.post("/api/chat")
async def chat_send(
    req: ChatRequest,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
    service=Depends(get_assistant_service),
):
    if not req.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message cannot be empty")

    _check_message_limit(service, user["username"], req.conversation_id)

    user_id = f"web:{user['username']}"
    channel_id = _build_channel_id(user["username"], req.conversation_id)

    response = await asyncio.to_thread(
        service.chat,
        user_id=user_id,
        channel_id=channel_id,
        guild_id=None,
        message=req.message.strip(),
    )

    if req.conversation_id:
        store.touch_conversation(req.conversation_id)

    return {"text": response.text, "image_urls": _extract_image_urls(response.image_paths)}


@app.post("/api/chat/reset")
async def chat_reset(
    user: dict = Depends(get_current_user),
    service=Depends(get_assistant_service),
    conversation_id: str | None = Query(None),
):
    user_id = f"web:{user['username']}"
    channel_id = _build_channel_id(user["username"], conversation_id)

    await asyncio.to_thread(
        service.reset_chat,
        user_id=user_id,
        channel_id=channel_id,
        guild_id=None,
    )
    return {"status": "ok"}


@app.post("/api/chat/upload")
async def chat_upload(
    file: UploadFile = File(...),
    caption: str = Form(""),
    conversation_id: str = Form(""),
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
    service=Depends(get_assistant_service),
):
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    user_id = f"web:{user['username']}"
    conv_id = conversation_id if conversation_id else None

    _check_message_limit(service, user["username"], conv_id)

    channel_id = _build_channel_id(user["username"], conv_id)

    response = await asyncio.to_thread(
        service.handle_file_upload,
        user_id=user_id,
        channel_id=channel_id,
        guild_id=None,
        filename=file.filename or "file",
        file_bytes=file_bytes,
        mime_type=file.content_type or "",
        caption=caption,
    )

    if conv_id:
        store.touch_conversation(conv_id)

    return {"text": response.text, "image_urls": _extract_image_urls(response.image_paths)}


@app.post("/api/chat/audio")
async def chat_audio(
    audio: UploadFile = File(...),
    conversation_id: str = Form(""),
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
    service=Depends(get_assistant_service),
):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty audio")

    from openai_connector import llm_api
    from utils import create_logger

    logger = create_logger.create_logger()
    filename = audio.filename or "recording.webm"
    mime_type = audio.content_type or "audio/webm"

    transcribed_text = await asyncio.to_thread(
        llm_api.transcribe_audio_input,
        audio_bytes,
        filename,
        mime_type,
        logger,
    )

    if not transcribed_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not transcribe audio")

    user_id = f"web:{user['username']}"
    conv_id = conversation_id if conversation_id else None

    _check_message_limit(service, user["username"], conv_id)

    channel_id = _build_channel_id(user["username"], conv_id)

    response = await asyncio.to_thread(
        service.chat,
        user_id=user_id,
        channel_id=channel_id,
        guild_id=None,
        message=transcribed_text,
    )

    if conv_id:
        store.touch_conversation(conv_id)

    return {
        "text": response.text,
        "transcribed_text": transcribed_text,
        "image_urls": _extract_image_urls(response.image_paths),
    }


@app.get("/api/chat/images/{filename}")
async def get_chat_image(filename: str, user: dict = Depends(get_current_user)):
    charts_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "charts_output")
    )
    files_dir = os.getenv(
        "ASSISTANT_FILES_DIR",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "files")),
    )
    safe_filename = os.path.basename(filename)
    for search_dir in [charts_dir, files_dir]:
        candidate = os.path.join(search_dir, safe_filename)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="Image not found")


# ---- Note endpoints ----

@app.get("/api/notes/tags")
async def list_note_tags(
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    tags = store.list_user_tags(user["id"])
    return {"tags": tags}


@app.get("/api/notes")
async def list_notes(
    tag: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    notes = store.list_notes(user["id"], tag=tag)
    return {"notes": notes}


@app.post("/api/notes", status_code=201)
async def create_note(
    body: NoteCreate,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    try:
        note = store.create_note(user["id"], body.title, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return note


@app.get("/api/notes/{note_id}")
async def get_note(
    note_id: str,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    note = store.get_note(note_id, user["id"])
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@app.patch("/api/notes/{note_id}")
async def update_note(
    note_id: str,
    body: NoteUpdate,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    if body.title is not None and not body.title.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title cannot be empty")
    try:
        updated = store.update_note(note_id, user["id"], title=body.title, content=body.content, tags=body.tags)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "ok"}


@app.post("/api/notes/{note_id}/generate-metadata")
async def generate_note_metadata_endpoint(
    note_id: str,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    note = store.get_note(note_id, user["id"])
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    from openai_connector.llm_api import generate_note_metadata, OpenAICallError
    from utils import create_logger

    logger = create_logger.create_logger()
    try:
        metadata = await asyncio.get_event_loop().run_in_executor(
            None, generate_note_metadata, note["content"], logger
        )
    except OpenAICallError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    store.update_note(note_id, user["id"], title=metadata["title"], tags=metadata["tags"])
    return {"title": metadata["title"], "tags": metadata["tags"]}


@app.delete("/api/notes/{note_id}")
async def delete_note(
    note_id: str,
    user: dict = Depends(get_current_user),
    store: WebUserStore = Depends(get_user_store),
):
    deleted = store.delete_note(note_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "ok"}


# ---- Notion connectivity check ----

_NOTION_DATABASES = {
    "Tarefas": ("notion_database_id", "NOTION_DATABASE_ID"),
    "Anotações": ("notion_notes_db_id", "NOTION_NOTES_DB_ID"),
    "Exercícios": ("notion_exercises_db_id", "NOTION_EXERCISES_DB_ID"),
    "Refeições": ("notion_meals_db_id", "NOTION_MEALS_DB_ID"),
    "Despesas": ("notion_expenses_db_id", "NOTION_EXPENSES_DB_ID"),
    "Controle Financeiro": ("notion_monthly_bills_db_id", "NOTION_MONTHLY_BILLS_DB_ID"),
}


def _check_notion_database(db_id: str, api_key: str) -> str:
    """Ping a single Notion database and return 'ok' or 'error'."""
    import requests as _requests

    try:
        resp = _requests.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": "2022-06-28",
            },
            timeout=10,
        )
        return "ok" if resp.status_code == 200 else "error"
    except Exception:
        return "error"


@app.get("/api/notion/check")
async def notion_check(user: dict = Depends(get_current_user)):
    from utils.load_credentials import _resolve

    user_id = f"web:{user['username']}"
    store = get_credential_store()

    api_key = _resolve("notion_api_key", "NOTION_API_KEY", user_id, store)
    if not api_key:
        return {
            "api_key_configured": False,
            "databases": {name: "not_configured" for name in _NOTION_DATABASES},
        }

    databases: dict[str, str] = {}
    for name, (cred_key, env_var) in _NOTION_DATABASES.items():
        db_id = _resolve(cred_key, env_var, user_id, store)
        if not db_id:
            databases[name] = "not_configured"
        else:
            databases[name] = await asyncio.to_thread(_check_notion_database, db_id, api_key)

    return {"api_key_configured": True, "databases": databases}


# ---- Google OAuth endpoints ----

@app.get("/api/google/status")
async def google_status(user: dict = Depends(get_current_user)):
    oauth = get_google_oauth()
    if oauth is None:
        return {"configured": False, "connected": False}
    user_id = f"web:{user['username']}"
    connected = oauth.has_valid_token(user_id)
    return {"configured": True, "connected": connected}


@app.get("/api/google/auth-url")
async def google_auth_url(user: dict = Depends(get_current_user)):
    oauth = get_google_oauth()
    if oauth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth not configured. Set GOOGLE_OAUTH_CALLBACK_URL in .env.",
        )
    user_id = f"web:{user['username']}"
    try:
        auth_url = oauth.start_flow(user_id)
        return {"auth_url": auth_url}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@app.get("/auth/google/callback", response_class=HTMLResponse)
async def google_oauth_callback(
    code: str = "",
    state: str = "",
    error: str = "",
):
    from web_app.google_oauth import _ERROR_HTML, _SUCCESS_HTML

    if error:
        return HTMLResponse(_ERROR_HTML.format(message=f"Google recusou: {error}"), status_code=400)
    if not code or not state:
        return HTMLResponse(_ERROR_HTML.format(message="Parâmetros ausentes."), status_code=400)

    oauth = get_google_oauth()
    if oauth is None:
        return HTMLResponse(_ERROR_HTML.format(message="Google OAuth não configurado no servidor."), status_code=500)

    ok, message, _user_id = oauth.handle_callback(code, state)
    if ok:
        return HTMLResponse(_SUCCESS_HTML, status_code=200)
    return HTMLResponse(_ERROR_HTML.format(message=message), status_code=400)


@app.delete("/api/google/disconnect")
async def google_disconnect(user: dict = Depends(get_current_user)):
    oauth = get_google_oauth()
    if oauth is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google OAuth not configured.")
    user_id = f"web:{user['username']}"
    oauth.revoke_token(user_id)
    return {"status": "ok"}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ---- Template reader ----

def _read_template(name: str) -> str:
    path = os.path.join(_TEMPLATES_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
