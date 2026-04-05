import os
import sqlite3
import tempfile
import unittest

from assistant_connector.file_store import FileStore, ACCEPTED_EXTENSIONS, _safe_filename


def _make_store(tmp_dir: str) -> FileStore:
    db_path = os.path.join(tmp_dir, "test.sqlite3")
    files_dir = os.path.join(tmp_dir, "files")
    return FileStore(db_path=db_path, files_dir=files_dir)


class TestFileStoreInit(unittest.TestCase):
    def test_creates_table_on_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            conn = sqlite3.connect(os.path.join(tmp, "test.sqlite3"))
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            self.assertIn("user_files", tables)


class TestFileStoreSaveFile(unittest.TestCase):
    def test_save_valid_txt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = store.save_file(
                user_id="user1",
                original_name="notes.txt",
                file_bytes=b"hello world",
                mime_type="text/plain",
                context_description="my notes",
            )
            self.assertIn("file_id", result)
            self.assertEqual(result["original_name"], "notes.txt")
            self.assertEqual(result["file_size"], 11)
            self.assertEqual(result["context_description"], "my notes")

    def test_save_creates_file_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = store.save_file(
                user_id="user1",
                original_name="data.csv",
                file_bytes=b"a,b,c\n1,2,3",
            )
            file_path = store.resolve_file_path(user_id="user1", file_id=result["file_id"])
            self.assertIsNotNone(file_path)
            self.assertTrue(os.path.isfile(file_path))
            with open(file_path, "rb") as f:
                self.assertEqual(f.read(), b"a,b,c\n1,2,3")

    def test_rejects_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            with self.assertRaises(ValueError) as ctx:
                store.save_file(
                    user_id="user1",
                    original_name="virus.exe",
                    file_bytes=b"bad",
                )
            self.assertIn(".exe", str(ctx.exception))

    def test_rejects_file_exceeding_max_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite3")
            files_dir = os.path.join(tmp, "files")
            store = FileStore(db_path=db_path, files_dir=files_dir, max_file_size_bytes=100)
            with self.assertRaises(ValueError) as ctx:
                store.save_file(
                    user_id="user1",
                    original_name="big.txt",
                    file_bytes=b"x" * 200,
                )
            self.assertIn("tamanho máximo", str(ctx.exception))

    def test_accepts_file_at_max_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite3")
            files_dir = os.path.join(tmp, "files")
            store = FileStore(db_path=db_path, files_dir=files_dir, max_file_size_bytes=100)
            result = store.save_file(
                user_id="user1",
                original_name="ok.txt",
                file_bytes=b"x" * 100,
            )
            self.assertEqual(result["file_size"], 100)

    def test_different_users_get_separate_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.save_file(user_id="alice", original_name="a.txt", file_bytes=b"a")
            store.save_file(user_id="bob", original_name="b.txt", file_bytes=b"b")
            self.assertTrue(os.path.isdir(os.path.join(tmp, "files", "alice")))
            self.assertTrue(os.path.isdir(os.path.join(tmp, "files", "bob")))


class TestFileStoreGetFile(unittest.TestCase):
    def test_get_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(user_id="u1", original_name="f.md", file_bytes=b"# hi")
            record = store.get_file(user_id="u1", file_id=saved["file_id"])
            self.assertIsNotNone(record)
            self.assertEqual(record["original_name"], "f.md")

    def test_get_returns_none_for_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self.assertIsNone(store.get_file(user_id="u1", file_id="nonexistent"))

    def test_get_does_not_cross_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(user_id="alice", original_name="secret.txt", file_bytes=b"s")
            self.assertIsNone(store.get_file(user_id="bob", file_id=saved["file_id"]))


class TestFileStoreListFiles(unittest.TestCase):
    def test_list_returns_user_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.save_file(user_id="u1", original_name="a.txt", file_bytes=b"a")
            store.save_file(user_id="u1", original_name="b.csv", file_bytes=b"b")
            store.save_file(user_id="u2", original_name="c.pdf", file_bytes=b"c")
            files = store.list_files(user_id="u1")
            self.assertEqual(len(files), 2)
            names = {f["original_name"] for f in files}
            self.assertIn("a.txt", names)
            self.assertIn("b.csv", names)

    def test_list_returns_empty_for_new_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self.assertEqual(store.list_files(user_id="nobody"), [])


class TestFileStoreDeleteFile(unittest.TestCase):
    def test_delete_removes_record_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(user_id="u1", original_name="del.txt", file_bytes=b"bye")
            file_path = store.resolve_file_path(user_id="u1", file_id=saved["file_id"])
            self.assertTrue(os.path.isfile(file_path))

            result = store.delete_file(user_id="u1", file_id=saved["file_id"])
            self.assertTrue(result)
            self.assertIsNone(store.get_file(user_id="u1", file_id=saved["file_id"]))
            self.assertFalse(os.path.isfile(file_path))

    def test_delete_returns_false_for_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self.assertFalse(store.delete_file(user_id="u1", file_id="ghost"))

    def test_delete_does_not_cross_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(user_id="alice", original_name="mine.txt", file_bytes=b"x")
            result = store.delete_file(user_id="bob", file_id=saved["file_id"])
            self.assertFalse(result)
            self.assertIsNotNone(store.get_file(user_id="alice", file_id=saved["file_id"]))

    def test_delete_still_removes_record_when_disk_file_already_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(user_id="u1", original_name="gone.txt", file_bytes=b"data")
            file_path = store.resolve_file_path(user_id="u1", file_id=saved["file_id"])
            os.remove(file_path)

            result = store.delete_file(user_id="u1", file_id=saved["file_id"])
            self.assertTrue(result)
            self.assertIsNone(store.get_file(user_id="u1", file_id=saved["file_id"]))


class TestFileStorePathTraversal(unittest.TestCase):
    def test_resolve_rejects_traversal_in_stored_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            saved = store.save_file(
                user_id="user1",
                original_name="legit.txt",
                file_bytes=b"safe content",
            )
            # Tamper with the SQLite record to simulate path traversal
            conn = sqlite3.connect(os.path.join(tmp, "test.sqlite3"))
            conn.execute(
                "UPDATE user_files SET stored_name = ? WHERE file_id = ?",
                ("../../etc/passwd", saved["file_id"]),
            )
            conn.commit()
            conn.close()

            result = store.resolve_file_path(user_id="user1", file_id=saved["file_id"])
            self.assertIsNone(result)


class TestSafeFilename(unittest.TestCase):
    def test_preserves_safe_chars(self):
        self.assertEqual(_safe_filename("my-file_1.pdf"), "my-file_1.pdf")

    def test_replaces_spaces(self):
        result = _safe_filename("my file.txt")
        self.assertNotIn(" ", result)

    def test_handles_path_traversal(self):
        result = _safe_filename("../evil/file.txt")
        self.assertNotIn("/", result)
        self.assertNotIn("..", result)
