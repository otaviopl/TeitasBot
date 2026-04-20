from __future__ import annotations

import os

from web_app.user_store import WebUserStore

_default_db_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "assistant_memory.sqlite3")
)
_user_store = WebUserStore(db_path=os.getenv("ASSISTANT_MEMORY_PATH", _default_db_path))

# Cache: web:username -> UUID
_uid_cache: dict[str, str] = {}


def _resolve_user_id(context_user_id: str) -> str:
    """Resolve context user_id (web:username) to the web_users UUID."""
    if context_user_id in _uid_cache:
        return _uid_cache[context_user_id]

    if context_user_id.startswith("web:"):
        username = context_user_id.split(":", 1)[1]
        user = _user_store.get_user_by_username(username)
        if user:
            _uid_cache[context_user_id] = user["id"]
            return user["id"]

    # Fallback: use context_user_id directly
    return context_user_id


def list_notes(arguments: dict, context) -> dict:
    """List user notes with optional tag filter."""
    uid = _resolve_user_id(context.user_id)
    limit = min(max(int(arguments.get("limit", 20)), 1), 100)
    tag = str(arguments.get("tag", "")).strip() or None

    notes = _user_store.list_notes(user_id=uid, limit=limit, tag=tag)
    return {"total": len(notes), "notes": notes}


def read_note(arguments: dict, context) -> dict:
    """Read a specific note by ID."""
    uid = _resolve_user_id(context.user_id)
    note_id = str(arguments.get("note_id", "")).strip()
    if not note_id:
        raise ValueError("note_id is required")

    note = _user_store.get_note(note_id=note_id, user_id=uid)
    if not note:
        return {"error": "note_not_found", "note_id": note_id}
    return note


def create_note(arguments: dict, context) -> dict:
    """Create a new note with title, content and optional tags."""
    uid = _resolve_user_id(context.user_id)
    title = str(arguments.get("title", "")).strip()
    content = str(arguments.get("content", "")).strip()
    tags = arguments.get("tags") or []

    if not title:
        raise ValueError("title is required")

    clean_tags = [str(t).strip().lower() for t in tags if str(t).strip()]

    note = _user_store.create_note(user_id=uid, title=title, content=content)

    if clean_tags:
        _user_store.set_note_tags(note["id"], uid, clean_tags)
        note["tags"] = clean_tags

    return {"status": "created", "note": note}


def edit_note(arguments: dict, context) -> dict:
    """Edit an existing note (title, content, and/or tags)."""
    uid = _resolve_user_id(context.user_id)
    note_id = str(arguments.get("note_id", "")).strip()
    if not note_id:
        raise ValueError("note_id is required")

    title = arguments.get("title")
    content = arguments.get("content")
    raw_tags = arguments.get("tags")

    if title is not None:
        title = str(title).strip()
    if content is not None:
        content = str(content)

    tags = None
    if raw_tags is not None:
        tags = [str(t).strip().lower() for t in raw_tags if str(t).strip()]

    if title is None and content is None and tags is None:
        raise ValueError("At least one of title, content, or tags must be provided")

    updated = _user_store.update_note(
        note_id=note_id, user_id=uid, title=title, content=content, tags=tags
    )
    if not updated:
        return {"error": "note_not_found", "note_id": note_id}

    note = _user_store.get_note(note_id=note_id, user_id=uid)
    return {"status": "updated", "note": note}


def delete_note(arguments: dict, context) -> dict:
    """Delete a note by ID."""
    uid = _resolve_user_id(context.user_id)
    note_id = str(arguments.get("note_id", "")).strip()
    if not note_id:
        raise ValueError("note_id is required")

    deleted = _user_store.delete_note(note_id=note_id, user_id=uid)
    if not deleted:
        return {"error": "note_not_found", "note_id": note_id}
    return {"status": "deleted", "note_id": note_id}


def search_notes(arguments: dict, context) -> dict:
    """Search notes by content or title."""
    uid = _resolve_user_id(context.user_id)
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")

    limit = min(max(int(arguments.get("limit", 20)), 1), 50)
    results = _user_store.search_notes(user_id=uid, query=query, limit=limit)
    return {"total": len(results), "results": results}
