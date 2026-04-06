from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import bcrypt


class WebUserStore:
    """Manages web users in the shared SQLite database."""

    def __init__(self, db_path: str):
        self._db_path = os.path.abspath(db_path)
        self._lock = threading.Lock()
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web_conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Nova conversa',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_web_conversations_user
                    ON web_conversations (user_id, updated_at DESC)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web_notes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Nova anotação',
                    content TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_web_notes_user
                    ON web_notes (user_id, updated_at DESC)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web_note_tags (
                    note_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (note_id, tag),
                    FOREIGN KEY (note_id) REFERENCES web_notes(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_web_note_tags_tag
                    ON web_note_tags (tag)
            """)

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
    ) -> dict[str, str]:
        clean_username = str(username).strip().lower()
        if not clean_username:
            raise ValueError("Username cannot be empty.")
        if len(clean_username) < 3:
            raise ValueError("Username must be at least 3 characters.")
        if not password or len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        user_id = uuid.uuid4().hex
        now = _utc_now_iso()
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM web_users WHERE username = ?", (clean_username,)
            ).fetchone()
            if existing:
                raise ValueError(f"Username '{clean_username}' already exists.")
            conn.execute(
                """
                INSERT INTO web_users (id, username, password_hash, display_name, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (user_id, clean_username, password_hash, display_name.strip(), now, now),
            )
        return {
            "id": user_id,
            "username": clean_username,
            "display_name": display_name.strip(),
            "is_active": True,
            "created_at": now,
        }

    def authenticate(self, username: str, password: str) -> Optional[dict[str, str]]:
        clean_username = str(username).strip().lower()
        if not clean_username or not password:
            return None

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, display_name, is_active FROM web_users WHERE username = ?",
                (clean_username,),
            ).fetchone()

        if not row:
            return None
        if not row["is_active"]:
            return None
        if not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
            return None

        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
        }

    def get_user_by_username(self, username: str) -> Optional[dict[str, str]]:
        clean_username = str(username).strip().lower()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, display_name, is_active, created_at FROM web_users WHERE username = ?",
                (clean_username,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def get_user_by_id(self, user_id: str) -> Optional[dict[str, str]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, display_name, is_active, created_at FROM web_users WHERE id = ?",
                (str(user_id),),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }

    def list_users(self) -> list[dict[str, str]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, username, display_name, is_active, created_at FROM web_users ORDER BY created_at"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": r["display_name"],
                "is_active": bool(r["is_active"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def deactivate_user(self, username: str) -> bool:
        clean_username = str(username).strip().lower()
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE web_users SET is_active = 0, updated_at = ? WHERE username = ?",
                (now, clean_username),
            )
        return cursor.rowcount > 0

    def change_password(self, username: str, new_password: str) -> bool:
        clean_username = str(username).strip().lower()
        if not new_password or len(new_password) < 6:
            raise ValueError("Password must be at least 6 characters.")
        password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE web_users SET password_hash = ?, updated_at = ? WHERE username = ?",
                (password_hash, now, clean_username),
            )
        return cursor.rowcount > 0

    # ---- Conversation management ----

    def create_conversation(self, user_id: str, title: str = "Nova conversa") -> dict:
        conv_id = uuid.uuid4().hex
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO web_conversations (id, user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conv_id, user_id, title.strip() or "Nova conversa", now, now),
            )
        return {"id": conv_id, "user_id": user_id, "title": title.strip() or "Nova conversa", "created_at": now, "updated_at": now}

    def list_conversations(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, user_id, title, created_at, updated_at FROM web_conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, max(1, limit)),
            ).fetchall()
        return [
            {"id": r["id"], "user_id": r["user_id"], "title": r["title"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
            for r in rows
        ]

    def get_conversation(self, conversation_id: str, user_id: str) -> Optional[dict]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, user_id, title, created_at, updated_at FROM web_conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "user_id": row["user_id"], "title": row["title"], "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> bool:
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE web_conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (title.strip(), now, conversation_id, user_id),
            )
        return cursor.rowcount > 0

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM web_conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                conn.commit()
        return deleted

    def prune_oldest_conversations(self, user_id: str, max_count: int) -> list[str]:
        """Delete oldest conversations exceeding max_count. Returns deleted IDs."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM web_conversations WHERE user_id = ? ORDER BY updated_at DESC, created_at DESC, rowid DESC LIMIT -1 OFFSET ?",
                (user_id, max(1, max_count)),
            ).fetchall()
            deleted_ids = [r["id"] for r in rows]
            if deleted_ids:
                placeholders = ",".join("?" * len(deleted_ids))
                conn.execute(
                    f"DELETE FROM web_conversations WHERE id IN ({placeholders})",
                    deleted_ids,
                )
                conn.commit()
        return deleted_ids

    def touch_conversation(self, conversation_id: str) -> None:
        """Update the updated_at timestamp of a conversation."""
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE web_conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

    # ---- Note management ----

    _MAX_NOTE_CONTENT_BYTES = 512_000  # 500 KB

    def _get_note_tags(self, conn: sqlite3.Connection, note_id: str) -> list[str]:
        rows = conn.execute(
            "SELECT tag FROM web_note_tags WHERE note_id = ? ORDER BY tag",
            (note_id,),
        ).fetchall()
        return [r["tag"] for r in rows]

    def create_note(self, user_id: str, title: str = "Nova anotação", content: str = "") -> dict:
        if len(content.encode("utf-8")) > self._MAX_NOTE_CONTENT_BYTES:
            raise ValueError("Note content exceeds 500 KB limit.")
        note_id = uuid.uuid4().hex
        now = _utc_now_iso()
        clean_title = title.strip() or "Nova anotação"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO web_notes (id, user_id, title, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (note_id, user_id, clean_title, content, now, now),
            )
        return {"id": note_id, "user_id": user_id, "title": clean_title, "content": content, "tags": [], "created_at": now, "updated_at": now}

    def list_notes(self, user_id: str, limit: int = 100, tag: str | None = None) -> list[dict]:
        with self._lock, self._connect() as conn:
            if tag:
                rows = conn.execute(
                    """
                    SELECT n.id, n.user_id, n.title, n.created_at, n.updated_at
                    FROM web_notes n
                    JOIN web_note_tags t ON t.note_id = n.id
                    WHERE n.user_id = ? AND t.tag = ?
                    ORDER BY n.updated_at DESC LIMIT ?
                    """,
                    (user_id, tag, max(1, limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, user_id, title, created_at, updated_at FROM web_notes WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (user_id, max(1, limit)),
                ).fetchall()
            result = []
            for r in rows:
                tags = self._get_note_tags(conn, r["id"])
                result.append({
                    "id": r["id"], "user_id": r["user_id"], "title": r["title"],
                    "tags": tags, "created_at": r["created_at"], "updated_at": r["updated_at"],
                })
        return result

    def get_note(self, note_id: str, user_id: str) -> Optional[dict]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, user_id, title, content, created_at, updated_at FROM web_notes WHERE id = ? AND user_id = ?",
                (note_id, user_id),
            ).fetchone()
            if not row:
                return None
            tags = self._get_note_tags(conn, note_id)
        return {
            "id": row["id"], "user_id": row["user_id"], "title": row["title"],
            "content": row["content"], "tags": tags, "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def update_note(self, note_id: str, user_id: str, title: Optional[str] = None, content: Optional[str] = None, tags: Optional[list[str]] = None) -> bool:
        if content is not None and len(content.encode("utf-8")) > self._MAX_NOTE_CONTENT_BYTES:
            raise ValueError("Note content exceeds 500 KB limit.")
        now = _utc_now_iso()
        updates: list[str] = ["updated_at = ?"]
        params: list = [now]
        if title is not None:
            clean_title = title.strip()
            if not clean_title:
                raise ValueError("Title cannot be empty.")
            updates.append("title = ?")
            params.append(clean_title)
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        params.extend([note_id, user_id])
        sql = f"UPDATE web_notes SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, params)
            if tags is not None and cursor.rowcount > 0:
                self._set_note_tags(conn, note_id, tags)
        return cursor.rowcount > 0

    def _set_note_tags(self, conn: sqlite3.Connection, note_id: str, tags: list[str]) -> None:
        conn.execute("DELETE FROM web_note_tags WHERE note_id = ?", (note_id,))
        clean_tags = list(dict.fromkeys(t.strip().lower() for t in tags if t.strip()))
        for tag in clean_tags:
            conn.execute(
                "INSERT OR IGNORE INTO web_note_tags (note_id, tag) VALUES (?, ?)",
                (note_id, tag),
            )

    def set_note_tags(self, note_id: str, user_id: str, tags: list[str]) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM web_notes WHERE id = ? AND user_id = ?",
                (note_id, user_id),
            ).fetchone()
            if not row:
                return False
            self._set_note_tags(conn, note_id, tags)
        return True

    def list_user_tags(self, user_id: str) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT t.tag
                FROM web_note_tags t
                JOIN web_notes n ON n.id = t.note_id
                WHERE n.user_id = ?
                ORDER BY t.tag
                """,
                (user_id,),
            ).fetchall()
        return [r["tag"] for r in rows]

    def delete_note(self, note_id: str, user_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM web_note_tags WHERE note_id = ?", (note_id,))
            cursor = conn.execute(
                "DELETE FROM web_notes WHERE id = ? AND user_id = ?",
                (note_id, user_id),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                conn.commit()
        return deleted

    def search_notes(self, user_id: str, query: str, limit: int = 20) -> list[dict]:
        """Search notes by title or content using LIKE matching."""
        pattern = f"%{query}%"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, title,
                       substr(content, 1, 300) AS snippet,
                       created_at, updated_at
                FROM web_notes
                WHERE user_id = ? AND (title LIKE ? OR content LIKE ?)
                ORDER BY updated_at DESC LIMIT ?
                """,
                (user_id, pattern, pattern, max(1, min(limit, 100))),
            ).fetchall()
            result = []
            for r in rows:
                tags = self._get_note_tags(conn, r["id"])
                result.append({
                    "id": r["id"], "title": r["title"],
                    "snippet": r["snippet"], "tags": tags,
                    "created_at": r["created_at"], "updated_at": r["updated_at"],
                })
        return result


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
