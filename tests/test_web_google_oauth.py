"""Tests for web_app.google_oauth and the Google OAuth routes in web_app.app."""
from __future__ import annotations

import json
import time
import unittest
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from web_app.google_oauth import WebGoogleOAuth, STATE_TTL_SECONDS

# Save original httpx.Client.request before conftest patches it.
_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


# ---- Unit tests for WebGoogleOAuth ----


def _make_oauth(**kwargs) -> WebGoogleOAuth:
    store = MagicMock()
    defaults = dict(
        credential_store=store,
        callback_url="https://example.com/auth/google/callback",
    )
    defaults.update(kwargs)
    return WebGoogleOAuth(**defaults)


class TestWebGoogleOAuthStartFlow(unittest.TestCase):
    def test_raises_if_credentials_json_missing(self):
        oauth = _make_oauth(credentials_path="/nonexistent/credentials.json")
        with self.assertRaises(ValueError):
            oauth.start_flow("web:alice")

    @patch("web_app.google_oauth.Flow")
    def test_start_flow_returns_url_and_stores_state(self, mock_flow_cls):
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth?state=abc", "abc")
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        oauth = _make_oauth()
        with patch("os.path.exists", return_value=True):
            url = oauth.start_flow("web:alice")

        self.assertIsInstance(url, str)
        self.assertEqual(len(oauth._pending), 1)
        stored = list(oauth._pending.values())[0]
        self.assertEqual(stored["user_id"], "web:alice")
        self.assertGreater(stored["expires_at"], time.monotonic())


class TestWebGoogleOAuthHandleCallback(unittest.TestCase):
    def test_unknown_state_rejected(self):
        oauth = _make_oauth()
        ok, msg, uid = oauth.handle_callback("code", "unknown_state")
        self.assertFalse(ok)
        self.assertIsNone(uid)

    def test_expired_state_rejected(self):
        oauth = _make_oauth()
        state = "expiredstate"
        oauth._pending[state] = {
            "user_id": "web:bob",
            "expires_at": time.monotonic() - 1,
        }
        ok, msg, uid = oauth.handle_callback("code", state)
        self.assertFalse(ok)
        self.assertIsNone(uid)

    def test_missing_flow_rejected(self):
        oauth = _make_oauth()
        state = "noflowstate"
        oauth._pending[state] = {
            "user_id": "web:carol",
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
        }
        ok, msg, uid = oauth.handle_callback("code", state)
        self.assertFalse(ok)
        self.assertIsNone(uid)

    @patch("web_app.google_oauth.Flow")
    def test_valid_callback_stores_token(self, mock_flow_cls):
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = json.dumps({"token": "tok"})
        mock_flow = MagicMock()
        mock_flow.credentials = mock_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        store = MagicMock()
        oauth = _make_oauth(credential_store=store)
        state = "teststate"
        oauth._pending[state] = {
            "user_id": "web:dave",
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
            "flow": mock_flow,
        }

        ok, msg, uid = oauth.handle_callback("authcode", state)
        self.assertTrue(ok)
        self.assertEqual(uid, "web:dave")
        store.set_credential.assert_called_once_with(
            "web:dave", "google_token_json", mock_creds.to_json.return_value
        )
        self.assertNotIn(state, oauth._pending)

    def test_fetch_token_failure_returns_error(self):
        flow = MagicMock()
        flow.fetch_token.side_effect = RuntimeError("exchange failed")
        oauth = _make_oauth()
        state = "failstate"
        oauth._pending[state] = {
            "user_id": "web:eve",
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
            "flow": flow,
        }
        ok, msg, uid = oauth.handle_callback("code", state)
        self.assertFalse(ok)
        self.assertIsNone(uid)
        self.assertIn("Erro ao processar", msg)


class TestWebGoogleOAuthTokenStatus(unittest.TestCase):
    def test_has_valid_token_true(self):
        store = MagicMock()
        store.get_credential.return_value = '{"token": "tok"}'
        oauth = _make_oauth(credential_store=store)
        self.assertTrue(oauth.has_valid_token("web:frank"))

    def test_has_valid_token_false(self):
        store = MagicMock()
        store.get_credential.return_value = None
        oauth = _make_oauth(credential_store=store)
        self.assertFalse(oauth.has_valid_token("web:grace"))

    def test_revoke_token(self):
        store = MagicMock()
        store.delete_credential.return_value = True
        oauth = _make_oauth(credential_store=store)
        self.assertTrue(oauth.revoke_token("web:heidi"))
        store.delete_credential.assert_called_once_with("web:heidi", "google_token_json")


class TestPurgeExpiredStates(unittest.TestCase):
    def test_purge_removes_only_expired(self):
        oauth = _make_oauth()
        now = time.monotonic()
        oauth._pending["expired1"] = {"user_id": "u1", "expires_at": now - 100}
        oauth._pending["expired2"] = {"user_id": "u2", "expires_at": now - 1}
        oauth._pending["alive"] = {"user_id": "u3", "expires_at": now + 600}

        oauth._purge_expired_states()

        self.assertNotIn("expired1", oauth._pending)
        self.assertNotIn("expired2", oauth._pending)
        self.assertIn("alive", oauth._pending)


# ---- FastAPI route integration tests ----


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_web_google.sqlite3")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-google-tests")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")
    monkeypatch.setenv("GOOGLE_OAUTH_CALLBACK_URL", "")

    import web_app.dependencies as deps
    deps._user_store = None
    deps._assistant_service = None
    deps._google_oauth = None

    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


@pytest.fixture
def auth_token(client):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user("oauthuser", "testpass123", display_name="OAuth User")

    res = client.post("/api/auth/login", json={"username": "oauthuser", "password": "testpass123"})
    assert res.status_code == 200
    return res.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestGoogleStatusRoute:
    def test_status_returns_not_configured_when_no_env(self, client, auth_token):
        res = client.get("/api/google/status", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is False
        assert data["connected"] is False

    def test_status_returns_connected_when_token_exists(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("GOOGLE_OAUTH_CALLBACK_URL", "https://example.com/auth/google/callback")
        import web_app.dependencies as deps
        deps._google_oauth = None

        mock_oauth = MagicMock()
        mock_oauth.has_valid_token.return_value = True
        monkeypatch.setattr(deps, "_google_oauth", mock_oauth)

        res = client.get("/api/google/status", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is True
        assert data["connected"] is True

    def test_status_requires_auth(self, client):
        res = client.get("/api/google/status")
        assert res.status_code in (401, 403)


class TestGoogleAuthUrlRoute:
    def test_auth_url_returns_503_when_not_configured(self, client, auth_token):
        res = client.get("/api/google/auth-url", headers=_auth(auth_token))
        assert res.status_code == 503

    def test_auth_url_returns_url_when_configured(self, client, auth_token, monkeypatch):
        import web_app.dependencies as deps

        mock_oauth = MagicMock()
        mock_oauth.start_flow.return_value = "https://accounts.google.com/o/oauth2/auth?state=abc"
        monkeypatch.setattr(deps, "_google_oauth", mock_oauth)

        res = client.get("/api/google/auth-url", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert "auth_url" in data
        assert "accounts.google.com" in data["auth_url"]


class TestGoogleCallbackRoute:
    def test_callback_with_error_param(self, client):
        res = client.get("/auth/google/callback?error=access_denied")
        assert res.status_code == 400
        assert "recusou" in res.text

    def test_callback_missing_params(self, client):
        res = client.get("/auth/google/callback")
        assert res.status_code == 400
        assert "ausentes" in res.text

    def test_callback_success(self, client, monkeypatch):
        import web_app.dependencies as deps

        mock_oauth = MagicMock()
        mock_oauth.handle_callback.return_value = (True, "OK", "web:oauthuser")
        monkeypatch.setattr(deps, "_google_oauth", mock_oauth)

        res = client.get("/auth/google/callback?code=abc123&state=xyz")
        assert res.status_code == 200
        assert "sucesso" in res.text

    def test_callback_failure(self, client, monkeypatch):
        import web_app.dependencies as deps

        mock_oauth = MagicMock()
        mock_oauth.handle_callback.return_value = (False, "Estado inválido", None)
        monkeypatch.setattr(deps, "_google_oauth", mock_oauth)

        res = client.get("/auth/google/callback?code=abc123&state=xyz")
        assert res.status_code == 400
        assert "inválido" in res.text.lower() or "inv" in res.text.lower()

    def test_callback_when_oauth_not_configured(self, client, monkeypatch):
        import web_app.dependencies as deps
        monkeypatch.setattr(deps, "_google_oauth", None)

        res = client.get("/auth/google/callback?code=abc&state=xyz")
        assert res.status_code == 500


class TestGoogleDisconnectRoute:
    def test_disconnect_removes_token(self, client, auth_token, monkeypatch):
        import web_app.dependencies as deps

        mock_oauth = MagicMock()
        mock_oauth.revoke_token.return_value = True
        monkeypatch.setattr(deps, "_google_oauth", mock_oauth)

        res = client.delete("/api/google/disconnect", headers=_auth(auth_token))
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        mock_oauth.revoke_token.assert_called_once_with("web:oauthuser")

    def test_disconnect_requires_auth(self, client):
        res = client.delete("/api/google/disconnect")
        assert res.status_code in (401, 403)

    def test_disconnect_when_not_configured(self, client, auth_token):
        res = client.delete("/api/google/disconnect", headers=_auth(auth_token))
        assert res.status_code == 503


if __name__ == "__main__":
    unittest.main()
