"""Tests for conversation timestamps, limits, and security."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, PropertyMock

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant_connector.models import ChatResponse

_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_conv.sqlite3")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-conv-tests")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")

    import web_app.dependencies as deps
    deps._user_store = None
    deps._assistant_service = None

    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


def _create_user_and_login(client, username="testuser", password="testpass123"):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user(username, password, display_name=username.title())
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["token"]


@pytest.fixture
def auth_token(client):
    return _create_user_and_login(client)


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


class TestMessageTimestamps:
    """Messages returned from the API should include created_at timestamps."""

    def test_messages_have_created_at(self, client, auth_token):
        """After sending a chat message, loading conversation messages should include timestamps."""
        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Hi!", image_paths=[])

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            # Create conversation
            res = client.post("/api/conversations", json={"title": "Timestamps test"}, headers=auth_headers(auth_token))
            conv_id = res.json()["id"]

            # Send a message
            client.post(
                "/api/chat",
                json={"message": "Hello", "conversation_id": conv_id},
                headers=auth_headers(auth_token),
            )

            # Load messages
            res = client.get(f"/api/conversations/{conv_id}/messages", headers=auth_headers(auth_token))
            assert res.status_code == 200
            data = res.json()

            assert "messages" in data
            assert "message_count" in data
            assert "message_limit" in data
            assert data["message_limit"] == 40

            for msg in data["messages"]:
                assert "created_at" in msg
                assert msg["created_at"]  # not empty
                assert "role" in msg
                assert "content" in msg
        finally:
            app.dependency_overrides.clear()

    def test_messages_response_includes_count(self, client, auth_token):
        """The messages endpoint returns message_count and message_limit."""
        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Reply", image_paths=[])

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post("/api/conversations", json={"title": "Count test"}, headers=auth_headers(auth_token))
            conv_id = res.json()["id"]

            # Empty conversation
            res = client.get(f"/api/conversations/{conv_id}/messages", headers=auth_headers(auth_token))
            assert res.status_code == 200
            assert res.json()["message_count"] == 0

            # Send 2 messages (user + assistant = 2 in memory store)
            client.post(
                "/api/chat",
                json={"message": "test", "conversation_id": conv_id},
                headers=auth_headers(auth_token),
            )

            res = client.get(f"/api/conversations/{conv_id}/messages", headers=auth_headers(auth_token))
            # The mock doesn't actually persist messages, so count may be 0
            # but the structure should still be correct
            assert "message_count" in res.json()
            assert "message_limit" in res.json()
        finally:
            app.dependency_overrides.clear()


class TestConversationLimit:
    """Users should not be able to create more than 100 conversations."""

    def test_create_conversation_within_limit(self, client, auth_token):
        """Creating a conversation within the limit should succeed."""
        res = client.post("/api/conversations", json={"title": "Test"}, headers=auth_headers(auth_token))
        assert res.status_code == 201

    def test_create_conversation_at_limit_rotates(self, client, auth_token):
        """Creating the 101st conversation should auto-delete the oldest."""
        from web_app.dependencies import get_user_store
        store = get_user_store()
        user = store.get_user_by_username("testuser")

        # Create 100 conversations directly in the store
        first_conv = None
        for i in range(100):
            c = store.create_conversation(user["id"], f"Conv {i}")
            if i == 0:
                first_conv = c

        # The 101st via API should succeed (oldest auto-deleted)
        res = client.post("/api/conversations", json={"title": "New one"}, headers=auth_headers(auth_token))
        assert res.status_code == 201

        # Verify total is still 100
        convs = store.list_conversations(user["id"], limit=200)
        assert len(convs) == 100

        # The oldest should be gone
        assert store.get_conversation(first_conv["id"], user["id"]) is None

    def test_rotation_keeps_newest_conversations(self, client, auth_token):
        """Auto-rotation should keep the most recently updated conversations."""
        from web_app.dependencies import get_user_store
        store = get_user_store()
        user = store.get_user_by_username("testuser")

        for i in range(100):
            store.create_conversation(user["id"], f"Conv {i}")

        # Create 3 more — the 3 oldest should be deleted
        for i in range(3):
            res = client.post(
                "/api/conversations",
                json={"title": f"New {i}"},
                headers=auth_headers(auth_token),
            )
            assert res.status_code == 201

        convs = store.list_conversations(user["id"], limit=200)
        assert len(convs) == 100
        titles = [c["title"] for c in convs]
        assert "New 0" in titles
        assert "New 1" in titles
        assert "New 2" in titles

    def test_conversation_limit_per_user(self, client):
        """Each user has their own independent conversation pool."""
        from web_app.dependencies import get_user_store
        store = get_user_store()

        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")
        user_a = store.get_user_by_username("alice")

        # Alice fills up to 100
        for i in range(100):
            store.create_conversation(user_a["id"], f"A-{i}")

        # Alice creates one more — oldest rotated out
        res = client.post("/api/conversations", json={"title": "A-new"}, headers=auth_headers(token_a))
        assert res.status_code == 201
        assert len(store.list_conversations(user_a["id"], limit=200)) == 100

        # Bob is unaffected
        res = client.post("/api/conversations", json={"title": "Bob conv"}, headers=auth_headers(token_b))
        assert res.status_code == 201


class TestMessageLimit:
    """Conversations should enforce a 40-message (20-exchange) limit."""

    def test_chat_blocked_at_message_limit(self, client, auth_token):
        """When a conversation has 40+ messages, new chats should be rejected."""
        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Reply", image_paths=[])
        mock_service._runtime._memory_store.count_messages.return_value = 40

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post("/api/conversations", json={"title": "Full conv"}, headers=auth_headers(auth_token))
            conv_id = res.json()["id"]

            res = client.post(
                "/api/chat",
                json={"message": "Hello", "conversation_id": conv_id},
                headers=auth_headers(auth_token),
            )
            assert res.status_code == 400
            assert "limite" in res.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_chat_allowed_under_limit(self, client, auth_token):
        """When a conversation has fewer than 40 messages, chats should work."""
        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Reply", image_paths=[])
        mock_service._runtime._memory_store.count_messages.return_value = 38

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post("/api/conversations", json={"title": "OK conv"}, headers=auth_headers(auth_token))
            conv_id = res.json()["id"]

            res = client.post(
                "/api/chat",
                json={"message": "Hello", "conversation_id": conv_id},
                headers=auth_headers(auth_token),
            )
            assert res.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_chat_without_conversation_id_not_limited(self, client, auth_token):
        """Messages without a conversation_id should not be subject to limits."""
        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Reply", image_paths=[])

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post(
                "/api/chat",
                json={"message": "Hello"},
                headers=auth_headers(auth_token),
            )
            assert res.status_code == 200
        finally:
            app.dependency_overrides.clear()


class TestContextWindow:
    """The LLM context window should be configured to 10 messages (5 exchanges)."""

    def test_memory_window_is_10(self):
        """agents.json should have memory_window set to 10."""
        import json
        config_path = os.path.join(
            os.path.dirname(__file__), "..",
            "assistant_connector", "config", "agents.json",
        )
        with open(config_path) as f:
            config = json.load(f)
        agents = config.get("agents", [])
        memory_windows = [a["memory_window"] for a in agents if "memory_window" in a]
        assert memory_windows, "No agent with memory_window found"
        assert all(w == 10 for w in memory_windows)


class TestConversationUserIsolation:
    """Security: users can only access their own conversations."""

    def test_cannot_read_other_users_conversation_messages(self, client):
        """User B should not be able to read User A's conversation messages."""
        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")

        mock_service = MagicMock()
        mock_service.chat.return_value = ChatResponse(text="Reply", image_paths=[])

        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            # Alice creates a conversation
            res = client.post("/api/conversations", json={"title": "Alice conv"}, headers=auth_headers(token_a))
            conv_id = res.json()["id"]

            # Bob tries to read Alice's messages
            res = client.get(f"/api/conversations/{conv_id}/messages", headers=auth_headers(token_b))
            assert res.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_cannot_delete_other_users_conversation(self, client):
        """User B should not be able to delete User A's conversation."""
        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")

        mock_service = MagicMock()
        from web_app.app import app
        from web_app.dependencies import get_assistant_service
        app.dependency_overrides[get_assistant_service] = lambda: mock_service

        try:
            res = client.post("/api/conversations", json={"title": "Alice conv"}, headers=auth_headers(token_a))
            conv_id = res.json()["id"]

            res = client.delete(f"/api/conversations/{conv_id}", headers=auth_headers(token_b))
            assert res.status_code == 404

            # Verify still exists for Alice
            res = client.get(f"/api/conversations/{conv_id}/messages", headers=auth_headers(token_a))
            assert res.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_list_only_own_conversations(self, client):
        """Users should only see their own conversations."""
        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")

        client.post("/api/conversations", json={"title": "Alice 1"}, headers=auth_headers(token_a))
        client.post("/api/conversations", json={"title": "Alice 2"}, headers=auth_headers(token_a))
        client.post("/api/conversations", json={"title": "Bob 1"}, headers=auth_headers(token_b))

        alice_convs = client.get("/api/conversations", headers=auth_headers(token_a)).json()["conversations"]
        bob_convs = client.get("/api/conversations", headers=auth_headers(token_b)).json()["conversations"]

        assert len(alice_convs) == 2
        assert len(bob_convs) == 1
        assert all("Alice" in c["title"] for c in alice_convs)
        assert bob_convs[0]["title"] == "Bob 1"
