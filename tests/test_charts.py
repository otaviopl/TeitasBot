"""Tests for the chart generation feature.

Covers:
- chart_generator.generate_nutrition_chart: PNG file creation, various data combinations
- chart_cleaner.clean_old_charts: age-based file deletion, edge cases
- chart_tools.generate_nutrition_chart: tool handler, ResponseAttachments integration
"""
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from assistant_connector.charts.chart_cleaner import clean_old_charts
from assistant_connector.charts.chart_generator import generate_nutrition_chart
from assistant_connector.models import AgentDefinition, ChatResponse, ResponseAttachments, ToolExecutionContext
from assistant_connector.tools.chart_tools import generate_nutrition_chart as chart_tool


class _FakeLogger:
    def debug(self, *_args, **_kwargs): pass
    def info(self, *_args, **_kwargs): pass
    def warning(self, *_args, **_kwargs): pass
    def error(self, *_args, **_kwargs): pass
    def exception(self, *_args, **_kwargs): pass


def _make_context(response_attachments=None):
    agent = AgentDefinition(
        agent_id="test_agent",
        description="test",
        model="gpt-4",
        system_prompt="",
        tools=[],
    )
    return ToolExecutionContext(
        session_id="sess-1",
        user_id="user-1",
        channel_id="chan-1",
        guild_id=None,
        project_logger=_FakeLogger(),
        agent=agent,
        available_tools=[],
        available_agents=[],
        response_attachments=response_attachments,
    )


# ---------------------------------------------------------------------------
# chart_generator
# ---------------------------------------------------------------------------

class TestChartGenerator(unittest.TestCase):

    def test_generates_png_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                path = generate_nutrition_chart(
                    title="Teste",
                    calories_consumed=1800,
                    calories_goal=2000,
                    protein_g=120,
                    protein_goal_g=150,
                )
                self.assertTrue(os.path.isfile(path), "PNG file should be created")
                self.assertTrue(path.endswith(".png"), "Output should be a PNG file")
                self.assertGreater(os.path.getsize(path), 1000, "PNG should have non-trivial size")

    def test_calories_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                path = generate_nutrition_chart(calories_consumed=1500, calories_goal=2000)
                self.assertTrue(os.path.isfile(path))

    def test_all_macros_with_pie(self):
        """When all three macros are provided, the pie panel should be included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                path = generate_nutrition_chart(
                    title="Full macros",
                    calories_consumed=2100,
                    calories_goal=2000,
                    protein_g=130,
                    protein_goal_g=150,
                    carbs_g=220,
                    carbs_goal_g=250,
                    fat_g=70,
                    fat_goal_g=65,
                    calories_burned=400,
                )
                self.assertTrue(os.path.isfile(path))

    def test_exercise_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                path = generate_nutrition_chart(calories_burned=350)
                self.assertTrue(os.path.isfile(path))

    def test_no_data_creates_fallback_chart(self):
        """Calling with no data should still produce a file (empty/fallback chart)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                path = generate_nutrition_chart()
                self.assertTrue(os.path.isfile(path))

    def test_unique_paths_per_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                p1 = generate_nutrition_chart(calories_consumed=100)
                p2 = generate_nutrition_chart(calories_consumed=200)
                self.assertNotEqual(p1, p2)

    def test_charts_dir_created_if_missing(self):
        with tempfile.TemporaryDirectory() as parent:
            new_dir = os.path.join(parent, "charts_subdir")
            self.assertFalse(os.path.exists(new_dir))
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": new_dir}):
                path = generate_nutrition_chart(calories_consumed=500)
                self.assertTrue(os.path.isdir(new_dir))
                self.assertTrue(os.path.isfile(path))


# ---------------------------------------------------------------------------
# chart_cleaner
# ---------------------------------------------------------------------------

class TestChartCleaner(unittest.TestCase):

    def _make_old_png(self, directory: str, age_days: float, name: str = "old.png") -> str:
        path = os.path.join(directory, name)
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        old_mtime = time.time() - age_days * 86400
        os.utime(path, (old_mtime, old_mtime))
        return path

    def test_deletes_files_older_than_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old = self._make_old_png(tmpdir, age_days=8)
            result = clean_old_charts(max_age_days=7, charts_dir=tmpdir)
            self.assertFalse(os.path.exists(old))
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["errors"], 0)

    def test_keeps_files_younger_than_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recent = self._make_old_png(tmpdir, age_days=3)
            result = clean_old_charts(max_age_days=7, charts_dir=tmpdir)
            self.assertTrue(os.path.exists(recent))
        self.assertEqual(result["deleted"], 0)

    def test_ignores_non_png_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = os.path.join(tmpdir, "old_file.txt")
            with open(txt_path, "w") as f:
                f.write("data")
            old_mtime = time.time() - 10 * 86400
            os.utime(txt_path, (old_mtime, old_mtime))
            result = clean_old_charts(max_age_days=7, charts_dir=tmpdir)
            self.assertTrue(os.path.exists(txt_path))
        self.assertEqual(result["deleted"], 0)

    def test_nonexistent_dir_returns_zeros(self):
        result = clean_old_charts(max_age_days=7, charts_dir="/nonexistent/path/xyz123")
        self.assertEqual(result, {"deleted": 0, "errors": 0})

    def test_max_age_clamped_to_one(self):
        """max_age_days < 1 should be treated as 1 to avoid deleting recent files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            recent = self._make_old_png(tmpdir, age_days=0.5)
            result = clean_old_charts(max_age_days=0, charts_dir=tmpdir)
            # File is 0.5 days old; clamped threshold = 1 day → should NOT be deleted
            self.assertEqual(result["deleted"], 0)
            self.assertTrue(os.path.exists(recent))

    def test_deletes_multiple_old_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(5):
                self._make_old_png(tmpdir, age_days=10, name=f"chart_{i}.png")
            result = clean_old_charts(max_age_days=7, charts_dir=tmpdir)
        self.assertEqual(result["deleted"], 5)


# ---------------------------------------------------------------------------
# chart_tools tool handler
# ---------------------------------------------------------------------------

class TestChartToolHandler(unittest.TestCase):

    def test_returns_error_when_no_data(self):
        ctx = _make_context(response_attachments=ResponseAttachments())
        result = chart_tool({}, ctx)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "no_data_provided")

    def test_generates_chart_and_attaches_image(self):
        attachments = ResponseAttachments()
        ctx = _make_context(response_attachments=attachments)
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                result = chart_tool(
                    {
                        "title": "Teste tool",
                        "calories_consumed": 1800,
                        "calories_goal": 2000,
                        "protein_g": 120,
                        "protein_goal_g": 150,
                    },
                    ctx,
                )
                self.assertTrue(result["success"])
                self.assertEqual(len(attachments.images), 1)
                self.assertTrue(os.path.isfile(attachments.images[0]))

    def test_invalid_numeric_values_gracefully_ignored(self):
        attachments = ResponseAttachments()
        ctx = _make_context(response_attachments=attachments)
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                # "bad" should be coerced to None; calories_consumed=1500 is valid
                result = chart_tool(
                    {"calories_consumed": 1500, "calories_goal": "bad"},
                    ctx,
                )
                self.assertTrue(result["success"])

    def test_does_not_crash_without_response_attachments(self):
        """If context.response_attachments is None, the tool should still succeed."""
        ctx = _make_context(response_attachments=None)
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch.dict(os.environ, {"ASSISTANT_CHARTS_DIR": tmpdir}):
                result = chart_tool({"calories_consumed": 1000}, ctx)
                self.assertTrue(result["success"])


# ---------------------------------------------------------------------------
# ResponseAttachments model
# ---------------------------------------------------------------------------

class TestResponseAttachments(unittest.TestCase):

    def test_add_image_appends_path(self):
        ra = ResponseAttachments()
        ra.add_image("/tmp/chart.png")
        self.assertEqual(ra.images, ["/tmp/chart.png"])

    def test_ignores_empty_paths(self):
        ra = ResponseAttachments()
        ra.add_image("")
        ra.add_image("  ")
        self.assertEqual(ra.images, [])

    def test_bool_false_when_empty(self):
        ra = ResponseAttachments()
        self.assertFalse(bool(ra))

    def test_bool_true_when_has_images(self):
        ra = ResponseAttachments()
        ra.add_image("/tmp/x.png")
        self.assertTrue(bool(ra))


# ---------------------------------------------------------------------------
# ChatResponse model
# ---------------------------------------------------------------------------

class TestChatResponse(unittest.TestCase):

    def test_has_images_false_by_default(self):
        cr = ChatResponse(text="hello")
        self.assertFalse(cr.has_images)
        self.assertEqual(cr.image_paths, [])

    def test_has_images_true_when_paths_provided(self):
        cr = ChatResponse(text="hello", image_paths=["/tmp/chart.png"])
        self.assertTrue(cr.has_images)


import unittest.mock  # ensure mock is available at module level

if __name__ == "__main__":
    unittest.main()
