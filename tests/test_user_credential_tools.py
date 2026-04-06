"""Tests for manage_user_credentials tool."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from cryptography.fernet import Fernet

TEST_KEY = Fernet.generate_key().decode()


def _make_context(user_id="u1", store=None):
    from assistant_connector.models import AgentDefinition, ToolExecutionContext

    agent = AgentDefinition(
        agent_id="personal_assistant",
        description="desc",
        model="model",
        system_prompt="prompt",
        tools=[],
    )
    return ToolExecutionContext(
        user_id=user_id,
        session_id="sess-test",
        channel_id="channel",
        guild_id="guild",
        project_logger=None,
        agent=agent,
        available_tools=[],
        available_agents=[],
        user_credential_store=store,
    )


class TestManageUserCredentials(unittest.TestCase):
    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmpfile.close()
        self._db_path = self._tmpfile.name
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = TEST_KEY

    def tearDown(self):
        os.unlink(self._db_path)
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)

    def _make_store(self):
        from assistant_connector.user_credential_store import UserCredentialStore

        return UserCredentialStore(db_path=self._db_path)

    # --- set action ---

    def test_set_valid_key(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        result = manage_user_credentials(
            {"action": "set", "key": "email_from", "value": "secret_123"},
            ctx,
        )
        self.assertIn("success", result)
        self.assertEqual(store.get_credential("u1", "email_from"), "secret_123")

    def test_set_invalid_key(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        result = manage_user_credentials(
            {"action": "set", "key": "invalid_unknown_key", "value": "val"},
            ctx,
        )
        self.assertIn("error", result)

    def test_set_missing_value(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        result = manage_user_credentials(
            {"action": "set", "key": "email_from"},
            ctx,
        )
        self.assertIn("error", result)

    # --- list_configured action ---

    def test_list_configured_empty(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        result = manage_user_credentials({"action": "list_configured"}, ctx)
        self.assertIn("configured_keys", result)
        self.assertEqual(result["configured_keys"], [])

    def test_list_configured_after_set(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        manage_user_credentials(
            {"action": "set", "key": "email_from", "value": "val"},
            ctx,
        )
        result = manage_user_credentials({"action": "list_configured"}, ctx)
        self.assertIn("email_from", result["configured_keys"])

    # --- delete action ---

    def test_delete_existing_key(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        manage_user_credentials(
            {"action": "set", "key": "email_from", "value": "x@x.com"},
            ctx,
        )
        result = manage_user_credentials({"action": "delete", "key": "email_from"}, ctx)
        self.assertIn("success", result)
        self.assertIsNone(store.get_credential("u1", "email_from", use_env_fallback=False))

    def test_delete_nonexistent_key(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        result = manage_user_credentials({"action": "delete", "key": "email_from"}, ctx)
        self.assertFalse(result.get("success", True))

    # --- check_integrations action ---

    def test_check_integrations_returns_dict(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        result = manage_user_credentials({"action": "check_integrations"}, ctx)
        self.assertIn("integrations", result)
        self.assertIsInstance(result["integrations"], dict)

    # --- no store ---

    def test_no_store_returns_error(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        ctx = _make_context(store=None)
        result = manage_user_credentials({"action": "list_configured"}, ctx)
        self.assertIn("error", result)

    # --- unknown action ---

    def test_unknown_action_returns_error(self):
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx = _make_context(store=store)
        result = manage_user_credentials({"action": "fly_to_moon"}, ctx)
        self.assertIn("error", result)

    # --- cross-user isolation at the tool layer ---

    def test_tool_user_id_comes_from_context_not_arguments(self):
        """The tool must ignore any user_id-like argument; isolation relies on context.user_id."""
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        # user_a configures a credential
        ctx_a = _make_context(user_id="user_a", store=store)
        manage_user_credentials({"action": "set", "key": "email_from", "value": "secret_a"}, ctx_a)

        # user_b tries to list keys — even if they somehow pass user_a's id in arguments, the
        # tool must only see user_b's own empty key list.
        ctx_b = _make_context(user_id="user_b", store=store)
        result = manage_user_credentials(
            {"action": "list_configured", "user_id": "user_a"},  # ignored extra arg
            ctx_b,
        )
        self.assertNotIn("email_from", result.get("configured_keys", []))

    def test_tool_contexts_are_isolated(self):
        """Setting a credential via one user context must not be visible via another."""
        from assistant_connector.tools.user_credential_tools import manage_user_credentials

        store = self._make_store()
        ctx_a = _make_context(user_id="user_a", store=store)
        ctx_b = _make_context(user_id="user_b", store=store)

        manage_user_credentials({"action": "set", "key": "email_from", "value": "a@test.com"}, ctx_a)
        manage_user_credentials({"action": "set", "key": "email_from", "value": "b@test.com"}, ctx_b)

        # Each user sees only their own value
        self.assertEqual(store.get_credential("user_a", "email_from", use_env_fallback=False), "a@test.com")
        self.assertEqual(store.get_credential("user_b", "email_from", use_env_fallback=False), "b@test.com")
