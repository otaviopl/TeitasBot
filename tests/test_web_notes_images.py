"""Tests for note image upload and serve endpoints."""
from __future__ import annotations

import io
import os

import httpx
import pytest
from fastapi.testclient import TestClient

_ORIGINAL_HTTPX_REQUEST = httpx.Client.request

# Minimal fake image bytes (content-type is enforced via the upload field, not magic bytes)
_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_images.sqlite3")
    images_dir = str(tmp_path / "note_images")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-images")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")
    monkeypatch.setenv("NOTES_IMAGES_DIR", images_dir)

    import web_app.dependencies as deps
    import web_app.app as app_module
    deps._user_store = None
    deps._assistant_service = None
    app_module._NOTE_IMAGES_DIR = os.path.abspath(images_dir)

    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


def _create_and_login(client, username="imguser", password="pass1234"):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user(username, password, display_name=username.title())
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["token"]


@pytest.fixture
def token(client):
    return _create_and_login(client)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


class TestNoteImageUpload:
    def test_upload_jpeg_success(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("photo.jpg", io.BytesIO(_FAKE_JPEG), "image/jpeg")},
            headers=_auth(token),
        )
        assert res.status_code == 201
        data = res.json()
        assert "url" in data
        assert data["url"].startswith("/api/notes/images/")
        assert data["url"].endswith(".jpg")

    def test_upload_png_success(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("image.png", io.BytesIO(_FAKE_PNG), "image/png")},
            headers=_auth(token),
        )
        assert res.status_code == 201
        assert res.json()["url"].endswith(".png")

    def test_upload_unsupported_mime_rejected(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            headers=_auth(token),
        )
        assert res.status_code == 415

    def test_upload_oversized_rejected(self, client, token):
        big_data = b"\xff\xd8" + b"\x00" * (6 * 1024 * 1024)
        res = client.post(
            "/api/notes/images",
            files={"file": ("big.jpg", io.BytesIO(big_data), "image/jpeg")},
            headers=_auth(token),
        )
        assert res.status_code == 413

    def test_upload_requires_auth(self, client):
        res = client.post(
            "/api/notes/images",
            files={"file": ("photo.jpg", io.BytesIO(_FAKE_JPEG), "image/jpeg")},
        )
        assert res.status_code == 401


class TestNoteImageServe:
    def _upload(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("test.jpg", io.BytesIO(_FAKE_JPEG), "image/jpeg")},
            headers=_auth(token),
        )
        assert res.status_code == 201
        return res.json()["url"]  # e.g. /api/notes/images/{filename}

    def test_serve_with_bearer(self, client, token):
        url = self._upload(client, token)
        res = client.get(url, headers=_auth(token))
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/jpeg")

    def test_serve_with_query_token(self, client, token):
        url = self._upload(client, token)
        filename = url.split("/")[-1]
        res = client.get(f"/api/notes/images/{filename}?token={token}")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/jpeg")

    def test_serve_requires_auth(self, client, token):
        url = self._upload(client, token)
        filename = url.split("/")[-1]
        res = client.get(f"/api/notes/images/{filename}")
        assert res.status_code == 401

    def test_serve_wrong_user_gets_404(self, client, token):
        url = self._upload(client, token)
        filename = url.split("/")[-1]
        token2 = _create_and_login(client, username="other", password="pass9999")
        res = client.get(f"/api/notes/images/{filename}", headers=_auth(token2))
        assert res.status_code == 404

    def test_serve_nonexistent_404(self, client, token):
        res = client.get("/api/notes/images/nonexistent.jpg", headers=_auth(token))
        assert res.status_code == 404


import io
import os

import httpx
import pytest
from fastapi.testclient import TestClient

_ORIGINAL_HTTPX_REQUEST = httpx.Client.request

# Minimal 1×1 JPEG bytes
_JPEG_1X1 = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
    b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd4P\x00\x00\x00\xff\xd9"
)

# Minimal 1×1 PNG bytes
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test_images.sqlite3")
    images_dir = str(tmp_path / "note_images")
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-images")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")
    monkeypatch.setenv("NOTES_IMAGES_DIR", images_dir)

    import web_app.dependencies as deps
    import web_app.app as app_module
    deps._user_store = None
    deps._assistant_service = None
    # Reset the module-level constant so it picks up the monkeypatched env var
    app_module._NOTE_IMAGES_DIR = os.path.abspath(images_dir)

    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


def _create_and_login(client, username="imguser", password="pass1234"):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user(username, password, display_name=username.title())
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["token"]


@pytest.fixture
def token(client):
    return _create_and_login(client)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


class TestNoteImageUpload:
    def test_upload_jpeg_success(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("photo.jpg", io.BytesIO(_JPEG_1X1), "image/jpeg")},
            headers=_auth(token),
        )
        assert res.status_code == 201
        data = res.json()
        assert "url" in data
        assert data["url"].startswith("/api/notes/images/")
        assert data["url"].endswith(".jpg")

    def test_upload_png_success(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("image.png", io.BytesIO(_PNG_1X1), "image/png")},
            headers=_auth(token),
        )
        assert res.status_code == 201
        assert res.json()["url"].endswith(".png")

    def test_upload_unsupported_mime_rejected(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            headers=_auth(token),
        )
        assert res.status_code == 415

    def test_upload_oversized_rejected(self, client, token):
        big_data = b"\xff\xd8" + b"\x00" * (6 * 1024 * 1024)
        res = client.post(
            "/api/notes/images",
            files={"file": ("big.jpg", io.BytesIO(big_data), "image/jpeg")},
            headers=_auth(token),
        )
        assert res.status_code == 413

    def test_upload_requires_auth(self, client):
        res = client.post(
            "/api/notes/images",
            files={"file": ("photo.jpg", io.BytesIO(_JPEG_1X1), "image/jpeg")},
        )
        assert res.status_code == 401


class TestNoteImageServe:
    def _upload(self, client, token):
        res = client.post(
            "/api/notes/images",
            files={"file": ("test.jpg", io.BytesIO(_JPEG_1X1), "image/jpeg")},
            headers=_auth(token),
        )
        assert res.status_code == 201
        return res.json()["url"]  # e.g. /api/notes/images/{filename}

    def test_serve_with_bearer(self, client, token):
        url = self._upload(client, token)
        res = client.get(url, headers=_auth(token))
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/jpeg")

    def test_serve_with_query_token(self, client, token):
        url = self._upload(client, token)
        filename = url.split("/")[-1]
        res = client.get(f"/api/notes/images/{filename}?token={token}")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/jpeg")

    def test_serve_requires_auth(self, client, token):
        url = self._upload(client, token)
        filename = url.split("/")[-1]
        res = client.get(f"/api/notes/images/{filename}")
        assert res.status_code == 401

    def test_serve_wrong_user_gets_404(self, client, token):
        url = self._upload(client, token)
        filename = url.split("/")[-1]
        # Create a second user
        token2 = _create_and_login(client, username="other", password="pass9999")
        res = client.get(f"/api/notes/images/{filename}", headers=_auth(token2))
        assert res.status_code == 404

    def test_serve_nonexistent_404(self, client, token):
        res = client.get("/api/notes/images/nonexistent.jpg", headers=_auth(token))
        assert res.status_code == 404
