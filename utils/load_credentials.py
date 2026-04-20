import os
from typing import Optional


def _get_required_env(env_key, project_logger):
    env_value = os.getenv(env_key)
    if env_value:
        return env_value

    error_message = f"Missing required environment variable: {env_key}"
    project_logger.error(error_message)
    raise ValueError(error_message)


def _resolve(key: str, env_key: str, user_id: Optional[str], store) -> Optional[str]:
    """Resolve a credential: store first (if provided), then env var fallback."""
    if store is not None and user_id is not None:
        value = store.get_credential(user_id, key, use_env_fallback=False)
        if value:
            return value
    return os.getenv(env_key) or None


def load_email_config(project_logger, user_id: Optional[str] = None, store=None):
    project_logger.debug("Getting EMAIL credentials...")

    email_from = _resolve("email_from", "EMAIL_FROM", user_id, store)
    email_to = _resolve("email_to", "EMAIL_TO", user_id, store)
    display_name = _resolve("display_name", "DISPLAY_NAME", user_id, store)

    if not email_from or not email_to or not display_name:
        project_logger.debug("Email config not fully configured for user %s.", user_id)
        return None

    project_logger.debug("Finished getting EMAIL credentials.")
    return {"email_from": email_from, "email_to": email_to, "display_name": display_name}
