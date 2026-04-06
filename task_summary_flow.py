import os

from assistant_connector.health_store import HealthStore
from openai_connector import llm_api


# Day offset for task filtering (0 = today).
DAYS_TO_CONSIDER = 0

_default_db_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "assistant_memory.sqlite3")
)
_health_store = HealthStore(db_path=os.getenv("ASSISTANT_MEMORY_PATH", _default_db_path))

# System-level user ID for Telegram bot context (no web prefix).
_SYSTEM_USER_ID = os.getenv("TASK_SUMMARY_USER_ID", "system")


def collect_tasks_and_summary(project_logger, n_days=DAYS_TO_CONSIDER, user_id=None):
    effective_user_id = user_id or _SYSTEM_USER_ID
    all_tasks = _health_store.list_tasks(user_id=effective_user_id, n_days=n_days, limit=50)
    chatgpt_answer = llm_api.call_openai_assistant(all_tasks, project_logger)
    return all_tasks, chatgpt_answer
