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


class TestMemoriesEndpoints:
    def test_list_memories_empty_dir(self, client, auth_token, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path))
        res = client.get("/api/memories", headers=auth_headers(auth_token))
        assert res.status_code == 200
        assert res.json()["count"] == 0

    def test_list_memories_with_files(self, client, auth_token, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path))
        import re
        user_dir = tmp_path / re.sub(r"[^a-zA-Z0-9_\-]", "", "web:testuser")
        user_dir.mkdir()
        (user_dir / "notes.md").write_text("Hello memory")

        res = client.get("/api/memories", headers=auth_headers(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 1
        assert data["files"][0]["filename"] == "notes.md"
        assert data["files"][0]["content"] == "Hello memory"

    def test_update_memory_success(self, client, auth_token, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path))
        import re
        user_dir = tmp_path / re.sub(r"[^a-zA-Z0-9_\-]", "", "web:testuser")
        user_dir.mkdir()
        (user_dir / "notes.md").write_text("Original content")

        res = client.put(
            "/api/memories/notes.md",
            json={"content": "Updated content"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert (user_dir / "notes.md").read_text() == "Updated content"

    def test_update_memory_invalid_filename(self, client, auth_token, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path))
        res = client.put(
            "/api/memories/../../etc/passwd",
            json={"content": "bad"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code in (400, 404, 422)

    def test_update_memory_reserved_file(self, client, auth_token, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path))
        import re
        user_dir = tmp_path / re.sub(r"[^a-zA-Z0-9_\-]", "", "web:testuser")
        user_dir.mkdir()
        (user_dir / "readme.md").write_text("Readme")

        res = client.put(
            "/api/memories/readme.md",
            json={"content": "new"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 403

    def test_update_memory_not_found(self, client, auth_token, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path))
        import re
        user_dir = tmp_path / re.sub(r"[^a-zA-Z0-9_\-]", "", "web:testuser")
        user_dir.mkdir()

        res = client.put(
            "/api/memories/nonexistent.md",
            json={"content": "new"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 404

    def test_update_memory_content_too_large(self, client, auth_token, tmp_path, monkeypatch):
        monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path))
        import re
        user_dir = tmp_path / re.sub(r"[^a-zA-Z0-9_\-]", "", "web:testuser")
        user_dir.mkdir()
        (user_dir / "notes.md").write_text("ok")

        res = client.put(
            "/api/memories/notes.md",
            json={"content": "x" * (100 * 1024 + 1)},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 413

    def test_update_memory_unauthenticated(self, client):
        res = client.put("/api/memories/notes.md", json={"content": "new"})
        assert res.status_code == 401


class TestTaskEndpoints:
    def test_list_tasks_empty(self, client, auth_token):
        res = client.get("/api/tasks", headers=auth_headers(auth_token))
        assert res.status_code == 200
        assert res.json()["tasks"] == []

    def test_create_task_minimal(self, client, auth_token):
        res = client.post("/api/tasks", json={"name": "Minha tarefa"}, headers=auth_headers(auth_token))
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Minha tarefa"
        assert data["id"]
        assert data["done"] is False
        assert data["tags"] == []
        assert data["deadline"] is None
        assert data["project"] is None

    def test_create_task_full(self, client, auth_token):
        res = client.post(
            "/api/tasks",
            json={"name": "Tarefa completa", "deadline": "2030-12-31", "project": "Proj Alpha", "tags": ["urgente", "backend"]},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Tarefa completa"
        assert data["deadline"] == "2030-12-31"
        assert data["project"] == "Proj Alpha"
        assert set(data["tags"]) == {"urgente", "backend"}

    def test_create_task_missing_name(self, client, auth_token):
        res = client.post("/api/tasks", json={"name": "   "}, headers=auth_headers(auth_token))
        assert res.status_code in (400, 422)

    def test_list_tasks_with_data(self, client, auth_token):
        client.post("/api/tasks", json={"name": "Task A", "tags": ["alpha"]}, headers=auth_headers(auth_token))
        client.post("/api/tasks", json={"name": "Task B", "tags": ["beta"]}, headers=auth_headers(auth_token))

        res = client.get("/api/tasks", headers=auth_headers(auth_token))
        assert res.status_code == 200
        tasks = res.json()["tasks"]
        assert len(tasks) == 2
        names = {t["name"] for t in tasks}
        assert names == {"Task A", "Task B"}
        # Each task has tags list
        for t in tasks:
            assert "tags" in t
            assert isinstance(t["tags"], list)

    def test_update_task_done(self, client, auth_token):
        res = client.post("/api/tasks", json={"name": "Marcar como feita"}, headers=auth_headers(auth_token))
        task_id = res.json()["id"]

        res = client.patch(f"/api/tasks/{task_id}", json={"done": True}, headers=auth_headers(auth_token))
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # Verify it's marked done
        res = client.get("/api/tasks", headers=auth_headers(auth_token))
        tasks = res.json()["tasks"]
        task = next(t for t in tasks if t["id"] == task_id)
        assert task["done"] is True

    def test_update_task_deadline_and_project(self, client, auth_token):
        res = client.post("/api/tasks", json={"name": "Atualizar detalhes"}, headers=auth_headers(auth_token))
        task_id = res.json()["id"]

        res = client.patch(
            f"/api/tasks/{task_id}",
            json={"name": "Novo nome", "deadline": "2031-06-15", "project": "Novo projeto"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200

        res = client.get("/api/tasks", headers=auth_headers(auth_token))
        task = next(t for t in res.json()["tasks"] if t["id"] == task_id)
        assert task["name"] == "Novo nome"
        assert task["deadline"] == "2031-06-15"
        assert task["project"] == "Novo projeto"

    def test_delete_task(self, client, auth_token):
        res = client.post("/api/tasks", json={"name": "Deletar isso"}, headers=auth_headers(auth_token))
        task_id = res.json()["id"]

        res = client.delete(f"/api/tasks/{task_id}", headers=auth_headers(auth_token))
        assert res.status_code == 200

        res = client.get("/api/tasks", headers=auth_headers(auth_token))
        tasks = res.json()["tasks"]
        assert all(t["id"] != task_id for t in tasks)

        # Re-delete should 404
        res = client.delete(f"/api/tasks/{task_id}", headers=auth_headers(auth_token))
        assert res.status_code == 404

    def test_tasks_meta(self, client, auth_token):
        client.post("/api/tasks", json={"name": "T1", "project": "Alpha", "tags": ["foo", "bar"]}, headers=auth_headers(auth_token))
        client.post("/api/tasks", json={"name": "T2", "project": "Beta", "tags": ["baz"]}, headers=auth_headers(auth_token))

        res = client.get("/api/tasks/meta", headers=auth_headers(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert "projects" in data
        assert "tags" in data
        assert set(data["projects"]) == {"Alpha", "Beta"}
        assert set(data["tags"]) == {"foo", "bar", "baz"}

    def test_tasks_unauthenticated(self, client):
        res = client.get("/api/tasks")
        assert res.status_code in (401, 403)
        res = client.post("/api/tasks", json={"name": "x"})
        assert res.status_code in (401, 403)
        res = client.patch("/api/tasks/abc", json={"done": True})
        assert res.status_code in (401, 403)
        res = client.delete("/api/tasks/abc")
        assert res.status_code in (401, 403)
        res = client.get("/api/tasks/meta")
        assert res.status_code in (401, 403)


def _register_and_login(client, username, password):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user(username, password, display_name=username)
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    return res.json()["token"]


class TestTaskUserIsolation:
    """Security: user A must NOT be able to access or affect user B's tasks."""

    def test_list_only_own_tasks(self, client):
        token_a = _register_and_login(client, "alice_t", "passA123")
        token_b = _register_and_login(client, "bob_t", "passB456")

        client.post("/api/tasks", json={"name": "Alice task"}, headers=auth_headers(token_a))
        client.post("/api/tasks", json={"name": "Bob task"}, headers=auth_headers(token_b))

        alice_tasks = client.get("/api/tasks", headers=auth_headers(token_a)).json()["tasks"]
        bob_tasks = client.get("/api/tasks", headers=auth_headers(token_b)).json()["tasks"]

        assert len(alice_tasks) == 1 and alice_tasks[0]["name"] == "Alice task"
        assert len(bob_tasks) == 1 and bob_tasks[0]["name"] == "Bob task"

    def test_cannot_update_other_users_task(self, client):
        token_a = _register_and_login(client, "alice_u", "passA123")
        token_b = _register_and_login(client, "bob_u", "passB456")

        task_id = client.post("/api/tasks", json={"name": "Alice original"}, headers=auth_headers(token_a)).json()["id"]

        res = client.patch(f"/api/tasks/{task_id}", json={"name": "Hijacked!"}, headers=auth_headers(token_b))
        assert res.status_code == 404

        task = client.get("/api/tasks", headers=auth_headers(token_a)).json()["tasks"][0]
        assert task["name"] == "Alice original"

    def test_cannot_delete_other_users_task_or_its_tags(self, client):
        token_a = _register_and_login(client, "alice_d", "passA123")
        token_b = _register_and_login(client, "bob_d", "passB456")

        task_id = client.post(
            "/api/tasks",
            json={"name": "Alice tagged", "tags": ["important", "work"]},
            headers=auth_headers(token_a),
        ).json()["id"]

        res = client.delete(f"/api/tasks/{task_id}", headers=auth_headers(token_b))
        assert res.status_code == 404

        # Task still exists for Alice with tags intact
        tasks = client.get("/api/tasks", headers=auth_headers(token_a)).json()["tasks"]
        assert len(tasks) == 1
        assert set(tasks[0]["tags"]) == {"important", "work"}
