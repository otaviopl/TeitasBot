"""Tests for Notes API endpoints (web_app.app)."""
from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

# Save original httpx.Client.request before conftest patches it.
_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_notes.sqlite3")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-notes-tests")
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


class TestNotesCRUD:
    def test_create_note(self, client, auth_token):
        res = client.post(
            "/api/notes",
            json={"title": "My note", "content": "# Hello\nWorld"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "My note"
        assert data["content"] == "# Hello\nWorld"
        assert data["id"]
        assert data["created_at"]
        assert data["updated_at"]

    def test_create_note_default_title(self, client, auth_token):
        res = client.post("/api/notes", json={}, headers=auth_headers(auth_token))
        assert res.status_code == 201
        assert res.json()["title"] == "Nova anotação"

    def test_create_note_empty_content(self, client, auth_token):
        res = client.post(
            "/api/notes",
            json={"title": "Empty"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        assert res.json()["content"] == ""

    def test_create_note_content_too_large(self, client, auth_token):
        res = client.post(
            "/api/notes",
            json={"title": "Big", "content": "x" * 600_000},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 400
        assert "500 KB" in res.json()["detail"]

    def test_list_notes_empty(self, client, auth_token):
        res = client.get("/api/notes", headers=auth_headers(auth_token))
        assert res.status_code == 200
        assert res.json()["notes"] == []

    def test_list_notes(self, client, auth_token):
        client.post("/api/notes", json={"title": "Note 1"}, headers=auth_headers(auth_token))
        client.post("/api/notes", json={"title": "Note 2"}, headers=auth_headers(auth_token))
        res = client.get("/api/notes", headers=auth_headers(auth_token))
        assert res.status_code == 200
        notes = res.json()["notes"]
        assert len(notes) == 2

    def test_list_notes_no_content_field(self, client, auth_token):
        client.post(
            "/api/notes",
            json={"title": "Note", "content": "big content"},
            headers=auth_headers(auth_token),
        )
        res = client.get("/api/notes", headers=auth_headers(auth_token))
        notes = res.json()["notes"]
        assert "content" not in notes[0]

    def test_get_note(self, client, auth_token):
        create_res = client.post(
            "/api/notes",
            json={"title": "Read me", "content": "body text"},
            headers=auth_headers(auth_token),
        )
        note_id = create_res.json()["id"]
        res = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["title"] == "Read me"
        assert data["content"] == "body text"

    def test_get_note_nonexistent(self, client, auth_token):
        res = client.get("/api/notes/nonexistent", headers=auth_headers(auth_token))
        assert res.status_code == 404

    def test_update_note_title(self, client, auth_token):
        create_res = client.post(
            "/api/notes",
            json={"title": "Old"},
            headers=auth_headers(auth_token),
        )
        note_id = create_res.json()["id"]
        res = client.patch(
            f"/api/notes/{note_id}",
            json={"title": "New"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        # Verify
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["title"] == "New"

    def test_update_note_content(self, client, auth_token):
        create_res = client.post(
            "/api/notes",
            json={"title": "Note", "content": "old"},
            headers=auth_headers(auth_token),
        )
        note_id = create_res.json()["id"]
        res = client.patch(
            f"/api/notes/{note_id}",
            json={"content": "# New content"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["content"] == "# New content"

    def test_update_note_empty_title_rejected(self, client, auth_token):
        create_res = client.post(
            "/api/notes",
            json={"title": "Note"},
            headers=auth_headers(auth_token),
        )
        note_id = create_res.json()["id"]
        res = client.patch(
            f"/api/notes/{note_id}",
            json={"title": "  "},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 400
        assert "empty" in res.json()["detail"].lower()

    def test_update_note_content_too_large(self, client, auth_token):
        create_res = client.post(
            "/api/notes",
            json={"title": "Note"},
            headers=auth_headers(auth_token),
        )
        note_id = create_res.json()["id"]
        res = client.patch(
            f"/api/notes/{note_id}",
            json={"content": "x" * 600_000},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 400

    def test_update_note_nonexistent(self, client, auth_token):
        res = client.patch(
            "/api/notes/nonexistent",
            json={"title": "X"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 404

    def test_delete_note(self, client, auth_token):
        create_res = client.post(
            "/api/notes",
            json={"title": "Delete me"},
            headers=auth_headers(auth_token),
        )
        note_id = create_res.json()["id"]
        res = client.delete(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        # Verify deleted
        get_res = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert get_res.status_code == 404

    def test_delete_note_nonexistent(self, client, auth_token):
        res = client.delete("/api/notes/nonexistent", headers=auth_headers(auth_token))
        assert res.status_code == 404


class TestNotesAuth:
    def test_list_notes_unauthenticated(self, client):
        res = client.get("/api/notes")
        assert res.status_code in (401, 403)

    def test_create_note_unauthenticated(self, client):
        res = client.post("/api/notes", json={"title": "X"})
        assert res.status_code in (401, 403)

    def test_get_note_unauthenticated(self, client):
        res = client.get("/api/notes/some-id")
        assert res.status_code in (401, 403)

    def test_update_note_unauthenticated(self, client):
        res = client.patch("/api/notes/some-id", json={"title": "X"})
        assert res.status_code in (401, 403)

    def test_delete_note_unauthenticated(self, client):
        res = client.delete("/api/notes/some-id")
        assert res.status_code in (401, 403)

    def test_invalid_token(self, client):
        res = client.get("/api/notes", headers={"Authorization": "Bearer invalid"})
        assert res.status_code == 401


class TestNotesUserIsolation:
    """Security: user A must NOT be able to access user B's notes."""

    def test_cannot_read_other_users_note(self, client):
        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")

        # Alice creates a note
        create_res = client.post(
            "/api/notes",
            json={"title": "Alice secret", "content": "private"},
            headers=auth_headers(token_a),
        )
        note_id = create_res.json()["id"]

        # Bob tries to read it
        res = client.get(f"/api/notes/{note_id}", headers=auth_headers(token_b))
        assert res.status_code == 404

    def test_cannot_update_other_users_note(self, client):
        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")

        create_res = client.post(
            "/api/notes",
            json={"title": "Alice note"},
            headers=auth_headers(token_a),
        )
        note_id = create_res.json()["id"]

        res = client.patch(
            f"/api/notes/{note_id}",
            json={"title": "Hijacked!"},
            headers=auth_headers(token_b),
        )
        assert res.status_code == 404

        # Verify original is unchanged
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(token_a))
        assert fetched.json()["title"] == "Alice note"

    def test_cannot_delete_other_users_note(self, client):
        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")

        create_res = client.post(
            "/api/notes",
            json={"title": "Alice note", "content": "private"},
            headers=auth_headers(token_a),
        )
        note_id = create_res.json()["id"]

        # Give the note some tags
        client.patch(
            f"/api/notes/{note_id}",
            json={"tags": ["important", "work"]},
            headers=auth_headers(token_a),
        )

        # Bob tries to delete it
        res = client.delete(f"/api/notes/{note_id}", headers=auth_headers(token_b))
        assert res.status_code == 404

        # Note still exists for Alice — AND tags must NOT have been wiped
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(token_a))
        assert fetched.status_code == 200
        assert set(fetched.json()["tags"]) == {"important", "work"}

    def test_list_only_own_notes(self, client):
        token_a = _create_user_and_login(client, "alice", "secret123")
        token_b = _create_user_and_login(client, "bob", "secret456")

        client.post("/api/notes", json={"title": "Alice 1"}, headers=auth_headers(token_a))
        client.post("/api/notes", json={"title": "Alice 2"}, headers=auth_headers(token_a))
        client.post("/api/notes", json={"title": "Bob 1"}, headers=auth_headers(token_b))

        alice_notes = client.get("/api/notes", headers=auth_headers(token_a)).json()["notes"]
        bob_notes = client.get("/api/notes", headers=auth_headers(token_b)).json()["notes"]

        assert len(alice_notes) == 2
        assert len(bob_notes) == 1
        assert all(n["title"].startswith("Alice") for n in alice_notes)
        assert bob_notes[0]["title"] == "Bob 1"


class TestNotesEdgeCases:
    def test_unicode_content(self, client, auth_token):
        res = client.post(
            "/api/notes",
            json={"title": "Notas 📝", "content": "Olá mundo! 🌍\n## Seção\n- Item ✓"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        note_id = res.json()["id"]
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert "📝" in fetched.json()["title"]
        assert "🌍" in fetched.json()["content"]

    def test_complex_markdown_content(self, client, auth_token):
        markdown = """# Heading 1
## Heading 2

Some **bold** and *italic* text.

```python
def hello():
    print("world")
```

| Col A | Col B |
|-------|-------|
| 1     | 2     |

> Blockquote here

- [x] Task done
- [ ] Task pending
"""
        res = client.post(
            "/api/notes",
            json={"title": "Markdown test", "content": markdown},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        note_id = res.json()["id"]
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["content"] == markdown

    def test_special_characters_in_title(self, client, auth_token):
        res = client.post(
            "/api/notes",
            json={"title": "Test <script>alert('xss')</script> & \"quotes\""},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        note_id = res.json()["id"]
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert "<script>" in fetched.json()["title"]

    def test_empty_content_preserved(self, client, auth_token):
        res = client.post(
            "/api/notes",
            json={"title": "Empty note", "content": ""},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        note_id = res.json()["id"]
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["content"] == ""

    def test_update_content_to_empty(self, client, auth_token):
        create_res = client.post(
            "/api/notes",
            json={"title": "Note", "content": "has content"},
            headers=auth_headers(auth_token),
        )
        note_id = create_res.json()["id"]
        res = client.patch(
            f"/api/notes/{note_id}",
            json={"content": ""},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["content"] == ""


class TestNoteFolders:
    def _create_folder(self, client, token, name="Pasta"):
        res = client.post(
            "/api/notes/folders",
            json={"name": name},
            headers=auth_headers(token),
        )
        assert res.status_code == 201
        return res.json()

    def test_list_folders_empty(self, client, auth_token):
        res = client.get("/api/notes/folders", headers=auth_headers(auth_token))
        assert res.status_code == 200
        assert res.json()["folders"] == []

    def test_create_folder(self, client, auth_token):
        folder = self._create_folder(client, auth_token, "Projetos")
        assert folder["name"] == "Projetos"
        assert folder["id"]

    def test_create_folder_empty_name_rejected(self, client, auth_token):
        res = client.post(
            "/api/notes/folders",
            json={"name": "  "},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 400

    def test_list_folders(self, client, auth_token):
        self._create_folder(client, auth_token, "A")
        self._create_folder(client, auth_token, "B")
        res = client.get("/api/notes/folders", headers=auth_headers(auth_token))
        assert len(res.json()["folders"]) == 2

    def test_rename_folder(self, client, auth_token):
        folder = self._create_folder(client, auth_token, "Old")
        res = client.patch(
            f"/api/notes/folders/{folder['id']}",
            json={"name": "New"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200
        folders = client.get("/api/notes/folders", headers=auth_headers(auth_token)).json()["folders"]
        assert folders[0]["name"] == "New"

    def test_rename_folder_not_found(self, client, auth_token):
        res = client.patch(
            "/api/notes/folders/nonexistent",
            json={"name": "New"},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 404

    def test_delete_folder(self, client, auth_token):
        folder = self._create_folder(client, auth_token)
        res = client.delete(f"/api/notes/folders/{folder['id']}", headers=auth_headers(auth_token))
        assert res.status_code == 200
        folders = client.get("/api/notes/folders", headers=auth_headers(auth_token)).json()["folders"]
        assert folders == []

    def test_delete_folder_not_found(self, client, auth_token):
        res = client.delete("/api/notes/folders/nonexistent", headers=auth_headers(auth_token))
        assert res.status_code == 404

    def test_delete_folder_notes_moved_to_root(self, client, auth_token):
        folder = self._create_folder(client, auth_token)
        note_res = client.post(
            "/api/notes",
            json={"title": "Note", "folder_id": folder["id"]},
            headers=auth_headers(auth_token),
        )
        note_id = note_res.json()["id"]
        client.delete(f"/api/notes/folders/{folder['id']}", headers=auth_headers(auth_token))
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["folder_id"] is None

    def test_note_create_with_folder_id(self, client, auth_token):
        folder = self._create_folder(client, auth_token)
        res = client.post(
            "/api/notes",
            json={"title": "Note in folder", "folder_id": folder["id"]},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 201
        assert res.json()["folder_id"] == folder["id"]

    def test_note_move_to_folder(self, client, auth_token):
        folder = self._create_folder(client, auth_token)
        note_res = client.post("/api/notes", json={"title": "Note"}, headers=auth_headers(auth_token))
        note_id = note_res.json()["id"]
        res = client.patch(
            f"/api/notes/{note_id}",
            json={"folder_id": folder["id"]},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["folder_id"] == folder["id"]

    def test_note_remove_from_folder(self, client, auth_token):
        folder = self._create_folder(client, auth_token)
        note_res = client.post(
            "/api/notes",
            json={"title": "Note", "folder_id": folder["id"]},
            headers=auth_headers(auth_token),
        )
        note_id = note_res.json()["id"]
        res = client.patch(
            f"/api/notes/{note_id}",
            json={"folder_id": None},
            headers=auth_headers(auth_token),
        )
        assert res.status_code == 200
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["folder_id"] is None

    def test_list_notes_includes_folder_id(self, client, auth_token):
        folder = self._create_folder(client, auth_token)
        client.post("/api/notes", json={"title": "In folder", "folder_id": folder["id"]}, headers=auth_headers(auth_token))
        client.post("/api/notes", json={"title": "No folder"}, headers=auth_headers(auth_token))
        notes = client.get("/api/notes", headers=auth_headers(auth_token)).json()["notes"]
        folder_ids = {n.get("folder_id") for n in notes}
        assert folder["id"] in folder_ids
        assert None in folder_ids

    def test_folders_require_auth(self, client):
        res = client.get("/api/notes/folders")
        assert res.status_code == 401

    def test_update_note_without_folder_id_preserves_it(self, client, auth_token):
        folder = self._create_folder(client, auth_token)
        note_res = client.post(
            "/api/notes",
            json={"title": "Note", "folder_id": folder["id"]},
            headers=auth_headers(auth_token),
        )
        note_id = note_res.json()["id"]
        # Update only title, folder_id should be preserved
        client.patch(f"/api/notes/{note_id}", json={"title": "New title"}, headers=auth_headers(auth_token))
        fetched = client.get(f"/api/notes/{note_id}", headers=auth_headers(auth_token))
        assert fetched.json()["folder_id"] == folder["id"]
