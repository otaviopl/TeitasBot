from __future__ import annotations

import os
from datetime import datetime, timezone

from assistant_connector.models import ToolExecutionContext

_RESERVED_NAMES = frozenset(["readme.md"])


def _is_safe_filename_char(char: str) -> bool:
    # Accept letters/digits (including unicode letters), spaces, hyphens, underscores and dots.
    return char.isalnum() or char in {" ", "-", "_", "."}


def _get_user_memories_dir(context: ToolExecutionContext) -> str:
    memories_dir = str(context.memories_dir or "").strip()
    if not memories_dir:
        raise ValueError(
            "Memories directory is not configured. "
            "Set ASSISTANT_MEMORIES_DIR and ensure the user subfolder exists."
        )
    return memories_dir


def _validate_filename(file_name: str) -> str:
    raw = str(file_name or "").strip()
    if not raw:
        raise ValueError("file_name is required")
    # Reject any path separators or traversal attempts before normalization
    if "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError("file_name must be a plain filename without path separators")
    clean = os.path.basename(raw)
    if not clean:
        raise ValueError("file_name is required")
    if not clean.endswith(".md"):
        raise ValueError("file_name must end with .md")
    if not all(_is_safe_filename_char(char) for char in clean):
        raise ValueError(
            "file_name contains invalid characters — only letters, digits, spaces, hyphens, underscores, and dots are allowed"
        )
    if clean.lower() in _RESERVED_NAMES:
        raise ValueError(f"'{clean}' is a reserved filename and cannot be modified")
    return clean


def list_memory_files(arguments: dict, context: ToolExecutionContext) -> dict:
    """List all memory files available for the current user."""
    memories_dir = _get_user_memories_dir(context)
    if not os.path.isdir(memories_dir):
        return {"files": [], "count": 0, "memories_dir": memories_dir}
    files = sorted(
        f
        for f in os.listdir(memories_dir)
        if f.lower().endswith(".md") and f.lower() not in _RESERVED_NAMES
    )
    return {"files": files, "count": len(files), "memories_dir": memories_dir}


def _resolve_safe_path(memories_dir: str, file_name: str) -> str:
    """Return the absolute path for file_name inside memories_dir.

    Raises ValueError if the resolved path escapes memories_dir or points to a symlink.
    """
    base = os.path.realpath(memories_dir)
    full_path = os.path.join(memories_dir, file_name)
    # Reject symlinks before resolving
    if os.path.islink(full_path):
        raise ValueError("Symlinks are not allowed in the memories directory")
    real_path = os.path.realpath(full_path)
    if not real_path.startswith(base + os.sep) and real_path != base:
        raise ValueError("Path traversal detected")
    return full_path


def read_memory_file(arguments: dict, context: ToolExecutionContext) -> dict:
    """Read the full contents of a memory file for the current user."""
    file_name = _validate_filename(arguments.get("file_name", ""))
    memories_dir = _get_user_memories_dir(context)
    full_path = _resolve_safe_path(memories_dir, file_name)
    if not os.path.isfile(full_path):
        return {"error": "file_not_found", "file_name": file_name, "memories_dir": memories_dir}
    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"file_name": file_name, "content": content, "chars": len(content)}


def edit_memory_file(arguments: dict, context: ToolExecutionContext) -> dict:
    """Create or update a memory file for the current user.

    mode='replace' overwrites the file with new content.
    mode='append' adds new content at the end of the file.
    """
    file_name = _validate_filename(arguments.get("file_name", ""))
    content = str(arguments.get("content", ""))
    mode = str(arguments.get("mode", "append")).strip().lower()
    if mode not in ("append", "replace"):
        raise ValueError("mode must be 'append' or 'replace'")

    memories_dir = _get_user_memories_dir(context)
    os.makedirs(memories_dir, exist_ok=True, mode=0o700)
    full_path = _resolve_safe_path(memories_dir, file_name)

    if mode == "replace":
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        action = "replaced"
    else:
        today_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stamped_content = f"[{today_stamp}] {content}"
        with open(full_path, "a", encoding="utf-8") as f:
            current_size = f.seek(0, 2)  # Seek to end — atomic size check
            if current_size > 0:
                f.write("\n")
            f.write(stamped_content)
        action = "appended"

    result = {
        "status": "ok",
        "file_name": file_name,
        "action": action,
        "chars_written": len(content),
        "memories_dir": memories_dir,
    }

    store = getattr(context, "memory_store", None)
    if store is not None:
        try:
            store.log_memory_edit(
                user_id=context.user_id,
                file_name=file_name,
                action=action,
                chars_written=len(content),
                source="assistant" if getattr(context, "session_id", "").startswith("scheduled-") else "user",
            )
        except Exception:
            pass  # audit is best-effort; never fail the actual edit

    return result
