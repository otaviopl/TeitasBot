import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from utils import create_logger, load_credentials, nice_message_collector


class _LoggerStub:
    def __init__(self):
        self.errors = []
        self.debugs = []
        self.warnings = []

    def error(self, message, *args):
        self.errors.append(message % args if args else message)

    def debug(self, message, *args):
        self.debugs.append(message % args if args else message)

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)

    def info(self, *_args, **_kwargs):
        return None


class TestUtilsHelpers(unittest.TestCase):
    def test_create_logger_creates_and_reuses_handlers(self):
        from logging.handlers import RotatingFileHandler

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {"LOG_PATH": temp_dir}, clear=False):
            logger = create_logger.create_logger()
            self.assertEqual(logger.name, "personal_notion_integration")
            self.assertGreaterEqual(len(logger.handlers), 2)
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "log_file.txt")))

            file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
            self.assertEqual(len(file_handlers), 1, "Expected one RotatingFileHandler")
            self.assertGreater(file_handlers[0].maxBytes, 0)
            self.assertGreater(file_handlers[0].backupCount, 0)

            handler_count = len(logger.handlers)
            logger_again = create_logger.create_logger()
            self.assertIs(logger_again, logger)
            self.assertEqual(len(logger_again.handlers), handler_count)

        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_get_required_env_raises_when_missing(self):
        logger = _LoggerStub()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                load_credentials._get_required_env("MISSING_ENV", logger)
        self.assertIn("Missing required environment variable: MISSING_ENV", logger.errors[0])

    def test_load_notion_credentials_prefers_store_then_env(self):
        logger = _LoggerStub()
        store = MagicMock()
        store.get_credential.side_effect = ["db-from-store", "api-from-store"]
        with patch.dict(
            os.environ,
            {"NOTION_DATABASE_ID": "db-env", "NOTION_API_KEY": "api-env"},
            clear=False,
        ):
            creds = load_credentials.load_notion_credentials(logger, user_id="u1", store=store)
        self.assertEqual(creds["database_id"], "db-from-store")
        self.assertEqual(creds["api_key"], "api-from-store")

    def test_load_email_config_returns_none_when_incomplete(self):
        logger = _LoggerStub()
        with patch.dict(os.environ, {"EMAIL_FROM": "from@example.com", "EMAIL_TO": ""}, clear=False):
            cfg = load_credentials.load_email_config(logger)
        self.assertIsNone(cfg)

    def test_load_notion_db_id_uses_env_fallback(self):
        logger = _LoggerStub()
        with patch.dict(os.environ, {"NOTION_NOTES_DB_ID": "notes-db"}, clear=False):
            db_id = load_credentials.load_notion_db_id(
                key="notion_notes_db_id",
                env_key="NOTION_NOTES_DB_ID",
                project_logger=logger,
                user_id=None,
                store=None,
            )
        self.assertEqual(db_id, "notes-db")

    @patch("utils.nice_message_collector.random.choice")
    @patch("utils.nice_message_collector.requests.get")
    def test_get_motivational_message_uses_remote_payload(self, mock_get, mock_choice):
        logger = _LoggerStub()
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"text": "A"}, {"text": "B"}]
        mock_get.return_value = response
        mock_choice.return_value = {"text": "B"}

        payload = nice_message_collector.get_motivational_message(logger)
        self.assertEqual(payload["text"], "B")

    @patch("utils.nice_message_collector.requests.get")
    def test_get_motivational_message_returns_fallback_on_request_error(self, mock_get):
        logger = _LoggerStub()
        mock_get.side_effect = requests.RequestException("network error")

        payload = nice_message_collector.get_motivational_message(logger)
        self.assertIn("Keep moving forward", payload["text"])
        self.assertTrue(logger.warnings)


if __name__ == "__main__":
    unittest.main()
