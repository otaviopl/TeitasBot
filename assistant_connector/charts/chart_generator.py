"""Chart generation utilities using matplotlib.

Generates visual summary charts (PNG) for nutrition and exercise data.
Charts are saved to ASSISTANT_CHARTS_DIR (env var) or a system temp directory.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from typing import Any

_CHARTS_ENV_VAR = "ASSISTANT_CHARTS_DIR"
_CHARTS_SUBDIR = "assistant_charts"


def _get_charts_dir() -> str:
    """Return the directory where charts will be saved, creating it if necessary."""
    base = os.getenv(_CHARTS_ENV_VAR, "").strip()
    if not base:
        base = os.path.join(tempfile.gettempdir(), _CHARTS_SUBDIR)
    os.makedirs(base, exist_ok=True)
    return base


def _new_chart_path(suffix: str = "") -> str:
    """Generate a unique file path for a new chart PNG."""
    name = f"chart_{uuid.uuid4().hex}{suffix}.png"
    return os.path.join(_get_charts_dir(), name)


def _clamp(value: float | None, minimum: float = 0.0) -> float:
    if value is None:
        return 0.0
    return max(float(value), minimum)


def generate_nutrition_chart(
    *,
    title: str = "",
    calories_consumed: float | None = None,
    calories_goal: float | None = None,
    protein_g: float | None = None,
    protein_goal_g: float | None = None,
    carbs_g: float | None = None,
    carbs_goal_g: float | None = None,
    fat_g: float | None = None,
    fat_goal_g: float | None = None,
    calories_burned: float | None = None,
) -> str:
    """Generate a nutrition summary chart and return the saved file path.

    Produces a figure with up to three panels:
    - Calorie balance bar (consumed vs. goal, with calories burned overlay if provided)
    - Macronutrient bars (protein, carbs, fat) vs. goals
    - Macronutrient distribution pie chart (when all three values are present)

    Args:
        title: Chart heading (e.g. "Nutrição — 09/03/2026").
        calories_consumed: Total kcal consumed.
        calories_goal: Daily calorie goal.
        protein_g: Protein consumed in grams.
        protein_goal_g: Protein goal in grams.
        carbs_g: Carbohydrates consumed in grams.
        carbs_goal_g: Carbohydrates goal in grams.
        fat_g: Fat consumed in grams.
        fat_goal_g: Fat goal in grams.
        calories_burned: Calories burned through exercise.

    Returns:
        Absolute path of the saved PNG file.
    """
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend — safe for server environments
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D

    has_calories = calories_consumed is not None
    has_macros = any(v is not None for v in (protein_g, carbs_g, fat_g))
    has_pie = all(v is not None and v > 0 for v in (protein_g, carbs_g, fat_g))

    n_panels = sum([has_calories, has_macros, has_pie])
    if n_panels == 0:
        n_panels = 1  # Empty chart fallback

    fig_width = max(6, n_panels * 4.5)
    fig, axes = plt.subplots(1, n_panels, figsize=(fig_width, 5))
    if n_panels == 1:
        axes = [axes]

    panel_idx = 0
    _CONSUMED_COLOR = "#4C9BE8"
    _OVER_COLOR = "#E85454"
    _BURNED_COLOR = "#66BB6A"
    _GOAL_LINE_COLOR = "#E85454"
    _MACRO_COLORS = {"protein": "#66BB6A", "carbs": "#4C9BE8", "fat": "#EF5350"}

    # --- Calorie panel ---
    if has_calories:
        ax = axes[panel_idx]
        panel_idx += 1
        consumed = _clamp(calories_consumed)
        goal = _clamp(calories_goal)
        burned = _clamp(calories_burned)

        bar_labels = ["Consumidas"]
        bar_values = [consumed]
        bar_colors = [_CONSUMED_COLOR if goal == 0 or consumed <= goal else _OVER_COLOR]

        if burned > 0:
            bar_labels.append("Gastas (exerc.)")
            bar_values.append(burned)
            bar_colors.append(_BURNED_COLOR)

        bars = ax.bar(bar_labels, bar_values, color=bar_colors, edgecolor="white", linewidth=0.8)

        for bar, val in zip(bars, bar_values):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(goal, consumed, burned) * 0.02,
                    f"{val:.0f} kcal",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold",
                )

        # Goal as a horizontal reference line instead of a bar
        if goal > 0:
            ax.axhline(y=goal, color=_GOAL_LINE_COLOR, linestyle="--", linewidth=1.5,
                       alpha=0.85, zorder=3)
            ax.legend(
                handles=[Line2D([0], [0], color=_GOAL_LINE_COLOR, linestyle="--",
                                linewidth=1.5, label=f"Meta: {goal:.0f} kcal")],
                fontsize=8,
            )

        if goal > 0 and consumed > goal:
            ax.text(
                0.5, 0.97,
                f"⚠ {consumed - goal:.0f} kcal acima da meta",
                transform=ax.transAxes,
                ha="center", va="top",
                fontsize=8.5, color=_OVER_COLOR,
            )
        elif goal > 0:
            remaining = goal - consumed + burned
            ax.text(
                0.5, 0.97,
                f"Saldo: {remaining:.0f} kcal restantes",
                transform=ax.transAxes,
                ha="center", va="top",
                fontsize=8.5, color="#2E7D32",
            )

        ax.set_title("Calorias", fontweight="bold", fontsize=11)
        ax.set_ylabel("kcal")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_ylim(0, max(bar_values + [goal]) * 1.18 if (bar_values or goal) else 10)

    # --- Macronutrient bars panel ---
    if has_macros:
        ax = axes[panel_idx]
        panel_idx += 1

        macros = [
            ("Proteína", protein_g, protein_goal_g, _MACRO_COLORS["protein"]),
            ("Carboidratos", carbs_g, carbs_goal_g, _MACRO_COLORS["carbs"]),
            ("Gordura", fat_g, fat_goal_g, _MACRO_COLORS["fat"]),
        ]
        macros = [(lbl, v, g, c) for lbl, v, g, c in macros if v is not None]

        labels = [m[0] for m in macros]
        values = [_clamp(m[1]) for m in macros]
        goals = [_clamp(m[2]) for m in macros]
        colors = [m[3] for m in macros]

        x = list(range(len(labels)))
        bar_width = 0.5  # wider since goals are shown as lines, not side-by-side bars
        bars_consumed = ax.bar(
            x, values, bar_width,
            color=colors, alpha=0.85, edgecolor="white", label="Consumido"
        )

        # Goal as horizontal reference lines per macro
        has_any_goal = any(g > 0 for g in goals)
        for i, g in enumerate(goals):
            if g > 0:
                ax.hlines(
                    y=g,
                    xmin=i - bar_width / 2 - 0.05,
                    xmax=i + bar_width / 2 + 0.05,
                    colors=_GOAL_LINE_COLOR,
                    linestyles="--",
                    linewidth=1.5,
                    alpha=0.85,
                )

        for bar, val in zip(bars_consumed, values):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 1,
                    f"{val:.0f}g",
                    ha="center", va="bottom", fontsize=8.5, fontweight="bold",
                )

        if has_any_goal:
            ax.legend(
                handles=[Line2D([0], [0], color=_GOAL_LINE_COLOR, linestyle="--",
                                linewidth=1.5, label="Meta")],
                fontsize=8,
            )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_title("Macronutrientes", fontweight="bold", fontsize=11)
        ax.set_ylabel("gramas (g)")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_ylim(0, max(values + goals) * 1.20 if (values + goals) else 10)

    # --- Pie chart panel ---
    if has_pie:
        ax = axes[panel_idx]
        pie_values = [_clamp(protein_g), _clamp(carbs_g), _clamp(fat_g)]
        pie_labels = ["Proteína", "Carboidratos", "Gordura"]
        pie_colors = [_MACRO_COLORS["protein"], _MACRO_COLORS["carbs"], _MACRO_COLORS["fat"]]

        wedges, texts, autotexts = ax.pie(
            pie_values,
            labels=pie_labels,
            colors=pie_colors,
            autopct="%1.1f%%",
            startangle=140,
            pctdistance=0.82,
            textprops={"fontsize": 8.5},
        )
        for autotext in autotexts:
            autotext.set_fontweight("bold")

        ax.set_title("Distribuição\nde macros", fontweight="bold", fontsize=11)

    # --- Figure layout ---
    fig.suptitle(title or "Resumo nutricional", fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()

    output_path = _new_chart_path()
    fig.savefig(output_path, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path
