"""Tests for GoogleOAuthCallbackServer."""
from __future__ import annotations

import json
import time
import unittest
from unittest.mock import MagicMock, patch

from google_auth_server import GoogleOAuthCallbackServer, STATE_TTL_SECONDS, _to_html_entities


def _make_server(**kwargs) -> GoogleOAuthCallbackServer:
    store = MagicMock()
    defaults = dict(
        credential_store=store,
        port=19999,
        callback_url="https://example.com/auth/google/callback",
        bot_token="test_token",
    )
    defaults.update(kwargs)
    return GoogleOAuthCallbackServer(**defaults)


class TestStartFlow(unittest.TestCase):
    def test_raises_if_credentials_json_missing(self):
        server = _make_server(credentials_path="/nonexistent/credentials.json")
        with self.assertRaises(ValueError):
            server.start_flow("user123")

    @patch("google_auth_server.Flow")
    def test_start_flow_uses_client_config_when_provided(self, mock_flow_cls):
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth?state=abc", "abc")
        mock_flow_cls.from_client_config.return_value = mock_flow

        server = _make_server(
            client_config={
                "web": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["https://example.com/auth/google/callback"],
                }
            }
        )
        url = server.start_flow("user123")

        self.assertIn("accounts.google.com", url)
        mock_flow_cls.from_client_config.assert_called_once()

    @patch("google_auth_server.Flow")
    def test_start_flow_returns_url_and_stores_state(self, mock_flow_cls):
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth?state=abc", "abc")
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        server = _make_server()
        with patch("os.path.exists", return_value=True):
            url = server.start_flow("user42")

        self.assertIsInstance(url, str)
        self.assertEqual(len(server._pending), 1)
        stored = list(server._pending.values())[0]
        self.assertEqual(stored["user_id"], "user42")
        self.assertGreater(stored["expires_at"], time.monotonic())


class TestHandleCallback(unittest.TestCase):
    @patch("google_auth_server.Flow")
    def _server_with_pending(self, user_id, mock_flow_cls):
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://auth_url", "state123")
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = json.dumps({"token": "tok"})
        mock_flow.credentials = mock_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        server = _make_server()
        server._store = MagicMock()
        state = "validstate"
        server._pending[state] = {
            "user_id": user_id,
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
        }
        return server, state, mock_flow

    def test_unknown_state_rejected(self):
        server = _make_server()
        ok, msg, uid = server._handle_callback("code", "unknown_state")
        self.assertFalse(ok)
        self.assertIsNone(uid)

    def test_expired_state_rejected(self):
        server = _make_server()
        state = "expiredstate"
        server._pending[state] = {
            "user_id": "user1",
            "expires_at": time.monotonic() - 1,  # already expired
        }
        ok, msg, uid = server._handle_callback("code", state)
        self.assertFalse(ok)
        self.assertIsNone(uid)

    @patch("google_auth_server.Flow")
    def test_valid_callback_stores_token(self, mock_flow_cls):
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = json.dumps({"token": "tok"})
        mock_flow = MagicMock()
        mock_flow.credentials = mock_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        server = _make_server()
        server._store = MagicMock()
        state = "teststate"
        server._pending[state] = {
            "user_id": "user99",
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
            "flow": mock_flow,
        }

        ok, msg, uid = server._handle_callback("authcode", state)
        self.assertTrue(ok)
        self.assertEqual(uid, "user99")
        server._store.set_credential.assert_called_once_with(
            "user99", "google_token_json", mock_creds.to_json.return_value
        )
        # State must be removed after successful callback
        self.assertNotIn(state, server._pending)

    def test_callback_fails_gracefully_when_flow_is_missing(self):
        server = _make_server()
        server._store = MagicMock()
        state = "noflowstate"
        server._pending[state] = {
            "user_id": "user77",
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
            # no "flow" key — simulates a state that somehow lost the flow object
        }

        ok, msg, uid = server._handle_callback("authcode", state)
        self.assertFalse(ok)
        self.assertIsNone(uid)
        self.assertIn("google_auth", msg.lower())
        server._store.set_credential.assert_not_called()

    def test_callback_returns_error_when_fetch_token_fails(self):
        server = _make_server()
        flow = MagicMock()
        flow.fetch_token.side_effect = RuntimeError("exchange failed")
        server._pending["state-x"] = {
            "user_id": "user-1",
            "expires_at": time.monotonic() + STATE_TTL_SECONDS,
            "flow": flow,
        }
        ok, msg, uid = server._handle_callback("authcode", "state-x")
        self.assertFalse(ok)
        self.assertIsNone(uid)
        self.assertIn("Erro ao processar autorização", msg)


class TestPurgeExpiredStates(unittest.TestCase):
    def test_purge_removes_only_expired(self):
        server = _make_server()
        now = time.monotonic()
        server._pending["expired1"] = {"user_id": "u1", "expires_at": now - 100}
        server._pending["expired2"] = {"user_id": "u2", "expires_at": now - 1}
        server._pending["alive"] = {"user_id": "u3", "expires_at": now + 600}

        server._purge_expired_states()

        self.assertNotIn("expired1", server._pending)
        self.assertNotIn("expired2", server._pending)
        self.assertIn("alive", server._pending)


class TestStopBeforeStart(unittest.TestCase):
    def test_stop_safe_before_start(self):
        server = _make_server()
        # Must not raise even if server was never started
        server.stop()
        self.assertFalse(server.is_running())


class TestTelegramNotification(unittest.TestCase):
    @patch("google_auth_server._http_requests.post")
    def test_notify_telegram_sends_expected_payload(self, mock_post):
        server = _make_server(bot_token="abc123")
        server._notify_telegram("42", "ok")
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["chat_id"], "42")
        self.assertEqual(kwargs["json"]["text"], "ok")

    @patch("google_auth_server._http_requests.post")
    def test_notify_telegram_logs_warning_on_error(self, mock_post):
        mock_post.side_effect = RuntimeError("network down")
        logger = MagicMock()
        server = _make_server(bot_token="abc123", project_logger=logger)
        server._notify_telegram("42", "ok")
        logger.warning.assert_called_once()


class TestStateTTL(unittest.TestCase):
    def test_state_ttl_seconds_equals_300(self):
        self.assertEqual(STATE_TTL_SECONDS, 300)


class TestServerBindsToLocalhost(unittest.TestCase):
    @patch("google_auth_server.http.server.HTTPServer")
    @patch("google_auth_server.threading.Thread")
    def test_start_binds_to_127_0_0_1(self, _mock_thread_cls, mock_httpserver_cls):
        server = _make_server(port=19999)
        server.start()
        mock_httpserver_cls.assert_called_once()
        bind_address = mock_httpserver_cls.call_args[0][0]
        self.assertEqual(bind_address, ("127.0.0.1", 19999))


class TestHtmlEncoding(unittest.TestCase):
    def test_to_html_entities_converts_unicode_to_ascii_entities(self):
        converted = _to_html_entities("Você & autorização ✅")
        self.assertEqual(converted, "Voc&#234; &amp; autoriza&#231;&#227;o &#9989;")


if __name__ == "__main__":
    unittest.main()
