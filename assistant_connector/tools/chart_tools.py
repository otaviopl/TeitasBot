"""Chart generation tool for the personal assistant.

Provides the ``generate_nutrition_chart`` tool that the LLM calls after collecting
nutrition and/or exercise data. The generated image path is registered in
``context.response_attachments`` so the Telegram bot (or other channel) can
deliver it to the user alongside the text response.
"""
from __future__ import annotations

from typing import Any

from assistant_connector.charts.chart_cleaner import clean_old_charts
from assistant_connector.charts.chart_generator import generate_nutrition_chart as _generate
from assistant_connector.models import ToolExecutionContext


def generate_nutrition_chart(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    """Generate a visual nutrition/exercise summary chart and attach it to the response.

    The chart PNG is saved locally and its path is pushed to
    ``context.response_attachments`` so the delivery layer (e.g. Telegram bot)
    can send it as a photo alongside the text reply.

    Before generating, stale chart files (older than 7 days) are cleaned up
    lazily to keep disk usage bounded.
    """
    title = str(arguments.get("title", "") or "").strip()

    def _float_or_none(key: str) -> float | None:
        raw = arguments.get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    calories_consumed = _float_or_none("calories_consumed")
    calories_goal = _float_or_none("calories_goal")
    protein_g = _float_or_none("protein_g")
    protein_goal_g = _float_or_none("protein_goal_g")
    carbs_g = _float_or_none("carbs_g")
    carbs_goal_g = _float_or_none("carbs_goal_g")
    fat_g = _float_or_none("fat_g")
    fat_goal_g = _float_or_none("fat_goal_g")
    calories_burned = _float_or_none("calories_burned")

    has_any_data = any(
        v is not None
        for v in (calories_consumed, protein_g, carbs_g, fat_g, calories_burned)
    )
    if not has_any_data:
        return {
            "success": False,
            "error": "no_data_provided",
            "message": "No nutrition or exercise values were supplied. Provide at least one numeric value.",
        }

    # Lazy cleanup: remove charts older than 7 days before creating a new one.
    try:
        clean_old_charts(max_age_days=7)
    except Exception as exc:  # noqa: BLE001
        logger = getattr(context.project_logger, "warning", None)
        if callable(logger):
            logger("Chart cleanup failed (non-fatal): %s", exc)

    try:
        chart_path = _generate(
            title=title,
            calories_consumed=calories_consumed,
            calories_goal=calories_goal,
            protein_g=protein_g,
            protein_goal_g=protein_goal_g,
            carbs_g=carbs_g,
            carbs_goal_g=carbs_goal_g,
            fat_g=fat_g,
            fat_goal_g=fat_goal_g,
            calories_burned=calories_burned,
        )
    except Exception as exc:
        logger = getattr(context.project_logger, "exception", None)
        if callable(logger):
            logger("Failed to generate nutrition chart: %s", exc)
        return {
            "success": False,
            "error": "chart_generation_failed",
            "message": str(exc),
        }

    if context.response_attachments is not None:
        context.response_attachments.add_image(chart_path)

    log_info = getattr(context.project_logger, "info", None)
    if callable(log_info):
        log_info("Nutrition chart generated: %s", chart_path)

    return {
        "success": True,
        "chart_path": chart_path,
        "message": "Chart generated successfully and will be sent to the user.",
    }
