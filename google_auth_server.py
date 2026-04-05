"""
Minimal HTTP OAuth callback server for Google authentication.

Runs in a background thread; handles GET /auth/google/callback.
Users are directed to a Google auth URL via /google_auth Telegram command.
On successful callback, the token is stored encrypted in UserCredentialStore.
"""
from __future__ import annotations

import html
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from typing import Optional

import requests as _http_requests
from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

STATE_TTL_SECONDS = 300
CALLBACK_PATH = "/auth/google/callback"

_SUCCESS_HTML = """<!DOCTYPE html>
<html><head><title>Autorizado</title><meta charset="utf-8"></head>
<body style="font-family:sans-serif;text-align:center;padding:3em">
<h2>&#x2705; Google autorizado com sucesso!</h2>
<p>Voc&ecirc; pode fechar esta aba e voltar ao Telegram.</p>
</body></html>"""

_ERROR_HTML = """<!DOCTYPE html>
<html><head><title>Erro</title><meta charset="utf-8"></head>
<body style="font-family:sans-serif;text-align:center;padding:3em">
<h2>\u274c Falha na autoriza\u00e7\u00e3o</h2>
<p>{message}</p>
</body></html>"""


def _to_html_entities(text: str) -> str:
    escaped = html.escape(str(text), quote=True)
    return escaped.encode("ascii", "xmlcharrefreplace").decode("ascii")


class GoogleOAuthCallbackServer:
    """HTTP server that handles the Google OAuth2 callback for multiple users."""

    def __init__(
        self,
        credential_store,
        *,
        port: int = 8080,
        callback_url: str,
        bot_token: Optional[str] = None,
        credentials_path: str = "credentials.json",
        project_logger=None,
    ):
        self._store = credential_store
        self._port = port
        self._callback_url = callback_url
        self._bot_token = bot_token
        self._credentials_path = credentials_path
        self._logger = project_logger or logging.getLogger(__name__)
        self._pending: dict[str, dict] = {}  # state → {user_id, expires_at}
        self._lock = threading.Lock()
        self._server: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start_flow(self, user_id: str) -> str:
        """Register a pending auth state and return the Google auth URL."""
        if not os.path.exists(self._credentials_path):
            raise ValueError(
                f"credentials.json not found at '{self._credentials_path}'. "
                "Download it from the Google Cloud Console and place it in the project root."
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

    def start(self) -> None:
        """Start the callback HTTP server in a daemon thread."""
        if self._server is not None:
            return
        server_ref = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self._respond(404, b"Not found")
                    return

                params = urllib.parse.parse_qs(parsed.query)
                code = (params.get("code") or [""])[0]
                state = (params.get("state") or [""])[0]
                error_param = (params.get("error") or [""])[0]

                if error_param:
                    msg = _to_html_entities(f"Google recusou: {error_param}")
                    self._respond(400, _ERROR_HTML.format(message=msg))
                    return
                if not code or not state:
                    self._respond(400, _ERROR_HTML.format(message=_to_html_entities("Parâmetros ausentes.")))
                    return

                ok, message, user_id = server_ref._handle_callback(code, state)
                if ok:
                    self._respond(200, _SUCCESS_HTML)
                    if user_id and server_ref._bot_token:
                        server_ref._notify_telegram(
                            user_id,
                            "✅ Google autorizado com sucesso! Já pode usar Gmail e Calendar.",
                        )
                else:
                    self._respond(400, _ERROR_HTML.format(message=_to_html_entities(message)))

            def _respond(
                self,
                status: int,
                body: str | bytes,
                content_type: str = "text/html; charset=utf-8",
            ) -> None:
                payload = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt, *args):
                server_ref._logger.debug("OAuth callback: " + fmt, *args)

        self._server = http.server.HTTPServer(("127.0.0.1", self._port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="google-oauth-server",
        )
        self._thread.start()
        self._logger.info("Google OAuth callback server started on port %d", self._port)

    def stop(self) -> None:
        """Shut down the callback server gracefully."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            self._logger.info("Google OAuth callback server stopped")

    def is_running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _make_flow(self) -> Flow:
        return Flow.from_client_secrets_file(
            self._credentials_path,
            scopes=SCOPES,
            redirect_uri=self._callback_url,
        )

    def _handle_callback(self, code: str, state: str) -> tuple[bool, str, Optional[str]]:
        with self._lock:
            pending = self._pending.pop(state, None)

        if pending is None:
            return False, "Estado inválido ou expirado. Use /google_auth para gerar um novo link.", None
        if time.monotonic() > pending["expires_at"]:
            return False, "Link expirado. Use /google_auth para gerar um novo.", None

        user_id = pending["user_id"]
        try:
            flow = pending.get("flow")
            if flow is None:
                return False, "Sessão de autenticação expirada. Use /google_auth para gerar um novo link.", None
            flow.fetch_token(code=code)
            creds = flow.credentials
            self._store.set_credential(user_id, "google_token_json", creds.to_json())
            self._logger.info("Google token stored for user_id=%s", user_id)
            return True, "Autorizado com sucesso.", user_id
        except Exception as exc:
            self._logger.exception("Failed to exchange OAuth code for user_id=%s", user_id)
            return False, f"Erro ao processar autorização: {exc}", None

    def _notify_telegram(self, chat_id: str, text: str) -> None:
        try:
            _http_requests.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception:
            self._logger.warning("Could not send Telegram notification after Google auth")

    def _purge_expired_states(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [s for s, v in self._pending.items() if now > v["expires_at"]]
            for s in expired:
                del self._pending[s]
