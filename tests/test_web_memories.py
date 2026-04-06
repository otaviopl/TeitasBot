"""Tests for the Memories endpoint in web_app.app."""
from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

# Save original httpx.Client.request before conftest patches it.
_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


# ---- Fixtures ----


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_memories.sqlite3")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-memories-tests")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")
    monkeypatch.setenv("GOOGLE_OAUTH_CALLBACK_URL", "")
    monkeypatch.setenv("ASSISTANT_MEMORIES_DIR", str(tmp_path / "memories"))

    for var in (
        "NOTION_API_KEY", "NOTION_DATABASE_ID", "NOTION_NOTES_DB_ID",
        "NOTION_EXERCISES_DB_ID", "NOTION_MEALS_DB_ID",
        "NOTION_EXPENSES_DB_ID", "NOTION_MONTHLY_BILLS_DB_ID",
    ):
        monkeypatch.setenv(var, "")

    import web_app.dependencies as deps
    deps._user_store = None
    deps._assistant_service = None
    deps._google_oauth = None
    deps._credential_store = None

    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


@pytest.fixture
def auth_token(client):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user("memuser", "testpass123", display_name="Mem User")

    res = client.post("/api/auth/login", json={"username": "memuser", "password": "testpass123"})
    assert res.status_code == 200
    return res.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---- Tests ----


class TestMemoriesRequiresAuth:
    def test_returns_401_without_token(self, client):
        res = client.get("/api/memories")
        assert res.status_code in (401, 403)


class TestMemoriesEmptyDirectory:
    def test_returns_empty_when_no_user_dir(self, client, auth_token):
        res = client.get("/api/memories", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["files"] == []
        assert data["count"] == 0


class TestMemoriesWithFiles:
    def test_returns_memory_files(self, client, auth_token, tmp_path):
        user_dir = tmp_path / "memories" / "webmemuser"
        user_dir.mkdir(parents=True)
        (user_dir / "about-me.md").write_text("I like coding", encoding="utf-8")
        (user_dir / "health.md").write_text("Exercício diário", encoding="utf-8")

        res = client.get("/api/memories", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 2
        filenames = [f["filename"] for f in data["files"]]
        assert "about-me.md" in filenames
        assert "health.md" in filenames

    def test_display_name_formatting(self, client, auth_token, tmp_path):
        user_dir = tmp_path / "memories" / "webmemuser"
        user_dir.mkdir(parents=True)
        (user_dir / "about-me.md").write_text("content", encoding="utf-8")

        res = client.get("/api/memories", headers=_auth(auth_token))
        data = res.json()
        file_entry = data["files"][0]
        assert file_entry["display_name"] == "About Me"

    def test_file_content_included(self, client, auth_token, tmp_path):
        user_dir = tmp_path / "memories" / "webmemuser"
        user_dir.mkdir(parents=True)
        (user_dir / "notes.md").write_text("My secret notes", encoding="utf-8")

        res = client.get("/api/memories", headers=_auth(auth_token))
        data = res.json()
        assert data["files"][0]["content"] == "My secret notes"

    def test_excludes_readme(self, client, auth_token, tmp_path):
        user_dir = tmp_path / "memories" / "webmemuser"
        user_dir.mkdir(parents=True)
        (user_dir / "README.md").write_text("Do not show", encoding="utf-8")
        (user_dir / "real-memory.md").write_text("Show this", encoding="utf-8")

        res = client.get("/api/memories", headers=_auth(auth_token))
        data = res.json()
        assert data["count"] == 1
        assert data["files"][0]["filename"] == "real-memory.md"

    def test_excludes_non_md_files(self, client, auth_token, tmp_path):
        user_dir = tmp_path / "memories" / "webmemuser"
        user_dir.mkdir(parents=True)
        (user_dir / "contacts.csv").write_text("name,email", encoding="utf-8")
        (user_dir / "data.json").write_text("{}", encoding="utf-8")
        (user_dir / "notes.md").write_text("Only md files", encoding="utf-8")

        res = client.get("/api/memories", headers=_auth(auth_token))
        data = res.json()
        assert data["count"] == 1
        assert data["files"][0]["filename"] == "notes.md"

    def test_files_sorted_alphabetically(self, client, auth_token, tmp_path):
        user_dir = tmp_path / "memories" / "webmemuser"
        user_dir.mkdir(parents=True)
        (user_dir / "zebra.md").write_text("z", encoding="utf-8")
        (user_dir / "alpha.md").write_text("a", encoding="utf-8")
        (user_dir / "middle.md").write_text("m", encoding="utf-8")

        res = client.get("/api/memories", headers=_auth(auth_token))
        data = res.json()
        filenames = [f["filename"] for f in data["files"]]
        assert filenames == ["alpha.md", "middle.md", "zebra.md"]


class TestMemoriesPathTraversal:
    def test_symlink_outside_dir_excluded(self, client, auth_token, tmp_path):
        user_dir = tmp_path / "memories" / "webmemuser"
        user_dir.mkdir(parents=True)
        outside = tmp_path / "secret.md"
        outside.write_text("secret data", encoding="utf-8")
        os.symlink(str(outside), str(user_dir / "evil-link.md"))
        (user_dir / "legit.md").write_text("ok", encoding="utf-8")

        res = client.get("/api/memories", headers=_auth(auth_token))
        data = res.json()
        # Symlink resolves outside user_dir, so it should be excluded
        filenames = [f["filename"] for f in data["files"]]
        assert "evil-link.md" not in filenames
        assert "legit.md" in filenames
