"""Tests for the Notion connectivity check route in web_app.app."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# Save original httpx.Client.request before conftest patches it.
_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


# ---- Fixtures ----


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_notion_status.sqlite3")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-notion-tests")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")
    monkeypatch.setenv("GOOGLE_OAUTH_CALLBACK_URL", "")

    # Set Notion env vars to empty so load_dotenv() won't reload from .env
    for var in (
        "NOTION_API_KEY",
        "NOTION_DATABASE_ID",
        "NOTION_NOTES_DB_ID",
        "NOTION_EXERCISES_DB_ID",
        "NOTION_MEALS_DB_ID",
        "NOTION_EXPENSES_DB_ID",
        "NOTION_MONTHLY_BILLS_DB_ID",
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
    store.create_user("notionuser", "testpass123", display_name="Notion User")

    res = client.post("/api/auth/login", json={"username": "notionuser", "password": "testpass123"})
    assert res.status_code == 200
    return res.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---- Tests ----


class TestNotionCheckRequiresAuth:
    def test_returns_401_without_token(self, client):
        res = client.get("/api/notion/check")
        assert res.status_code in (401, 403)


class TestNotionCheckNoApiKey:
    def test_returns_not_configured_when_no_api_key(self, client, auth_token):
        res = client.get("/api/notion/check", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["api_key_configured"] is False
        assert isinstance(data["databases"], dict)
        assert len(data["databases"]) == 6
        for status in data["databases"].values():
            assert status == "not_configured"


class TestNotionCheckResponseStructure:
    def test_response_has_correct_structure(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-api-key")
        import web_app.dependencies as deps
        deps._credential_store = None

        with patch("web_app.app._check_notion_database", return_value="ok"):
            monkeypatch.setenv("NOTION_DATABASE_ID", "db-tarefas-id")
            res = client.get("/api/notion/check", headers=_auth(auth_token))

        assert res.status_code == 200
        data = res.json()
        assert "api_key_configured" in data
        assert "databases" in data
        assert data["api_key_configured"] is True

        expected_names = {"Tarefas", "Anotações", "Exercícios", "Refeições", "Despesas", "Controle Financeiro"}
        assert set(data["databases"].keys()) == expected_names


class TestNotionCheckDatabaseStatuses:
    def test_mixed_statuses(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-api-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "db-tarefas-id")
        monkeypatch.setenv("NOTION_NOTES_DB_ID", "db-notes-id")
        import web_app.dependencies as deps
        deps._credential_store = None

        def mock_check(db_id, api_key):
            if db_id == "db-tarefas-id":
                return "ok"
            return "error"

        with patch("web_app.app._check_notion_database", side_effect=mock_check):
            res = client.get("/api/notion/check", headers=_auth(auth_token))

        assert res.status_code == 200
        data = res.json()
        assert data["api_key_configured"] is True
        assert data["databases"]["Tarefas"] == "ok"
        assert data["databases"]["Anotações"] == "error"
        # Databases without env vars should be not_configured
        assert data["databases"]["Exercícios"] == "not_configured"
        assert data["databases"]["Refeições"] == "not_configured"

    def test_all_databases_ok(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-api-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "db1")
        monkeypatch.setenv("NOTION_NOTES_DB_ID", "db2")
        monkeypatch.setenv("NOTION_EXERCISES_DB_ID", "db3")
        monkeypatch.setenv("NOTION_MEALS_DB_ID", "db4")
        monkeypatch.setenv("NOTION_EXPENSES_DB_ID", "db5")
        monkeypatch.setenv("NOTION_MONTHLY_BILLS_DB_ID", "db6")
        import web_app.dependencies as deps
        deps._credential_store = None

        with patch("web_app.app._check_notion_database", return_value="ok"):
            res = client.get("/api/notion/check", headers=_auth(auth_token))

        data = res.json()
        assert data["api_key_configured"] is True
        for status in data["databases"].values():
            assert status == "ok"


class TestCheckNotionDatabaseHelper:
    def test_returns_ok_on_200(self):
        from web_app.app import _check_notion_database

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = _check_notion_database("db-id-123", "api-key-456")

        assert result == "ok"
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "db-id-123" in call_kwargs[0][0]
        assert call_kwargs[1]["headers"]["Authorization"] == "Bearer api-key-456"

    def test_returns_error_on_non_200(self):
        from web_app.app import _check_notion_database

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("requests.get", return_value=mock_resp):
            result = _check_notion_database("bad-db-id", "api-key")

        assert result == "error"

    def test_returns_error_on_exception(self):
        from web_app.app import _check_notion_database

        with patch("requests.get", side_effect=ConnectionError("timeout")):
            result = _check_notion_database("db-id", "api-key")

        assert result == "error"
