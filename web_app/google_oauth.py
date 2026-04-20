"""
Google OAuth2 flow for the FastAPI web app.

Manages pending auth states and exchanges authorization codes for tokens,
storing them encrypted in UserCredentialStore under the web user ID.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from typing import Optional

from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

STATE_TTL_SECONDS = 600  # 10 minutes

_SUCCESS_HTML = """<!DOCTYPE html>
<html><head><title>Autorizado</title><meta charset="utf-8">
<style>body{{font-family:sans-serif;text-align:center;padding:3em}}</style>
</head><body>
<h2>&#x2705; Google autorizado com sucesso!</h2>
<p>Você pode fechar esta aba ou <a href="/chat">voltar ao chat</a>.</p>
<script>setTimeout(function(){{window.location.href='/chat'}}, 3000);</script>
</body></html>"""

_ERROR_HTML = """<!DOCTYPE html>
<html><head><title>Erro</title><meta charset="utf-8">
<style>body{{font-family:sans-serif;text-align:center;padding:3em}}</style>
</head><body>
<h2>&#x274c; Falha na autorização</h2>
<p>{message}</p>
<p><a href="/chat">Voltar ao chat</a></p>
</body></html>"""


class WebGoogleOAuth:
    """Manages Google OAuth2 flow for web app users."""

    def __init__(
        self,
        credential_store,
        *,
        callback_url: str,
        credentials_path: str = "credentials.json",
        client_config: Optional[dict] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._store = credential_store
        self._callback_url = callback_url
        self._credentials_path = credentials_path
        self._client_config = client_config
        self._logger = logger or logging.getLogger(__name__)
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start_flow(self, user_id: str) -> str:
        """Create a pending auth state and return the Google authorization URL."""
        if self._client_config is None and not os.path.exists(self._credentials_path):
            raise ValueError(
                f"Google OAuth client config not found. "
                f"Expected credentials.json at '{self._credentials_path}' or GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET in env."
            )
        self._purge_expired_states()
        state = secrets.token_urlsafe(32)
        flow = self._make_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            state=state,
            prompt="consent",
        )
        with self._lock:
            self._pending[state] = {
                "user_id": str(user_id),
                "expires_at": time.monotonic() + STATE_TTL_SECONDS,
                "flow": flow,
            }
        return auth_url

    def _make_flow(self) -> Flow:
        if self._client_config is not None:
            return Flow.from_client_config(
                self._client_config,
                scopes=SCOPES,
                redirect_uri=self._callback_url,
            )
        return Flow.from_client_secrets_file(
            self._credentials_path,
            scopes=SCOPES,
            redirect_uri=self._callback_url,
        )

    def handle_callback(self, code: str, state: str) -> tuple[bool, str, Optional[str]]:
        """Exchange the authorization code for a token and store it.

        Returns (success, message, user_id).
        """
        with self._lock:
            pending = self._pending.pop(state, None)

        if pending is None:
            return False, "Estado inválido ou expirado. Tente autorizar novamente.", None
        if time.monotonic() > pending["expires_at"]:
            return False, "Link expirado. Tente autorizar novamente.", None

        user_id = pending["user_id"]
        flow = pending.get("flow")
        if flow is None:
            return False, "Sessão de autenticação expirada. Tente autorizar novamente.", None

        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            self._store.set_credential(user_id, "google_token_json", creds.to_json())
            self._logger.info("Google token stored for web user_id=%s", user_id)
            return True, "Autorizado com sucesso.", user_id
        except Exception as exc:
            self._logger.exception("Failed to exchange OAuth code for web user_id=%s", user_id)
            return False, f"Erro ao processar autorização: {exc}", None

    def has_valid_token(self, user_id: str) -> bool:
        """Check if the user has a Google token stored."""
        raw = self._store.get_credential(str(user_id), "google_token_json", use_env_fallback=False)
        return bool(raw)

    def revoke_token(self, user_id: str) -> bool:
        """Remove the stored Google token for a user."""
        return self._store.delete_credential(str(user_id), "google_token_json")

    def _purge_expired_states(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [s for s, v in self._pending.items() if now > v["expires_at"]]
            for s in expired:
                del self._pending[s]
