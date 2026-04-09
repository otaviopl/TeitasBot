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
        store.touch_conversation(c1["id"], user["id"])
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
        store.touch_conversation(conv["id"], user["id"])
        fetched = store.get_conversation(conv["id"], user["id"])
        assert fetched["updated_at"] >= original_updated

    def test_touch_conversation_wrong_user_is_noop(self, store):
        u1 = store.create_user("alice", "secret123")
        u2 = store.create_user("bob", "secret456")
        conv = store.create_conversation(u1["id"], "Alice's chat")
        original_updated = conv["updated_at"]
        import time
        time.sleep(1.1)
        # Bob tries to touch Alice's conversation — should be silently ignored
        store.touch_conversation(conv["id"], u2["id"])
        fetched = store.get_conversation(conv["id"], u1["id"])
        # updated_at must NOT have changed
        assert fetched["updated_at"] == original_updated

    def test_prune_oldest_conversations(self, store):
        user = store.create_user("alice", "secret123")
        convs = []
        for i in range(5):
            convs.append(store.create_conversation(user["id"], f"Conv {i}"))
        deleted = store.prune_oldest_conversations(user["id"], 3)
        assert len(deleted) == 2
        remaining = store.list_conversations(user["id"])
        assert len(remaining) == 3

    def test_prune_oldest_conversations_no_excess(self, store):
        user = store.create_user("alice", "secret123")
        store.create_conversation(user["id"], "Only one")
        deleted = store.prune_oldest_conversations(user["id"], 5)
        assert deleted == []
        assert len(store.list_conversations(user["id"])) == 1


class TestNotes:
    def test_create_note(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "My note", "# Hello")
        assert note["title"] == "My note"
        assert note["content"] == "# Hello"
        assert note["user_id"] == user["id"]
        assert note["id"]

    def test_create_note_default_title(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"])
        assert note["title"] == "Nova anotação"

    def test_create_note_empty_title_uses_default(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "  ")
        assert note["title"] == "Nova anotação"

    def test_create_note_content_limit(self, store):
        user = store.create_user("alice", "secret123")
        with pytest.raises(ValueError, match="500 KB"):
            store.create_note(user["id"], "Big", "x" * 600_000)

    def test_list_notes_empty(self, store):
        user = store.create_user("alice", "secret123")
        assert store.list_notes(user["id"]) == []

    def test_list_notes_ordered_by_updated(self, store):
        user = store.create_user("alice", "secret123")
        n1 = store.create_note(user["id"], "First")
        n2 = store.create_note(user["id"], "Second")
        import time
        time.sleep(1.1)
        store.update_note(n1["id"], user["id"], content="updated")
        notes = store.list_notes(user["id"])
        assert len(notes) == 2
        assert notes[0]["id"] == n1["id"]

    def test_list_notes_excludes_content(self, store):
        user = store.create_user("alice", "secret123")
        store.create_note(user["id"], "Note", "some content")
        notes = store.list_notes(user["id"])
        assert "content" not in notes[0]

    def test_list_notes_user_isolation(self, store):
        u1 = store.create_user("alice", "secret123")
        u2 = store.create_user("bob", "secret456")
        store.create_note(u1["id"], "Alice's note")
        store.create_note(u2["id"], "Bob's note")
        assert len(store.list_notes(u1["id"])) == 1
        assert len(store.list_notes(u2["id"])) == 1

    def test_get_note(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "Note", "content here")
        fetched = store.get_note(note["id"], user["id"])
        assert fetched is not None
        assert fetched["title"] == "Note"
        assert fetched["content"] == "content here"

    def test_get_note_wrong_user(self, store):
        u1 = store.create_user("alice", "secret123")
        u2 = store.create_user("bob", "secret456")
        note = store.create_note(u1["id"], "Alice's note")
        assert store.get_note(note["id"], u2["id"]) is None

    def test_get_note_nonexistent(self, store):
        user = store.create_user("alice", "secret123")
        assert store.get_note("nonexistent", user["id"]) is None

    def test_update_note_title(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "Old title")
        assert store.update_note(note["id"], user["id"], title="New title") is True
        fetched = store.get_note(note["id"], user["id"])
        assert fetched["title"] == "New title"

    def test_update_note_content(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "Note", "old")
        assert store.update_note(note["id"], user["id"], content="new content") is True
        fetched = store.get_note(note["id"], user["id"])
        assert fetched["content"] == "new content"

    def test_update_note_both(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "Old", "old content")
        assert store.update_note(note["id"], user["id"], title="New", content="new content") is True
        fetched = store.get_note(note["id"], user["id"])
        assert fetched["title"] == "New"
        assert fetched["content"] == "new content"

    def test_update_note_empty_title_raises(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "Note")
        with pytest.raises(ValueError, match="cannot be empty"):
            store.update_note(note["id"], user["id"], title="  ")

    def test_update_note_content_limit(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "Note")
        with pytest.raises(ValueError, match="500 KB"):
            store.update_note(note["id"], user["id"], content="x" * 600_000)

    def test_update_note_wrong_user(self, store):
        u1 = store.create_user("alice", "secret123")
        u2 = store.create_user("bob", "secret456")
        note = store.create_note(u1["id"], "Alice's note")
        assert store.update_note(note["id"], u2["id"], title="Hijacked") is False

    def test_update_note_nonexistent(self, store):
        user = store.create_user("alice", "secret123")
        assert store.update_note("nonexistent", user["id"], title="X") is False

    def test_delete_note(self, store):
        user = store.create_user("alice", "secret123")
        note = store.create_note(user["id"], "Delete me")
        assert store.delete_note(note["id"], user["id"]) is True
        assert store.get_note(note["id"], user["id"]) is None

    def test_delete_note_wrong_user(self, store):
        u1 = store.create_user("alice", "secret123")
        u2 = store.create_user("bob", "secret456")
        note = store.create_note(u1["id"], "Alice's note")
        assert store.delete_note(note["id"], u2["id"]) is False
        assert store.get_note(note["id"], u1["id"]) is not None

    def test_delete_note_nonexistent(self, store):
        user = store.create_user("alice", "secret123")
        assert store.delete_note("nonexistent", user["id"]) is False


class TestNoteFolders:
    def _user(self, store):
        return store.create_user("alice", "secret123")

    def test_create_folder(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Projetos")
        assert folder["id"]
        assert folder["name"] == "Projetos"
        assert folder["user_id"] == user["id"]

    def test_create_folder_empty_name_raises(self, store):
        user = self._user(store)
        with pytest.raises(ValueError, match="cannot be empty"):
            store.create_folder(user["id"], "  ")

    def test_list_folders_empty(self, store):
        user = self._user(store)
        assert store.list_folders(user["id"]) == []

    def test_list_folders(self, store):
        user = self._user(store)
        store.create_folder(user["id"], "A")
        store.create_folder(user["id"], "B")
        folders = store.list_folders(user["id"])
        assert len(folders) == 2
        names = [f["name"] for f in folders]
        assert "A" in names and "B" in names

    def test_list_folders_scoped_to_user(self, store):
        u1 = self._user(store)
        u2 = store.create_user("bob", "secret456")
        store.create_folder(u1["id"], "Alice folder")
        assert store.list_folders(u2["id"]) == []

    def test_rename_folder(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Old Name")
        result = store.rename_folder(folder["id"], user["id"], "New Name")
        assert result is True
        folders = store.list_folders(user["id"])
        assert folders[0]["name"] == "New Name"

    def test_rename_folder_empty_name_raises(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Folder")
        with pytest.raises(ValueError, match="cannot be empty"):
            store.rename_folder(folder["id"], user["id"], "  ")

    def test_rename_folder_wrong_user(self, store):
        u1 = self._user(store)
        u2 = store.create_user("bob", "secret456")
        folder = store.create_folder(u1["id"], "Folder")
        assert store.rename_folder(folder["id"], u2["id"], "Hijacked") is False

    def test_delete_folder_moves_notes_to_root(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Pasta")
        note = store.create_note(user["id"], "Nota", folder_id=folder["id"])
        assert note["folder_id"] == folder["id"]
        store.delete_folder(folder["id"], user["id"])
        updated = store.get_note(note["id"], user["id"])
        assert updated["folder_id"] is None

    def test_delete_folder_wrong_user(self, store):
        u1 = self._user(store)
        u2 = store.create_user("bob", "secret456")
        folder = store.create_folder(u1["id"], "Folder")
        assert store.delete_folder(folder["id"], u2["id"]) is False

    def test_delete_folder_nonexistent(self, store):
        user = self._user(store)
        assert store.delete_folder("nonexistent", user["id"]) is False

    def test_note_folder_id_in_list(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Pasta")
        store.create_note(user["id"], "In folder", folder_id=folder["id"])
        store.create_note(user["id"], "No folder")
        notes = store.list_notes(user["id"])
        folder_ids = {n["folder_id"] for n in notes}
        assert folder["id"] in folder_ids
        assert None in folder_ids

    def test_update_note_folder_id(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Pasta")
        note = store.create_note(user["id"], "Note")
        assert note["folder_id"] is None
        store.update_note(note["id"], user["id"], folder_id=folder["id"])
        updated = store.get_note(note["id"], user["id"])
        assert updated["folder_id"] == folder["id"]

    def test_update_note_folder_id_to_none(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Pasta")
        note = store.create_note(user["id"], "Note", folder_id=folder["id"])
        store.update_note(note["id"], user["id"], folder_id=None)
        updated = store.get_note(note["id"], user["id"])
        assert updated["folder_id"] is None

    def test_update_note_without_folder_id_preserves_it(self, store):
        user = self._user(store)
        folder = store.create_folder(user["id"], "Pasta")
        note = store.create_note(user["id"], "Note", folder_id=folder["id"])
        # Update only title, folder_id should be unchanged
        store.update_note(note["id"], user["id"], title="New title")
        updated = store.get_note(note["id"], user["id"])
        assert updated["folder_id"] == folder["id"]
