"""Tests for email importance rules endpoints in web_app.app."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_email_rules.sqlite3")

    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-email-rules")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")
    monkeypatch.setenv("GOOGLE_OAUTH_CALLBACK_URL", "")

    import web_app.dependencies as deps
    deps._user_store = None
    deps._assistant_service = None
    deps._google_oauth = None
    deps._credential_store = None
    deps._health_store = None

    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


@pytest.fixture
def auth_token(client):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user("emailrules", "testpass123", display_name="Email Rules")
    res = client.post("/api/auth/login", json={"username": "emailrules", "password": "testpass123"})
    assert res.status_code == 200
    return res.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_email_rules_require_auth(client):
    res = client.get("/api/email/importance-rules")
    assert res.status_code in (401, 403)


def test_get_empty_email_rules(client, auth_token):
    res = client.get("/api/email/importance-rules", headers=_auth(auth_token))
    assert res.status_code == 200
    assert res.json() == {"senders": [], "keywords": []}


def test_update_and_get_email_rules(client, auth_token):
    payload = {
        "senders": ["banco@avisos.com", "cliente@empresa.com"],
        "keywords": ["boleto", "prazo", "pagamento"],
    }
    update = client.put("/api/email/importance-rules", json=payload, headers=_auth(auth_token))
    assert update.status_code == 200

    fetch = client.get("/api/email/importance-rules", headers=_auth(auth_token))
    assert fetch.status_code == 200
    data = fetch.json()
    assert data["senders"] == payload["senders"]
    assert data["keywords"] == payload["keywords"]
