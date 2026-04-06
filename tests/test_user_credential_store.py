"""Tests for UserCredentialStore."""

import os
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

TEST_KEY = Fernet.generate_key().decode()


def _make_store(db_path):
    """Create a UserCredentialStore backed by a temp DB file."""
    os.environ["CREDENTIAL_ENCRYPTION_KEY"] = TEST_KEY
    from assistant_connector.user_credential_store import UserCredentialStore

    return UserCredentialStore(db_path=db_path)


class TestUserCredentialStore(unittest.TestCase):
    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmpfile.close()
        self._db_path = self._tmpfile.name
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = TEST_KEY

    def tearDown(self):
        os.unlink(self._db_path)
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)

    # --- basic set / get roundtrip ---

    def test_set_and_get_credential(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("u1", "email_from", "secret_abc")
        self.assertEqual(store.get_credential("u1", "email_from"), "secret_abc")

    def test_values_are_encrypted_at_rest(self):
        """Raw DB bytes should NOT contain the plaintext value."""
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("u2", "email_from", "plaintext_secret")

        with open(self._db_path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"plaintext_secret", raw)

    def test_get_returns_none_when_missing(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        # Disable env fallback to avoid picking up real env vars
        result = store.get_credential("u3", "email_from", use_env_fallback=False)
        self.assertIsNone(result)

    def test_env_fallback_when_not_in_db(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        with patch.dict(os.environ, {"EMAIL_FROM": "env_fallback_value"}):
            result = store.get_credential("u4", "email_from", use_env_fallback=True)
        self.assertEqual(result, "env_fallback_value")

    def test_db_value_takes_priority_over_env(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("u5", "email_from", "db_value")
        with patch.dict(os.environ, {"EMAIL_FROM": "env_value"}):
            result = store.get_credential("u5", "email_from", use_env_fallback=True)
        self.assertEqual(result, "db_value")

    def test_no_env_fallback_when_disabled(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        with patch.dict(os.environ, {"EMAIL_FROM": "env_value"}):
            result = store.get_credential("u6", "email_from", use_env_fallback=False)
        self.assertIsNone(result)

    # --- update ---

    def test_update_existing_credential(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("u7", "email_from", "old@mail.com")
        store.set_credential("u7", "email_from", "new@mail.com")
        self.assertEqual(store.get_credential("u7", "email_from"), "new@mail.com")

    # --- delete ---

    def test_delete_existing_returns_true(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("u8", "email_from", "val")
        result = store.delete_credential("u8", "email_from")
        self.assertTrue(result)
        self.assertIsNone(store.get_credential("u8", "email_from", use_env_fallback=False))

    def test_delete_nonexistent_returns_false(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        result = store.delete_credential("u9", "email_from")
        self.assertFalse(result)

    # --- list_configured_keys ---

    def test_list_configured_keys(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("u10", "email_from", "v1")
        store.set_credential("u10", "email_to", "v2")
        keys = store.list_configured_keys("u10")
        self.assertIn("email_from", keys)
        self.assertIn("email_to", keys)
        self.assertEqual(len(keys), 2)

    def test_list_configured_keys_isolated_per_user(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("userA", "email_from", "a")
        store.set_credential("userB", "email_from", "b")
        self.assertEqual(store.list_configured_keys("userA"), ["email_from"])
        self.assertEqual(store.list_configured_keys("userB"), ["email_from"])

    # --- check_integrations ---

    def test_check_integrations_email_available(self):
        from assistant_connector.user_credential_store import UserCredentialStore, _INTEGRATION_REQUIREMENTS

        store = UserCredentialStore(db_path=self._db_path)
        email_keys = _INTEGRATION_REQUIREMENTS.get("Email", [])
        for key in email_keys:
            store.set_credential("u11", key, "dummy")
        result = store.check_integrations("u11")
        self.assertTrue(result["Email"])

    def test_check_integrations_email_unavailable(self):
        from assistant_connector.user_credential_store import UserCredentialStore, _INTEGRATION_REQUIREMENTS

        store = UserCredentialStore(db_path=self._db_path)
        email_keys = _INTEGRATION_REQUIREMENTS.get("Email", [])
        from assistant_connector.user_credential_store import _ENV_FALLBACK

        env_overrides = {_ENV_FALLBACK[k]: "" for k in email_keys if k in _ENV_FALLBACK}
        with patch.dict(os.environ, env_overrides):
            result = store.check_integrations("u12")
        self.assertFalse(result.get("Email", True))

    # --- missing encryption key ---

    def test_missing_encryption_key_raises(self):
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        # Must re-import to force re-evaluation; use a fresh env
        import importlib
        import assistant_connector.user_credential_store as ucs_module

        with self.assertRaises((ValueError, KeyError, Exception)):
            importlib.reload(ucs_module)
            ucs_module.UserCredentialStore(db_path=self._db_path)

        # Restore for other tests
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = TEST_KEY

    # --- cross-user isolation ---

    def test_user_cannot_read_another_users_credential(self):
        """User B must not be able to read a credential set by User A."""
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("user_a", "email_from", "secret_for_a")

        result = store.get_credential("user_b", "email_from", use_env_fallback=False)
        self.assertIsNone(result)

    def test_user_cannot_overwrite_another_users_credential(self):
        """User B writing a key must not affect User A's copy of that key."""
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("user_a", "email_from", "a@example.com")
        store.set_credential("user_b", "email_from", "b@example.com")

        self.assertEqual(store.get_credential("user_a", "email_from", use_env_fallback=False), "a@example.com")
        self.assertEqual(store.get_credential("user_b", "email_from", use_env_fallback=False), "b@example.com")

    def test_user_cannot_delete_another_users_credential(self):
        """User B deleting a key must not remove User A's copy."""
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("user_a", "email_from", "value_a")

        deleted = store.delete_credential("user_b", "email_from")
        self.assertFalse(deleted)
        self.assertEqual(
            store.get_credential("user_a", "email_from", use_env_fallback=False),
            "value_a",
        )

    def test_list_configured_keys_does_not_include_other_users_keys(self):
        """list_configured_keys must only return the requesting user's keys."""
        from assistant_connector.user_credential_store import UserCredentialStore

        store = UserCredentialStore(db_path=self._db_path)
        store.set_credential("user_a", "email_from", "a1")
        store.set_credential("user_a", "email_to", "a2")
        store.set_credential("user_b", "display_name", "b1")

        keys_a = store.list_configured_keys("user_a")
        keys_b = store.list_configured_keys("user_b")

        self.assertNotIn("display_name", keys_a)
        self.assertNotIn("email_from", keys_b)
        self.assertNotIn("email_to", keys_b)

    def test_check_integrations_isolated_per_user(self):
        """Integration status for User A must not reflect User B's credentials."""
        from assistant_connector.user_credential_store import UserCredentialStore, _INTEGRATION_REQUIREMENTS

        store = UserCredentialStore(db_path=self._db_path)
        # Fully configure Email for user_a
        for key in _INTEGRATION_REQUIREMENTS["Email"]:
            store.set_credential("user_a", key, "dummy")

        with patch.dict(os.environ, {"EMAIL_FROM": "", "EMAIL_TO": ""}):
            result_b = store.check_integrations("user_b")
        # user_b must not inherit user_a's configuration
        self.assertFalse(result_b.get("Email", True))
