"""Notion tools — Notes only (tasks, meals, exercises, expenses, bills migrated to SQLite)."""
from __future__ import annotations

import datetime

from notion_connector import notion_connector


def list_notion_notes(arguments, context):
    days_back = max(int(arguments.get("days_back", 5)), 0)
    days_forward = max(int(arguments.get("days_forward", 5)), 0)
    limit = int(arguments.get("limit", 20))
    limit = min(max(limit, 1), 100)

    notes = notion_connector.collect_notes_around_today(
        days_back=days_back,
        days_forward=days_forward,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "total": len(notes),
        "returned": min(limit, len(notes)),
        "notes": notes[:limit],
    }


def create_notion_note(arguments, context):
    note_name = str(arguments.get("note_name", "")).strip()
    if not note_name:
        raise ValueError("note_name is required")

    tag = str(arguments.get("tag", "GENERAL")).strip() or "GENERAL"
    observations = str(arguments.get("observations", ""))
    url = str(arguments.get("url", "")).strip()

    return notion_connector.create_note_in_notes_db(
        {
            "note_name": note_name,
            "tag": tag,
            "observations": observations,
            "url": url,
        },
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )


def edit_notion_item(arguments, context):
    """Edit a Notion card (note). Tasks are now managed via SQLite."""
    item_type = str(arguments.get("item_type", "")).strip().lower()
    if item_type != "card":
        raise ValueError("item_type must be 'card'")

    page_id = str(arguments.get("page_id", "")).strip()
    if not page_id:
        raise ValueError("page_id is required")

    payload = {
        "item_type": item_type,
        "page_id": page_id,
    }
    content = None
    if "content" in arguments:
        raw_content = str(arguments.get("content", ""))
        if raw_content.strip():
            content = raw_content
            payload["content"] = raw_content
    if "content_mode" in arguments and content is not None:
        content_mode = str(arguments.get("content_mode", "")).strip().lower()
        if content_mode and content_mode not in {"append", "replace"}:
            raise ValueError("content_mode must be 'append' or 'replace'")
        if content_mode:
            payload["content_mode"] = content_mode

    if "note_name" in arguments:
        note_name = str(arguments.get("note_name", "")).strip()
        if note_name:
            payload["note_name"] = note_name
    if "tag" in arguments:
        tag = str(arguments.get("tag", "")).strip()
        if tag:
            payload["tag"] = tag
    if "observations" in arguments:
        observations = str(arguments.get("observations", ""))
        if observations.strip():
            payload["observations"] = observations
    if "url" in arguments:
        url = str(arguments.get("url", "")).strip()
        if url:
            payload["url"] = url
    if "date" in arguments:
        date_value = str(arguments.get("date", "")).strip()
        if date_value:
            try:
                datetime.date.fromisoformat(date_value)
            except ValueError:
                raise ValueError("date must be a valid ISO date (YYYY-MM-DD)")
            payload["date"] = date_value
    if set(payload.keys()) == {"item_type", "page_id"}:
        raise ValueError("At least one card field is required")

    return notion_connector.update_notion_page(
        payload,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )

