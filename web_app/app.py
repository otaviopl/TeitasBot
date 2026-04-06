from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from web_app.auth import create_access_token
from web_app.dependencies import get_assistant_service, get_credential_store, get_current_user, get_google_oauth, get_health_store, get_user_store
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


# ---- Notion connectivity check (Notes only — other data is in SQLite) ----

_NOTION_DATABASES = {
    "Anotações": ("notion_notes_db_id", "NOTION_NOTES_DB_ID"),
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


# ---- Memories endpoints ----


@app.get("/api/memories")
async def list_memories(user: dict = Depends(get_current_user)):
    """List and return contents of user memory files."""
    import re

    user_id = f"web:{user['username']}"
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", user_id)

    base_dir = os.getenv(
        "ASSISTANT_MEMORIES_DIR",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "memories")),
    )
    user_dir = os.path.join(base_dir, safe_id)

    if not os.path.isdir(user_dir):
        return {"files": [], "count": 0}

    reserved = {"readme.md"}
    files = sorted(
        f
        for f in os.listdir(user_dir)
        if f.lower().endswith(".md") and f.lower() not in reserved
    )

    result = []
    for fname in files:
        fpath = os.path.join(user_dir, fname)
        real = os.path.realpath(fpath)
        if not real.startswith(os.path.realpath(user_dir)):
            continue  # path traversal guard
        try:
            with open(real, "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            content = "(erro ao ler arquivo)"

        # Derive a display name from filename
        display_name = fname.replace("-", " ").replace("_", " ")
        if display_name.lower().endswith(".md"):
            display_name = display_name[:-3]
        display_name = display_name.title()

        result.append({
            "filename": fname,
            "display_name": display_name,
            "content": content,
        })

    return {"files": result, "count": len(result)}


# ---- Health (meals + exercises) endpoints ----


_VALID_MEAL_TYPES = {"ALMOÇO", "JANTAR", "LANCHE", "CAFÉ DA MANHÃ", "SUPLEMENTO"}

_MAX_FOOD_LEN = 200
_MAX_ACTIVITY_LEN = 200
_MAX_QUANTITY_LEN = 100
_MAX_OBSERVATIONS_LEN = 500
_MAX_CALORIES = 50000
_MAX_FUTURE_DAYS = 30


def _parse_date_param(date_str: str | None, default_date=None) -> str:
    """Validate and return a YYYY-MM-DD date string."""
    import datetime as _dt

    from utils.timezone_utils import today_in_configured_timezone

    if not date_str:
        return (default_date or today_in_configured_timezone()).isoformat()
    try:
        parsed = _dt.date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be a valid YYYY-MM-DD")
    today = today_in_configured_timezone()
    if (parsed - today).days > _MAX_FUTURE_DAYS:
        raise HTTPException(status_code=400, detail=f"date cannot be more than {_MAX_FUTURE_DAYS} days in the future")
    return parsed.isoformat()


@app.get("/api/health/dashboard")
async def health_dashboard(
    date: str | None = Query(None, description="YYYY-MM-DD, defaults to today"),
    user: dict = Depends(get_current_user),
):
    """Daily health dashboard: meals + exercises + totals for a given date."""
    from assistant_connector.health_store import HealthStore

    target_date = _parse_date_param(date)
    user_id = f"web:{user['username']}"
    store: HealthStore = get_health_store()

    async def fetch_meals():
        try:
            return await asyncio.to_thread(
                store.list_meals_by_date_range,
                user_id=user_id,
                start_date=target_date,
                end_date=target_date,
            )
        except Exception:
            return []

    async def fetch_exercises():
        try:
            return await asyncio.to_thread(
                store.list_exercises_by_date_range,
                user_id=user_id,
                start_date=target_date,
                end_date=target_date,
            )
        except Exception:
            return []

    meals, exercises = await asyncio.gather(fetch_meals(), fetch_exercises())

    calories_consumed = sum(float(m.get("calories") or 0) for m in meals)
    calories_burned = sum(float(e.get("calories") or 0) for e in exercises)

    return {
        "date": target_date,
        "meals": meals,
        "exercises": exercises,
        "totals": {
            "calories_consumed": round(calories_consumed, 1),
            "calories_burned": round(calories_burned, 1),
            "balance": round(calories_consumed - calories_burned, 1),
        },
    }


@app.get("/api/health/weekly")
async def health_weekly(
    end_date: str | None = Query(None, description="YYYY-MM-DD, defaults to today"),
    user: dict = Depends(get_current_user),
):
    """Weekly summary: per-day calorie totals for the last 7 days."""
    import datetime as _dt

    from assistant_connector.health_store import HealthStore

    target_end = _parse_date_param(end_date)
    end = _dt.date.fromisoformat(target_end)
    start = end - _dt.timedelta(days=6)
    user_id = f"web:{user['username']}"
    store: HealthStore = get_health_store()

    async def fetch_meals():
        try:
            return await asyncio.to_thread(
                store.list_meals_by_date_range,
                user_id=user_id,
                start_date=start.isoformat(),
                end_date=target_end,
            )
        except Exception:
            return []

    async def fetch_exercises():
        try:
            return await asyncio.to_thread(
                store.list_exercises_by_date_range,
                user_id=user_id,
                start_date=start.isoformat(),
                end_date=target_end,
            )
        except Exception:
            return []

    meals, exercises = await asyncio.gather(fetch_meals(), fetch_exercises())

    days_map: dict[str, dict] = {}
    for i in range(7):
        d = (start + _dt.timedelta(days=i)).isoformat()
        days_map[d] = {"date": d, "calories_consumed": 0.0, "calories_burned": 0.0, "meal_count": 0, "exercise_count": 0}

    for m in meals:
        d = str(m.get("date", ""))[:10]
        if d in days_map:
            days_map[d]["calories_consumed"] += float(m.get("calories") or 0)
            days_map[d]["meal_count"] += 1

    for e in exercises:
        d = str(e.get("date", ""))[:10]
        if d in days_map:
            days_map[d]["calories_burned"] += float(e.get("calories") or 0)
            days_map[d]["exercise_count"] += 1

    days = []
    for d in sorted(days_map):
        entry = days_map[d]
        entry["calories_consumed"] = round(entry["calories_consumed"], 1)
        entry["calories_burned"] = round(entry["calories_burned"], 1)
        days.append(entry)

    return {"days": days}


class CreateMealRequest(BaseModel):
    food: str
    meal_type: str
    quantity: str
    estimated_calories: float


@app.post("/api/health/meals")
async def create_health_meal(
    body: CreateMealRequest,
    user: dict = Depends(get_current_user),
):
    """Register a meal in the local SQLite health database."""
    from assistant_connector.health_store import HealthStore
    from assistant_connector.health_store import parse_quantity_details, normalize_quantity

    food = body.food.strip()
    if not food or len(food) > _MAX_FOOD_LEN:
        raise HTTPException(status_code=400, detail=f"food must be 1-{_MAX_FOOD_LEN} characters")
    meal_type = body.meal_type.strip().upper()
    if meal_type not in _VALID_MEAL_TYPES:
        raise HTTPException(status_code=400, detail=f"meal_type must be one of: {', '.join(sorted(_VALID_MEAL_TYPES))}")
    quantity = body.quantity.strip()
    if not quantity or len(quantity) > _MAX_QUANTITY_LEN:
        raise HTTPException(status_code=400, detail=f"quantity must be 1-{_MAX_QUANTITY_LEN} characters")
    if body.estimated_calories <= 0 or body.estimated_calories > _MAX_CALORIES:
        raise HTTPException(status_code=400, detail=f"estimated_calories must be between 0 and {_MAX_CALORIES}")

    from utils.timezone_utils import today_iso_in_configured_timezone

    normalized_amount = None
    normalized_unit = None
    try:
        qty_details = parse_quantity_details(quantity)
        qty_normalized = normalize_quantity(qty_details)
        normalized_amount = qty_normalized["amount"]
        normalized_unit = qty_normalized["unit"]
    except (ValueError, Exception):
        pass

    user_id = f"web:{user['username']}"
    store: HealthStore = get_health_store()
    result = await asyncio.to_thread(
        store.create_meal,
        user_id=user_id,
        food=food,
        meal_type=meal_type,
        quantity=quantity,
        calories=body.estimated_calories,
        date=today_iso_in_configured_timezone(),
        normalized_amount=normalized_amount,
        normalized_unit=normalized_unit,
    )
    return {"status": "created", "meal": result}


class CreateExerciseRequest(BaseModel):
    activity: str
    calories: float
    observations: str = ""
    done: bool = True


@app.post("/api/health/exercises")
async def create_health_exercise(
    body: CreateExerciseRequest,
    user: dict = Depends(get_current_user),
):
    """Register an exercise in the local SQLite health database."""
    from assistant_connector.health_store import HealthStore

    activity = body.activity.strip()
    if not activity or len(activity) > _MAX_ACTIVITY_LEN:
        raise HTTPException(status_code=400, detail=f"activity must be 1-{_MAX_ACTIVITY_LEN} characters")
    if body.calories <= 0 or body.calories > _MAX_CALORIES:
        raise HTTPException(status_code=400, detail=f"calories must be between 0 and {_MAX_CALORIES}")
    observations = body.observations.strip()
    if len(observations) > _MAX_OBSERVATIONS_LEN:
        raise HTTPException(status_code=400, detail=f"observations must be at most {_MAX_OBSERVATIONS_LEN} characters")

    from utils.timezone_utils import today_iso_in_configured_timezone

    user_id = f"web:{user['username']}"
    store: HealthStore = get_health_store()
    result = await asyncio.to_thread(
        store.create_exercise,
        user_id=user_id,
        activity=activity,
        calories=body.calories,
        date=today_iso_in_configured_timezone(),
        observations=observations,
        done=body.done,
    )
    return {"status": "created", "exercise": result}


class UpdateExerciseRequest(BaseModel):
    activity: str | None = None
    calories: float | None = None
    observations: str | None = None
    done: bool | None = None


@app.patch("/api/health/exercises/{exercise_id}")
async def update_health_exercise(
    exercise_id: str,
    body: UpdateExerciseRequest,
    user: dict = Depends(get_current_user),
):
    """Update an exercise in the local SQLite health database."""
    from assistant_connector.health_store import HealthStore

    if not exercise_id.strip():
        raise HTTPException(status_code=400, detail="exercise_id is required")

    kwargs: dict = {}
    if body.activity is not None:
        a = body.activity.strip()
        if not a or len(a) > _MAX_ACTIVITY_LEN:
            raise HTTPException(status_code=400, detail=f"activity must be 1-{_MAX_ACTIVITY_LEN} characters")
        kwargs["activity"] = a
    if body.calories is not None:
        if body.calories <= 0 or body.calories > _MAX_CALORIES:
            raise HTTPException(status_code=400, detail=f"calories must be between 0 and {_MAX_CALORIES}")
        kwargs["calories"] = body.calories
    if body.observations is not None:
        if len(body.observations) > _MAX_OBSERVATIONS_LEN:
            raise HTTPException(status_code=400, detail=f"observations must be at most {_MAX_OBSERVATIONS_LEN} characters")
        kwargs["observations"] = body.observations
    if body.done is not None:
        kwargs["done"] = body.done

    if not kwargs:
        raise HTTPException(status_code=400, detail="At least one field must be provided to update")

    user_id = f"web:{user['username']}"
    store: HealthStore = get_health_store()
    try:
        result = await asyncio.to_thread(
            store.update_exercise,
            user_id=user_id,
            exercise_id=exercise_id.strip(),
            **kwargs,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ---- Template reader ----

def _read_template(name: str) -> str:
    path = os.path.join(_TEMPLATES_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
