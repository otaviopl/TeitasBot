import unittest
from unittest.mock import patch, MagicMock

from task_summary_flow import collect_tasks_and_summary


class TestTaskSummaryFlow(unittest.TestCase):
    @patch("task_summary_flow.llm_api.call_openai_assistant")
    @patch("task_summary_flow._health_store")
    def test_collect_tasks_and_summary_uses_connectors(self, mock_store, mock_openai):
        logger = _MockLogger()
        tasks = [{"name": "Task 1", "deadline": "2026-01-01", "project": "X", "tags": ["FAST"]}]
        mock_store.list_tasks.return_value = tasks
        mock_openai.return_value = "Summary"

        returned_tasks, summary = collect_tasks_and_summary(logger, n_days=2)

        self.assertEqual(returned_tasks, tasks)
        self.assertEqual(summary, "Summary")
        mock_store.list_tasks.assert_called_once()
        mock_openai.assert_called_once_with(tasks, logger)


class _MockLogger:
    def info(self, *_args, **_kwargs):
        return None
