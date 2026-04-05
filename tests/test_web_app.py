"""Tests for web_app.app (FastAPI endpoints)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant_connector.models import ChatResponse

# Save original httpx.Client.request before conftest patches it.
_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_web.sqlite3")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-web-app-tests")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")

    # Reset singletons between tests
    import web_app.dependencies as deps
    deps._user_store = None
    deps._assistant_service = None

    # Restore httpx.Client.request so TestClient can make local ASGI calls.
    # conftest.py blocks it to prevent external network calls, but TestClient
    # uses httpx internally with a local transport — no real network involved.
    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


@pytest.fixture
def auth_token(client):
    """Create a user and return a valid JWT token."""
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user("testuser", "testpass123", display_name="Test User")

    res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpass123"})
    assert res.status_code == 200
    return res.json()["token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


class TestPageRoutes:
    def test_index_returns_login_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "login-form" in res.text

    def test_chat_page_returns_html(self, client):
        res = client.get("/chat")
        assert res.status_code == 200
        assert "chat-messages" in res.text

    def test_manifest_json(self, client):
        res = client.get("/manifest.json")
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "Assistente Pessoal"

    def test_health_endpoint(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


class TestAuthEndpoints:
    def test_login_success(self, client, auth_token):
        assert auth_token
        assert len(auth_token) > 20

    def test_login_wrong_password(self, client):
        from web_app.dependencies import get_user_store
        store = get_user_store()
        store.create_user("alice", "correct123")

        res = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
        assert res.status_code == 401

    def test_login_nonexistent_user(self, client):
        res = client.post("/api/auth/login", json={"username": "nobody", "password": "pass123"})
        assert res.status_code == 401

    def test_me_with_valid_token(self, client, auth_token):
        res = client.get("/api/auth/me", headers=auth_headers(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["username"] == "testuser"

    def test_me_without_token(self, client):
        res = client.get("/api/auth/me")
        assert res.status_code == 401 or res.status_code == 403

    def test_me_with_invalid_token(self, client):
        res = client.get("/api/auth/me", headers=auth_headers("bad.token.here"))
        assert res.status_code == 401


class TestChatEndpoints:
    def test_chat_send(self, client, auth_token):
        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Hello!", image_paths=[])

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post(
                "/api/chat",
                json={"message": "Hi"},
                headers=auth_headers(auth_token),
            )
            assert res.status_code == 200
            data = res.json()
            assert data["text"] == "Hello!"
        finally:
            app.dependency_overrides.clear()

    def test_chat_send_empty_message(self, client, auth_token):
        res = client.post(
            "/api/chat",
            json={"message": "   "},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 400

    def test_chat_send_unauthenticated(self, client):
        res = client.post("/api/chat", json={"message": "Hi"})
        assert res.status_code == 401 or res.status_code == 403

    def test_chat_reset(self, client, auth_token):
        mock_service = MagicMock()

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post("/api/chat/reset", headers=auth_headers(auth_token))
            assert res.status_code == 200
            assert res.json()["status"] == "ok"
        finally:
            app.dependency_overrides.clear()

    def test_chat_upload(self, client, auth_token):
        mock_service = MagicMock()
        mock_service.handle_file_upload.return_value = ChatResponse(text="File received", image_paths=[])

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post(
                "/api/chat/upload",
                files={"file": ("test.txt", b"hello world", "text/plain")},
                data={"caption": "test file"},
                headers=auth_headers(auth_token),
            )
            assert res.status_code == 200
            assert res.json()["text"] == "File received"
        finally:
            app.dependency_overrides.clear()


class TestConversationEndpoints:
    def test_create_conversation(self, client, auth_token):
        res = client.post(
            "/api/conversations",
            json={"title": "My chat"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "My chat"
        assert data["id"]

    def test_list_conversations(self, client, auth_token):
        # Create two conversations
        client.post("/api/conversations", json={"title": "First"}, headers=auth_headers(auth_token))
        client.post("/api/conversations", json={"title": "Second"}, headers=auth_headers(auth_token))

        res = client.get("/api/conversations", headers=auth_headers(auth_token))
        assert res.status_code == 200
        convs = res.json()["conversations"]
        assert len(convs) == 2

    def test_rename_conversation(self, client, auth_token):
        res = client.post("/api/conversations", json={"title": "Old"}, headers=auth_headers(auth_token))
        conv_id = res.json()["id"]

        res = client.patch(
            f"/api/conversations/{conv_id}",
            json={"title": "New title"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200

    def test_rename_conversation_empty_title(self, client, auth_token):
        res = client.post("/api/conversations", json={"title": "Chat"}, headers=auth_headers(auth_token))
        conv_id = res.json()["id"]

        res = client.patch(
            f"/api/conversations/{conv_id}",
            json={"title": "   "},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 400

    def test_rename_nonexistent_conversation(self, client, auth_token):
        res = client.patch(
            "/api/conversations/nonexistent",
            json={"title": "X"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 404

    def test_delete_conversation(self, client, auth_token):
        mock_service = MagicMock()

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post("/api/conversations", json={"title": "Delete me"}, headers=auth_headers(auth_token))
            conv_id = res.json()["id"]

            res = client.delete(f"/api/conversations/{conv_id}", headers=auth_headers(auth_token))
            assert res.status_code == 200

            # Verify it's gone
            res = client.get("/api/conversations", headers=auth_headers(auth_token))
            convs = res.json()["conversations"]
            assert all(c["id"] != conv_id for c in convs)
        finally:
            app.dependency_overrides.clear()

    def test_delete_nonexistent_conversation(self, client, auth_token):
        mock_service = MagicMock()

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.delete("/api/conversations/nonexistent", headers=auth_headers(auth_token))
            assert res.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_conversations_unauthenticated(self, client):
        res = client.get("/api/conversations")
        assert res.status_code == 401 or res.status_code == 403

    def test_chat_with_conversation_id(self, client, auth_token):
        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Reply", image_paths=[])

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            # Create a conversation first
            res = client.post("/api/conversations", json={"title": "Chat"}, headers=auth_headers(auth_token))
            conv_id = res.json()["id"]

            res = client.post(
                "/api/chat",
                json={"message": "Hello", "conversation_id": conv_id},
                headers=auth_headers(auth_token),
            )
            assert res.status_code == 200
            assert res.json()["text"] == "Reply"

            # Verify channel_id includes conversation_id
            call_kwargs = mock_service.chat.call_args
            assert conv_id in call_kwargs.kwargs.get("channel_id", call_kwargs[1].get("channel_id", ""))
        finally:
            app.dependency_overrides.clear()
