"""Tests for assistant_connector.tools.notes_tools."""
from __future__ import annotations

import os
import tempfile
import types
from unittest.mock import MagicMock

import pytest

from web_app.user_store import WebUserStore


@pytest.fixture()
def tmp_store(tmp_path):
    db = str(tmp_path / "test.sqlite3")
    store = WebUserStore(db_path=db)
    return store


@pytest.fixture()
def web_user(tmp_store):
    user = tmp_store.create_user("alice", "pass123", display_name="Alice")
    return user


@pytest.fixture()
def notes_mod(tmp_store, monkeypatch):
    """Import notes_tools with a test store and return the module."""
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", tmp_store._db_path)
    # Patch the module-level store
    import assistant_connector.tools.notes_tools as mod
    monkeypatch.setattr(mod, "_user_store", tmp_store)
    monkeypatch.setattr(mod, "_uid_cache", {})
    return mod


def _make_context(user_id: str = "web:alice"):
    ctx = types.SimpleNamespace(user_id=user_id)
    return ctx


# ── list_notes ──────────────────────────────────────────────

class TestListNotes:
    def test_empty(self, notes_mod, web_user):
        result = notes_mod.list_notes({}, _make_context())
        assert result["total"] == 0
        assert result["notes"] == []

    def test_returns_notes(self, notes_mod, web_user, tmp_store):
        tmp_store.create_note(web_user["id"], "Note 1", "content 1")
        tmp_store.create_note(web_user["id"], "Note 2", "content 2")
        result = notes_mod.list_notes({}, _make_context())
        assert result["total"] == 2

    def test_filter_by_tag(self, notes_mod, web_user, tmp_store):
        n = tmp_store.create_note(web_user["id"], "Tagged", "c")
        tmp_store.set_note_tags(n["id"], web_user["id"], ["work"])
        tmp_store.create_note(web_user["id"], "Untagged", "c")
        result = notes_mod.list_notes({"tag": "work"}, _make_context())
        assert result["total"] == 1
        assert result["notes"][0]["title"] == "Tagged"

    def test_limit(self, notes_mod, web_user, tmp_store):
        for i in range(5):
            tmp_store.create_note(web_user["id"], f"N{i}", "c")
        result = notes_mod.list_notes({"limit": 2}, _make_context())
        assert result["total"] == 2


# ── read_note ───────────────────────────────────────────────

class TestReadNote:
    def test_read_existing(self, notes_mod, web_user, tmp_store):
        n = tmp_store.create_note(web_user["id"], "My Note", "# Hello")
        result = notes_mod.read_note({"note_id": n["id"]}, _make_context())
        assert result["title"] == "My Note"
        assert result["content"] == "# Hello"

    def test_read_not_found(self, notes_mod, web_user):
        result = notes_mod.read_note({"note_id": "nonexistent"}, _make_context())
        assert result["error"] == "note_not_found"

    def test_missing_note_id(self, notes_mod, web_user):
        with pytest.raises(ValueError, match="note_id"):
            notes_mod.read_note({}, _make_context())


# ── create_note ─────────────────────────────────────────────

class TestCreateNote:
    def test_create_basic(self, notes_mod, web_user):
        result = notes_mod.create_note({"title": "New"}, _make_context())
        assert result["status"] == "created"
        assert result["note"]["title"] == "New"

    def test_create_with_content_and_tags(self, notes_mod, web_user):
        result = notes_mod.create_note(
            {"title": "T", "content": "Body", "tags": ["a", "b"]},
            _make_context(),
        )
        assert result["note"]["tags"] == ["a", "b"]
        assert result["note"]["content"] == "Body"

    def test_create_missing_title(self, notes_mod, web_user):
        with pytest.raises(ValueError, match="title"):
            notes_mod.create_note({"content": "x"}, _make_context())


# ── edit_note ───────────────────────────────────────────────

class TestEditNote:
    def test_edit_title(self, notes_mod, web_user, tmp_store):
        n = tmp_store.create_note(web_user["id"], "Old", "c")
        result = notes_mod.edit_note(
            {"note_id": n["id"], "title": "New Title"}, _make_context()
        )
        assert result["status"] == "updated"
        assert result["note"]["title"] == "New Title"

    def test_edit_content(self, notes_mod, web_user, tmp_store):
        n = tmp_store.create_note(web_user["id"], "T", "old content")
        result = notes_mod.edit_note(
            {"note_id": n["id"], "content": "new content"}, _make_context()
        )
        assert result["note"]["content"] == "new content"

    def test_edit_tags(self, notes_mod, web_user, tmp_store):
        n = tmp_store.create_note(web_user["id"], "T", "c")
        result = notes_mod.edit_note(
            {"note_id": n["id"], "tags": ["x", "y"]}, _make_context()
        )
        assert set(result["note"]["tags"]) == {"x", "y"}

    def test_edit_not_found(self, notes_mod, web_user):
        result = notes_mod.edit_note(
            {"note_id": "nope", "title": "X"}, _make_context()
        )
        assert result["error"] == "note_not_found"

    def test_edit_no_fields(self, notes_mod, web_user, tmp_store):
        n = tmp_store.create_note(web_user["id"], "T", "c")
        with pytest.raises(ValueError, match="At least one"):
            notes_mod.edit_note({"note_id": n["id"]}, _make_context())

    def test_edit_missing_note_id(self, notes_mod, web_user):
        with pytest.raises(ValueError, match="note_id"):
            notes_mod.edit_note({"title": "X"}, _make_context())


# ── delete_note ─────────────────────────────────────────────

class TestDeleteNote:
    def test_delete_existing(self, notes_mod, web_user, tmp_store):
        n = tmp_store.create_note(web_user["id"], "Del", "c")
        result = notes_mod.delete_note({"note_id": n["id"]}, _make_context())
        assert result["status"] == "deleted"
        # Confirm gone
        assert tmp_store.get_note(n["id"], web_user["id"]) is None

    def test_delete_not_found(self, notes_mod, web_user):
        result = notes_mod.delete_note({"note_id": "nope"}, _make_context())
        assert result["error"] == "note_not_found"

    def test_delete_missing_note_id(self, notes_mod, web_user):
        with pytest.raises(ValueError, match="note_id"):
            notes_mod.delete_note({}, _make_context())


# ── search_notes ────────────────────────────────────────────

class TestSearchNotes:
    def test_search_by_title(self, notes_mod, web_user, tmp_store):
        tmp_store.create_note(web_user["id"], "Python Tips", "some content")
        tmp_store.create_note(web_user["id"], "Java Guide", "other content")
        result = notes_mod.search_notes({"query": "Python"}, _make_context())
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Python Tips"

    def test_search_by_content(self, notes_mod, web_user, tmp_store):
        tmp_store.create_note(web_user["id"], "T1", "kubernetes deployment guide")
        tmp_store.create_note(web_user["id"], "T2", "cooking recipe")
        result = notes_mod.search_notes({"query": "kubernetes"}, _make_context())
        assert result["total"] == 1

    def test_search_no_results(self, notes_mod, web_user, tmp_store):
        tmp_store.create_note(web_user["id"], "T", "c")
        result = notes_mod.search_notes({"query": "zzzznotfound"}, _make_context())
        assert result["total"] == 0

    def test_search_missing_query(self, notes_mod, web_user):
        with pytest.raises(ValueError, match="query"):
            notes_mod.search_notes({}, _make_context())

    def test_search_returns_snippets(self, notes_mod, web_user, tmp_store):
        long_content = "A" * 500
        tmp_store.create_note(web_user["id"], "Long", long_content)
        result = notes_mod.search_notes({"query": "AAA"}, _make_context())
        assert result["total"] == 1
        assert len(result["results"][0]["snippet"]) <= 300


# ── user isolation ──────────────────────────────────────────

class TestUserIsolation:
    def test_cannot_read_other_user_notes(self, notes_mod, web_user, tmp_store):
        bob = tmp_store.create_user("bob", "pass456")
        n = tmp_store.create_note(bob["id"], "Bob's note", "secret")
        result = notes_mod.read_note({"note_id": n["id"]}, _make_context("web:alice"))
        assert result["error"] == "note_not_found"

    def test_cannot_search_other_user_notes(self, notes_mod, web_user, tmp_store):
        bob = tmp_store.create_user("bob", "pass456")
        tmp_store.create_note(bob["id"], "Bob secret", "top secret data")
        result = notes_mod.search_notes({"query": "secret"}, _make_context("web:alice"))
        assert result["total"] == 0


# ── _resolve_user_id ────────────────────────────────────────

class TestResolveUserId:
    def test_resolves_web_prefix(self, notes_mod, web_user):
        uid = notes_mod._resolve_user_id("web:alice")
        assert uid == web_user["id"]

    def test_caches_resolution(self, notes_mod, web_user):
        notes_mod._resolve_user_id("web:alice")
        assert "web:alice" in notes_mod._uid_cache

    def test_fallback_unknown_user(self, notes_mod):
        uid = notes_mod._resolve_user_id("web:unknown_user_xyz")
        assert uid == "web:unknown_user_xyz"

    def test_non_web_prefix_passthrough(self, notes_mod):
        uid = notes_mod._resolve_user_id("telegram:12345")
        assert uid == "telegram:12345"
