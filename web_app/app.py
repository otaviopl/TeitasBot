from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from web_app.auth import create_access_token
from web_app.dependencies import get_assistant_service, get_current_user, get_user_store
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


# ---- Helpers ----

def _build_channel_id(username: str, conversation_id: str | None) -> str:
    if conversation_id:
        return f"web:{username}:{conversation_id}"
    return f"web:{username}"


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

    from assistant_connector.service import AssistantService as AService

    channel_id = _build_channel_id(user["username"], conversation_id)
    user_id = f"web:{user['username']}"
    session_id = AService.build_session_id(user_id=user_id, channel_id=channel_id, guild_id=None)

    messages = service._runtime._memory_store.get_recent_messages(session_id, limit=200)
    return {"messages": messages}


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


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ---- Template reader ----

def _read_template(name: str) -> str:
    path = os.path.join(_TEMPLATES_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
