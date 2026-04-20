from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from typing import Optional


ACCEPTED_EXTENSIONS = {".pdf", ".txt", ".csv", ".md", ".docx"}
ACCEPTED_EXTENSIONS_DISPLAY = ", ".join(sorted(ACCEPTED_EXTENSIONS))

DEFAULT_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

logger = logging.getLogger(__name__)


class FileStore:
    """Stores file metadata in SQLite and manages file paths on disk."""

    def __init__(
        self,
        db_path: str,
        files_dir: str,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    ):
        self._db_path = os.path.abspath(db_path)
        self._files_dir = os.path.abspath(files_dir)
        self._max_file_size_bytes = max(1, int(max_file_size_bytes))
        self._lock = threading.Lock()
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_files (
                        file_id      TEXT PRIMARY KEY,
                        user_id      TEXT NOT NULL,
                        original_name TEXT NOT NULL,
                        stored_name  TEXT NOT NULL,
                        mime_type    TEXT NOT NULL DEFAULT '',
                        file_size    INTEGER NOT NULL DEFAULT 0,
                        context_description TEXT NOT NULL DEFAULT '',
                        uploaded_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    )
                """)
                conn.commit()

    def save_file(
        self,
        *,
        user_id: str,
        original_name: str,
        file_bytes: bytes,
        mime_type: str = "",
        context_description: str = "",
    ) -> dict:
        """Save a file to disk and record it in the DB. Returns file metadata."""
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ACCEPTED_EXTENSIONS:
            raise ValueError(
                f"Formato '{ext or 'desconhecido'}' não é aceito. "
                f"Formatos suportados: {ACCEPTED_EXTENSIONS_DISPLAY}"
            )

        file_size = len(file_bytes)
        if file_size > self._max_file_size_bytes:
            max_mb = self._max_file_size_bytes / (1024 * 1024)
            raise ValueError(
                f"Arquivo excede o tamanho máximo permitido "
                f"({file_size / (1024 * 1024):.1f} MB > {max_mb:.0f} MB)."
            )

        file_id = str(uuid.uuid4())
        safe_name = _safe_filename(original_name)
        stored_name = f"{file_id}_{safe_name}"

        user_dir = os.path.join(self._files_dir, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = os.path.join(user_dir, stored_name)

        with open(file_path, "wb") as f:
            f.write(file_bytes)

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO user_files
                        (file_id, user_id, original_name, stored_name, mime_type, file_size, context_description)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        str(user_id),
                        original_name,
                        stored_name,
                        mime_type,
                        len(file_bytes),
                        context_description,
                    ),
                )
                conn.commit()

        return {
            "file_id": file_id,
            "original_name": original_name,
            "mime_type": mime_type,
            "file_size": len(file_bytes),
            "context_description": context_description,
        }

    def get_file(self, *, user_id: str, file_id: str) -> Optional[dict]:
        """Return file metadata dict for the given user and file_id, or None."""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM user_files WHERE file_id = ? AND user_id = ?",
                    (file_id, str(user_id)),
                ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_files(self, *, user_id: str) -> list[dict]:
        """Return all file metadata records for the given user, newest first."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM user_files WHERE user_id = ? ORDER BY uploaded_at DESC",
                    (str(user_id),),
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_file(self, *, user_id: str, file_id: str) -> bool:
        """Delete the file from disk and remove its DB record. Returns True if deleted."""
        record = self.get_file(user_id=user_id, file_id=file_id)
        if record is None:
            return False

        file_path = os.path.join(self._files_dir, str(user_id), record["stored_name"])
        disk_deleted = True
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError as exc:
            logger.error(
                "Failed to delete file from disk: %s (user_id=%s, file_id=%s): %s",
                file_path, user_id, file_id, exc,
            )
            disk_deleted = False

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM user_files WHERE file_id = ? AND user_id = ?",
                    (file_id, str(user_id)),
                )
                conn.commit()
        return True

    def resolve_file_path(self, *, user_id: str, file_id: str) -> Optional[str]:
        """Return the absolute path to the stored file, or None if not found."""
        record = self.get_file(user_id=user_id, file_id=file_id)
        if record is None:
            return None
        user_dir = os.path.abspath(os.path.join(self._files_dir, str(user_id)))
        full_path = os.path.abspath(
            os.path.join(user_dir, record["stored_name"])
        )
        if not full_path.startswith(user_dir + os.sep):
            return None
        return full_path if os.path.isfile(full_path) else None


def _safe_filename(name: str) -> str:
    """Return a filesystem-safe version of the filename."""
    safe = "".join(
        c if c.isalnum() or c in "-_." else "_"
        for c in os.path.basename(name)
    )
    return safe or "file"
