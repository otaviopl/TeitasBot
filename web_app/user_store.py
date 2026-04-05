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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
