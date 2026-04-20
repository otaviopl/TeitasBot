import os
import tempfile
import unittest
from unittest.mock import MagicMock

from assistant_connector.file_store import FileStore
from assistant_connector.tools import file_tools


def _make_store(tmp_dir: str) -> FileStore:
    db_path = os.path.join(tmp_dir, "test.sqlite3")
    files_dir = os.path.join(tmp_dir, "files")
    return FileStore(db_path=db_path, files_dir=files_dir)


def _build_context(file_store: FileStore | None = None, user_id: str = "user1") -> MagicMock:
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.file_store = file_store
    return ctx


class TestListUserFiles(unittest.TestCase):
    def test_returns_empty_list_for_new_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = file_tools.list_user_files({}, _build_context(store))
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["files"], [])

    def test_returns_uploaded_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.save_file(user_id="user1", original_name="report.pdf", file_bytes=b"%PDF test")
            store.save_file(user_id="user1", original_name="notes.txt", file_bytes=b"hello")
            result = file_tools.list_user_files({}, _build_context(store))
            self.assertEqual(result["count"], 2)
            names = {f["name"] for f in result["files"]}
            self.assertIn("report.pdf", names)
            self.assertIn("notes.txt", names)

    def test_raises_when_file_store_not_configured(self):
        ctx = _build_context(file_store=None)
        with self.assertRaises(RuntimeError):
            file_tools.list_user_files({}, ctx)


class TestReadFileContent(unittest.TestCase):
    def test_missing_file_id_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = file_tools.read_file_content({}, _build_context(store))
            self.assertEqual(result["error"], "missing_file_id")

    def test_unknown_file_id_returns_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = file_tools.read_file_content(
                {"file_id": "nonexistent"}, _build_context(store)
            )
            self.assertEqual(result["error"], "file_not_found")

    def test_reads_txt_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(
                user_id="user1",
                original_name="readme.txt",
                file_bytes="Hello, world!".encode("utf-8"),
            )
            result = file_tools.read_file_content(
                {"file_id": saved["file_id"]}, _build_context(store)
            )
            self.assertIn("Hello, world!", result["content"])
            self.assertFalse(result["truncated"])

    def test_reads_csv_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            csv_bytes = "name,age\nAlice,30\nBob,25".encode("utf-8")
            saved = store.save_file(
                user_id="user1", original_name="data.csv", file_bytes=csv_bytes
            )
            result = file_tools.read_file_content(
                {"file_id": saved["file_id"]}, _build_context(store)
            )
            self.assertIn("Alice", result["content"])

    def test_truncation_respects_max_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            big_content = "A" * 200
            saved = store.save_file(
                user_id="user1",
                original_name="big.txt",
                file_bytes=big_content.encode("utf-8"),
            )
            result = file_tools.read_file_content(
                {"file_id": saved["file_id"], "max_chars": 50}, _build_context(store)
            )
            self.assertEqual(len(result["content"]), 50)
            self.assertTrue(result["truncated"])

    def test_does_not_cross_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(
                user_id="alice", original_name="secret.txt", file_bytes=b"secret"
            )
            ctx = _build_context(store, user_id="bob")
            result = file_tools.read_file_content({"file_id": saved["file_id"]}, ctx)
            self.assertEqual(result["error"], "file_not_found")


class TestDeleteUserFile(unittest.TestCase):
    def test_missing_file_id_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = file_tools.delete_user_file({}, _build_context(store))
            self.assertEqual(result["error"], "missing_file_id")

    def test_unknown_file_id_returns_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = file_tools.delete_user_file(
                {"file_id": "ghost"}, _build_context(store)
            )
            self.assertEqual(result["error"], "file_not_found")

    def test_deletes_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(
                user_id="user1", original_name="bye.txt", file_bytes=b"bye"
            )
            result = file_tools.delete_user_file(
                {"file_id": saved["file_id"]}, _build_context(store)
            )
            self.assertEqual(result["status"], "deleted")
            self.assertIsNone(store.get_file(user_id="user1", file_id=saved["file_id"]))

    def test_does_not_cross_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(
                user_id="alice", original_name="mine.txt", file_bytes=b"x"
            )
            ctx = _build_context(store, user_id="bob")
            result = file_tools.delete_user_file({"file_id": saved["file_id"]}, ctx)
            self.assertEqual(result["error"], "file_not_found")
            self.assertIsNotNone(store.get_file(user_id="alice", file_id=saved["file_id"]))
