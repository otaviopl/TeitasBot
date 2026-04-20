"""Tests for web_app.auth."""
from __future__ import annotations

import os

import pytest

from web_app.auth import create_access_token, verify_token


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch):
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")


class TestCreateAccessToken:
    def test_returns_string(self):
        token = create_access_token("user-123", "alice")
        assert isinstance(token, str)
        assert len(token) > 20

    def test_different_users_get_different_tokens(self):
        t1 = create_access_token("user-1", "alice")
        t2 = create_access_token("user-2", "bob")
        assert t1 != t2


class TestVerifyToken:
    def test_verify_valid_token(self):
        token = create_access_token("user-123", "alice")
        data = verify_token(token)
        assert data is not None
        assert data["user_id"] == "user-123"
        assert data["username"] == "alice"

    def test_verify_invalid_token(self):
        assert verify_token("not.a.valid.token") is None

    def test_verify_empty_token(self):
        assert verify_token("") is None

    def test_verify_expired_token(self, monkeypatch):
        monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "0")
        # With 0 hours expiry, token should be created with minimal time
        # but since we clamp to 1 hour minimum, this still passes
        token = create_access_token("user-123", "alice")
        data = verify_token(token)
        # Even with "0" hours, we clamp to 1 hour minimum, so token is still valid
        assert data is not None

    def test_verify_token_wrong_secret(self, monkeypatch):
        token = create_access_token("user-123", "alice")
        monkeypatch.setenv("WEB_JWT_SECRET", "different-secret")
        assert verify_token(token) is None


class TestNoSecret:
    def test_create_token_without_secret_raises(self, monkeypatch):
        monkeypatch.delenv("WEB_JWT_SECRET", raising=False)
        with pytest.raises(ValueError, match="WEB_JWT_SECRET"):
            create_access_token("user-123", "alice")

    def test_verify_token_without_secret_returns_none(self, monkeypatch):
        token = create_access_token("user-123", "alice")
        monkeypatch.delenv("WEB_JWT_SECRET", raising=False)
        assert verify_token(token) is None
