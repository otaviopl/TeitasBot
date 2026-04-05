"""Tests for web_app.user_store."""
from __future__ import annotations

import os
import tempfile

import pytest

from web_app.user_store import WebUserStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_users.sqlite3")
    return WebUserStore(db_path)


class TestCreateUser:
    def test_create_user_success(self, store):
        user = store.create_user("alice", "secret123", display_name="Alice")
        assert user["username"] == "alice"
        assert user["display_name"] == "Alice"
        assert user["is_active"] is True
        assert user["id"]

    def test_create_user_normalizes_username(self, store):
        user = store.create_user("  Bob  ", "secret123")
        assert user["username"] == "bob"

    def test_create_duplicate_username_raises(self, store):
        store.create_user("alice", "secret123")
        with pytest.raises(ValueError, match="already exists"):
            store.create_user("Alice", "other_pass")

    def test_create_empty_username_raises(self, store):
        with pytest.raises(ValueError, match="cannot be empty"):
            store.create_user("", "secret123")

    def test_create_short_username_raises(self, store):
        with pytest.raises(ValueError, match="at least 3"):
            store.create_user("ab", "secret123")

    def test_create_short_password_raises(self, store):
        with pytest.raises(ValueError, match="at least 6"):
            store.create_user("alice", "123")


class TestAuthenticate:
    def test_authenticate_success(self, store):
        store.create_user("alice", "secret123", display_name="Alice")
        user = store.authenticate("alice", "secret123")
        assert user is not None
        assert user["username"] == "alice"
        assert user["display_name"] == "Alice"

    def test_authenticate_case_insensitive_username(self, store):
        store.create_user("alice", "secret123")
        user = store.authenticate("Alice", "secret123")
        assert user is not None

    def test_authenticate_wrong_password(self, store):
        store.create_user("alice", "secret123")
        assert store.authenticate("alice", "wrong") is None

    def test_authenticate_nonexistent_user(self, store):
        assert store.authenticate("nobody", "pass") is None

    def test_authenticate_inactive_user(self, store):
        store.create_user("alice", "secret123")
        store.deactivate_user("alice")
        assert store.authenticate("alice", "secret123") is None

    def test_authenticate_empty_inputs(self, store):
        assert store.authenticate("", "pass") is None
        assert store.authenticate("alice", "") is None


class TestGetUser:
    def test_get_by_username(self, store):
        created = store.create_user("alice", "secret123", display_name="Alice")
        user = store.get_user_by_username("alice")
        assert user is not None
        assert user["id"] == created["id"]

    def test_get_by_id(self, store):
        created = store.create_user("alice", "secret123")
        user = store.get_user_by_id(created["id"])
        assert user is not None
        assert user["username"] == "alice"

    def test_get_nonexistent(self, store):
        assert store.get_user_by_username("nobody") is None
        assert store.get_user_by_id("nonexistent") is None


class TestListUsers:
    def test_list_empty(self, store):
        assert store.list_users() == []

    def test_list_multiple(self, store):
        store.create_user("alice", "secret123")
        store.create_user("bob", "secret456")
        users = store.list_users()
        assert len(users) == 2
        assert users[0]["username"] == "alice"
        assert users[1]["username"] == "bob"


class TestDeactivate:
    def test_deactivate_success(self, store):
        store.create_user("alice", "secret123")
        assert store.deactivate_user("alice") is True
        user = store.get_user_by_username("alice")
        assert user["is_active"] is False

    def test_deactivate_nonexistent(self, store):
        assert store.deactivate_user("nobody") is False


class TestChangePassword:
    def test_change_password_success(self, store):
        store.create_user("alice", "old_pass123")
        assert store.change_password("alice", "new_pass456") is True
        assert store.authenticate("alice", "new_pass456") is not None
        assert store.authenticate("alice", "old_pass123") is None

    def test_change_password_short_raises(self, store):
        store.create_user("alice", "secret123")
        with pytest.raises(ValueError, match="at least 6"):
            store.change_password("alice", "abc")

    def test_change_password_nonexistent(self, store):
        assert store.change_password("nobody", "newpass123") is False


class TestConversations:
    def test_create_conversation(self, store):
        user = store.create_user("alice", "secret123")
        conv = store.create_conversation(user["id"], "Test chat")
        assert conv["title"] == "Test chat"
        assert conv["user_id"] == user["id"]
        assert conv["id"]

    def test_create_conversation_default_title(self, store):
        user = store.create_user("alice", "secret123")
        conv = store.create_conversation(user["id"])
        assert conv["title"] == "Nova conversa"

    def test_list_conversations_empty(self, store):
        user = store.create_user("alice", "secret123")
        assert store.list_conversations(user["id"]) == []

    def test_list_conversations_ordered_by_updated(self, store):
        user = store.create_user("alice", "secret123")
        c1 = store.create_conversation(user["id"], "First")
        c2 = store.create_conversation(user["id"], "Second")
        # Touch c1 so it has a newer updated_at
        import time
        time.sleep(1.1)
        store.touch_conversation(c1["id"])
        convs = store.list_conversations(user["id"])
        assert len(convs) == 2
        # c1 was touched most recently, should be first
        assert convs[0]["id"] == c1["id"]

    def test_get_conversation(self, store):
        user = store.create_user("alice", "secret123")
        conv = store.create_conversation(user["id"], "Chat")
        fetched = store.get_conversation(conv["id"], user["id"])
        assert fetched is not None
        assert fetched["title"] == "Chat"

    def test_get_conversation_wrong_user(self, store):
        u1 = store.create_user("alice", "secret123")
        u2 = store.create_user("bob", "secret456")
        conv = store.create_conversation(u1["id"], "Alice's chat")
        assert store.get_conversation(conv["id"], u2["id"]) is None

    def test_get_conversation_nonexistent(self, store):
        user = store.create_user("alice", "secret123")
        assert store.get_conversation("nonexistent", user["id"]) is None

    def test_rename_conversation(self, store):
        user = store.create_user("alice", "secret123")
        conv = store.create_conversation(user["id"], "Old title")
        assert store.rename_conversation(conv["id"], user["id"], "New title") is True
        fetched = store.get_conversation(conv["id"], user["id"])
        assert fetched["title"] == "New title"

    def test_rename_conversation_wrong_user(self, store):
        u1 = store.create_user("alice", "secret123")
        u2 = store.create_user("bob", "secret456")
        conv = store.create_conversation(u1["id"], "Chat")
        assert store.rename_conversation(conv["id"], u2["id"], "Hijacked") is False

    def test_delete_conversation(self, store):
        user = store.create_user("alice", "secret123")
        conv = store.create_conversation(user["id"], "Delete me")
        assert store.delete_conversation(conv["id"], user["id"]) is True
        assert store.get_conversation(conv["id"], user["id"]) is None

    def test_delete_conversation_nonexistent(self, store):
        user = store.create_user("alice", "secret123")
        assert store.delete_conversation("nonexistent", user["id"]) is False

    def test_touch_conversation(self, store):
        user = store.create_user("alice", "secret123")
        conv = store.create_conversation(user["id"], "Touch me")
        original_updated = conv["updated_at"]
        import time
        time.sleep(1.1)
        store.touch_conversation(conv["id"])
        fetched = store.get_conversation(conv["id"], user["id"])
        assert fetched["updated_at"] >= original_updated
