from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

# Maps credential keys to their .env fallback variable names.
_ENV_FALLBACK: dict[str, str] = {
    "email_from": "EMAIL_FROM",
    "email_to": "EMAIL_TO",
    "display_name": "DISPLAY_NAME",
    "email_tone": "EMAIL_ASSISTANT_TONE",
    "email_signature": "EMAIL_ASSISTANT_SIGNATURE",
    "email_style_guide": "EMAIL_ASSISTANT_STYLE_GUIDE",
    "email_subject_prefix": "EMAIL_ASSISTANT_SUBJECT_PREFIX",
}

# Keys that are stored only in the DB (no .env fallback variable).
_STORE_ONLY_KEYS: frozenset[str] = frozenset(
    [
        "google_token_json",
        "email_important_senders",
        "email_important_keywords",
    ]
)

# Integrations and their minimum required credential keys.
_INTEGRATION_REQUIREMENTS: dict[str, list[str]] = {
    "Email": ["email_from", "email_to"],
    "Google": ["google_token_json"],
}

ALL_VALID_KEYS: frozenset[str] = frozenset(_ENV_FALLBACK.keys()) | _STORE_ONLY_KEYS


class UserCredentialStore:
    """Per-user encrypted credential storage backed by the shared SQLite database."""

    def __init__(self, db_path: str, encryption_key: Optional[str] = None):
        self._db_path = os.path.abspath(db_path)
        self._lock = threading.Lock()
        raw_key = encryption_key or os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
        if not raw_key:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEY is required. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self._fernet = Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the user_credentials table if it does not exist."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_credentials (
                    telegram_user_id TEXT NOT NULL,
                    credential_key   TEXT NOT NULL,
                    credential_value TEXT NOT NULL,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    PRIMARY KEY (telegram_user_id, credential_key)
                )
                """
            )
            conn.commit()

    def set_credential(self, user_id: str, key: str, value: str) -> None:
        """Encrypt and upsert a credential for the given user."""
        normalized_key = str(key).strip().lower()
        encrypted = self._fernet.encrypt(value.encode()).decode()
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_credentials
                    (telegram_user_id, credential_key, credential_value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, credential_key)
                DO UPDATE SET credential_value = excluded.credential_value,
                              updated_at       = excluded.updated_at
                """,
                (str(user_id), normalized_key, encrypted, now, now),
            )
            conn.commit()

    def get_credential(
        self,
        user_id: str,
        key: str,
        *,
        use_env_fallback: bool = True,
    ) -> Optional[str]:
        """Return the decrypted value for a credential, or None if unavailable."""
        normalized_key = str(key).strip().lower()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT credential_value FROM user_credentials "
                "WHERE telegram_user_id = ? AND credential_key = ?",
                (str(user_id), normalized_key),
            ).fetchone()
        if row:
            try:
                return self._fernet.decrypt(row["credential_value"].encode()).decode()
            except InvalidToken:
                return None
        if use_env_fallback:
            env_var = _ENV_FALLBACK.get(normalized_key)
            if env_var:
                return os.getenv(env_var) or None
        return None

    def delete_credential(self, user_id: str, key: str) -> bool:
        """Remove a credential. Returns True if a row was deleted."""
        normalized_key = str(key).strip().lower()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM user_credentials WHERE telegram_user_id = ? AND credential_key = ?",
                (str(user_id), normalized_key),
            )
            conn.commit()
        return cursor.rowcount > 0

    def list_configured_keys(self, user_id: str) -> list[str]:
        """Return the list of keys stored for this user (values are never exposed)."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT credential_key FROM user_credentials "
                "WHERE telegram_user_id = ? ORDER BY credential_key",
                (str(user_id),),
            ).fetchall()
        return [row["credential_key"] for row in rows]

    def check_integrations(self, user_id: str) -> dict[str, bool]:
        """Return which integrations have all their required credentials configured."""
        return {
            integration: all(
                self.get_credential(user_id, k) is not None for k in required_keys
            )
            for integration, required_keys in _INTEGRATION_REQUIREMENTS.items()
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
