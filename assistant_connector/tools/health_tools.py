from __future__ import annotations

import datetime
import os
import re

from assistant_connector.health_store import HealthStore, normalize_quantity, parse_quantity_details
from utils.timezone_utils import today_in_configured_timezone, today_iso_in_configured_timezone

_default_db_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "assistant_memory.sqlite3")
)
_health_store = HealthStore(db_path=os.getenv("ASSISTANT_MEMORY_PATH", _default_db_path))

_ALLOWED_MEAL_CATEGORIES = ("ALMOÇO", "JANTAR", "LANCHE", "CAFÉ DA MANHÃ", "SUPLEMENTO")
_MEAL_CATEGORY_ALIASES = {
    "almoco": "ALMOÇO", "almoço": "ALMOÇO",
    "jantar": "JANTAR",
    "lanche": "LANCHE",
    "cafe da manha": "CAFÉ DA MANHÃ", "café da manhã": "CAFÉ DA MANHÃ",
    "cafe da manhã": "CAFÉ DA MANHÃ", "café da manha": "CAFÉ DA MANHÃ",
    "cafe": "CAFÉ DA MANHÃ", "breakfast": "CAFÉ DA MANHÃ",
    "suplemento": "SUPLEMENTO", "suplementos": "SUPLEMENTO",
    "supplement": "SUPLEMENTO", "supplements": "SUPLEMENTO",
}
_SUGGESTION_SUGAR_KEYWORDS = ("refrigerante", "suco", "bolo", "doce", "chocolate", "sorvete")
_SUGGESTION_VEGETABLE_KEYWORDS = ("salada", "alface", "brocolis", "brócolis", "legume", "verdura")


def _normalize_meal_category(raw_value: str) -> str:
    meal_category = str(raw_value or "").strip()
    if not meal_category:
        raise ValueError("refeicao is required")
    normalized = meal_category.lower()
    normalized = (
        normalized.replace("á", "a").replace("à", "a").replace("â", "a").replace("ã", "a")
        .replace("é", "e").replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o").replace("ô", "o").replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    canonical = _MEAL_CATEGORY_ALIASES.get(normalized)
    if canonical:
        return canonical
    if meal_category in _ALLOWED_MEAL_CATEGORIES:
        return meal_category
    raise ValueError(f"refeicao must be one of: {', '.join(_ALLOWED_MEAL_CATEGORIES)}")


def _read_optional_boolean(arguments, *keys):
    for key in keys:
        if key not in arguments:
            continue
        raw_value = arguments.get(key)
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, (int, float)) and raw_value in {0, 1}:
            return bool(int(raw_value))
        normalized = str(raw_value or "").strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "sim", "s"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "nao", "não"}:
            return False
        raise ValueError(f"{keys[0]} must be a boolean")
    return None


def _resolve_exercise_done_value(exercise: dict, today: datetime.date) -> bool:
    done_value = _read_optional_boolean({"done": exercise.get("done")}, "done")
    if done_value is not None:
        return done_value
    exercise_date = str(exercise.get("date") or "").strip()
    try:
        parsed_date = datetime.date.fromisoformat(exercise_date[:10])
    except ValueError:
        return False
    return parsed_date <= today


def _build_meal_insights(meals: list, meal_breakdown: list, average_calories_per_day: float) -> list:
    insights = []
    if not meals:
        return [
            "Nenhum registro encontrado no período. Sem consistência de registro você não melhora: registre todas as refeições a partir de hoje."
        ]
    if average_calories_per_day > 2500:
        insights.append(
            "Média calórica diária acima de 2500 kcal: ajuste imediato necessário. Reduza ultraprocessados/doces e aumente vegetais nas próximas refeições."
        )
    elif average_calories_per_day < 1200:
        insights.append(
            "Média calórica diária abaixo de 1200 kcal: isso indica sub-registro ou ingestão insuficiente. Corrija o registro completo e mantenha refeições estruturadas."
        )
    dinner_aliases = {"jantar", "dinner", "ceia"}
    dinner_entry = next(
        (e for e in meal_breakdown if str(e.get("meal_type", "")).strip().lower() in dinner_aliases),
        None,
    )
    total_calories = sum(float(m.get("calories") or 0.0) for m in meals)
    if dinner_entry and total_calories > 0 and (dinner_entry["calories"] / total_calories) >= 0.45:
        insights.append(
            "Mais de 45% das calorias estão concentradas no jantar/ceia. Rebalanceie já: antecipe calorias para café da manhã e almoço."
        )
    normalized_foods = " ".join(str(m.get("food") or "").lower() for m in meals)
    sugary_occurrences = sum(1 for kw in _SUGGESTION_SUGAR_KEYWORDS if kw in normalized_foods)
    if sugary_occurrences >= 2:
        insights.append(
            "Consumo frequente de itens açucarados detectado. Reduza ativamente doces e bebidas açucaradas nesta semana."
        )
    has_vegetables = any(kw in normalized_foods for kw in _SUGGESTION_VEGETABLE_KEYWORDS)
    if not has_vegetables:
        insights.append(
            "Não há indícios de verduras/legumes nas refeições registradas. Defina regra mínima: incluir vegetais no almoço e no jantar todos os dias."
        )
    if not insights:
        insights.append("Boa consistência alimentar. Mantenha disciplina e preserve variedade nutricional diariamente.")
    return insights


# ---------------------------------------------------------------------------
# Task tools
# ---------------------------------------------------------------------------

def list_tasks(arguments, context):
    n_days = max(int(arguments.get("n_days", 0)), 0)
    limit = min(max(int(arguments.get("limit", 10)), 1), 50)
    tasks = _health_store.list_tasks(user_id=context.user_id, n_days=n_days, limit=limit)
    return {"total": len(tasks), "returned": len(tasks), "tasks": tasks}


def create_task(arguments, context):
    task_name = str(arguments.get("task_name", "")).strip()
    if not task_name:
        raise ValueError("task_name is required")
    project = str(arguments.get("project", "Pessoal")).strip() or "Pessoal"
    due_date = str(arguments.get("due_date", today_iso_in_configured_timezone())).strip()
    try:
        datetime.date.fromisoformat(due_date)
    except ValueError:
        raise ValueError("due_date must be a valid ISO date (YYYY-MM-DD)")
    tags = arguments.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")
    clean_tags = [str(t).strip() for t in tags if str(t).strip()]
    return _health_store.create_task(
        user_id=context.user_id,
        task_name=task_name,
        project=project,
        due_date=due_date,
        tags=clean_tags,
    )


def edit_task(arguments, context):
    task_id = str(arguments.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("task_id is required")
    kwargs = {}
    if "task_name" in arguments:
        name = str(arguments.get("task_name", "")).strip()
        if not name:
            raise ValueError("task_name must be a non-empty string when provided")
        kwargs["task_name"] = name
    if "project" in arguments:
        kwargs["project"] = str(arguments.get("project", "")).strip() or "Pessoal"
    if "due_date" in arguments:
        due_date = str(arguments.get("due_date", "")).strip()
        if due_date:
            try:
                datetime.date.fromisoformat(due_date)
            except ValueError:
                raise ValueError("due_date must be a valid ISO date (YYYY-MM-DD)")
            kwargs["due_date"] = due_date
    if "tags" in arguments:
        tags = arguments.get("tags", [])
        if not isinstance(tags, list):
            raise ValueError("tags must be a list")
        kwargs["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if "done" in arguments:
        kwargs["done"] = bool(arguments.get("done"))
    if not kwargs:
        raise ValueError("At least one task field is required")
    return _health_store.update_task(user_id=context.user_id, task_id=task_id, **kwargs)


# ---------------------------------------------------------------------------
# Meal tools
# ---------------------------------------------------------------------------

def register_meal(arguments, context):
    food = str(arguments.get("alimento", arguments.get("food", ""))).strip()
    meal_type = _normalize_meal_category(arguments.get("refeicao", arguments.get("meal_type", "")))
    quantity = str(arguments.get("quantidade", arguments.get("quantity", ""))).strip()
    meal_date = str(arguments.get("data", arguments.get("date", today_iso_in_configured_timezone()))).strip()
    estimated_calories = arguments.get("calorias_estimadas", arguments.get("estimated_calories"))

    if not food:
        raise ValueError("alimento is required")
    if not quantity:
        raise ValueError("quantidade is required")
    if estimated_calories is None:
        raise ValueError("calorias_estimadas is required")
    try:
        datetime.date.fromisoformat(meal_date)
    except ValueError:
        raise ValueError("date must be a valid ISO date (YYYY-MM-DD)")
    try:
        calories = float(str(estimated_calories).replace(",", "."))
    except ValueError:
        raise ValueError("calorias_estimadas must be a valid number")
    if calories <= 0:
        raise ValueError("calorias_estimadas must be greater than zero")

    normalized_amount = None
    normalized_unit = None
    calorie_estimation_method = "provided"
    try:
        qty_details = parse_quantity_details(quantity)
        qty_normalized = normalize_quantity(qty_details)
        normalized_amount = qty_normalized["amount"]
        normalized_unit = qty_normalized["unit"]
        quantity = f"{qty_details['amount']} {qty_details['unit']}"
    except (ValueError, Exception):
        pass

    meal = _health_store.create_meal(
        user_id=context.user_id,
        food=food,
        meal_type=meal_type,
        quantity=quantity,
        calories=calories,
        date=meal_date,
        normalized_amount=normalized_amount,
        normalized_unit=normalized_unit,
    )
    meal["calorie_estimation_method"] = calorie_estimation_method
    return {"status": "created", "meal": meal}


def analyze_meals(arguments, context):
    days_back = max(int(arguments.get("days_back", 7)), 0)
    days_forward = max(int(arguments.get("days_forward", 0)), 0)
    limit = min(max(int(arguments.get("limit", 100)), 1), 300)

    today = today_in_configured_timezone()
    start_date = today - datetime.timedelta(days=days_back)
    end_date = today + datetime.timedelta(days=days_forward)

    meals = _health_store.list_meals_by_date_range(
        user_id=context.user_id,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        limit=limit,
    )
    selected_meals = meals[:limit]
    total_calories = round(sum(float(m.get("calories") or 0.0) for m in selected_meals), 2)

    by_meal_type: dict = {}
    by_food: dict = {}
    covered_days: set = set()
    for meal in selected_meals:
        meal_day = str(meal.get("date") or "")[:10]
        if meal_day:
            covered_days.add(meal_day)
        meal_type = str(meal.get("meal_type") or "Não informado")
        by_meal_type.setdefault(meal_type, {"meal_type": meal_type, "entries": 0, "calories": 0.0})
        by_meal_type[meal_type]["entries"] += 1
        by_meal_type[meal_type]["calories"] += float(meal.get("calories") or 0.0)
        food_name = str(meal.get("food") or "").strip()
        if food_name:
            key = food_name.lower()
            by_food.setdefault(key, {"food": food_name, "entries": 0, "calories": 0.0})
            by_food[key]["entries"] += 1
            by_food[key]["calories"] += float(meal.get("calories") or 0.0)

    meal_breakdown = [
        {"meal_type": v["meal_type"], "entries": v["entries"], "calories": round(v["calories"], 2)}
        for v in sorted(by_meal_type.values(), key=lambda x: x["calories"], reverse=True)
    ]
    top_foods = [
        {"food": v["food"], "entries": v["entries"], "calories": round(v["calories"], 2)}
        for v in sorted(by_food.values(), key=lambda x: x["calories"], reverse=True)[:5]
    ]
    days_count = len(covered_days)
    avg_cal_per_day = round(total_calories / days_count, 2) if days_count else 0.0
    insights = _build_meal_insights(selected_meals, meal_breakdown, avg_cal_per_day)

    return {
        "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "total_entries": len(meals),
        "returned_entries": len(selected_meals),
        "days_with_entries": days_count,
        "total_calories": total_calories,
        "average_calories_per_day": avg_cal_per_day,
        "meal_breakdown": meal_breakdown,
        "top_foods": top_foods,
        "insights": insights,
        "meals": selected_meals,
    }


# ---------------------------------------------------------------------------
# Exercise tools
# ---------------------------------------------------------------------------

def register_exercise(arguments, context):
    activity = str(arguments.get("atividade", arguments.get("activity", ""))).strip()
    raw_calories = arguments.get("calorias", arguments.get("calories"))
    exercise_date = str(arguments.get("data", arguments.get("date", today_iso_in_configured_timezone()))).strip()
    try:
        exercise_date_value = datetime.date.fromisoformat(exercise_date)
    except ValueError:
        raise ValueError("date must be a valid ISO date (YYYY-MM-DD)")
    observations = str(arguments.get("observacoes", arguments.get("observations", ""))).strip()
    done = _read_optional_boolean(arguments, "done", "concluido", "concluído")
    if done is None:
        done = exercise_date_value <= today_in_configured_timezone()
    if not activity:
        raise ValueError("atividade is required")
    if raw_calories is None:
        raise ValueError("calorias is required")
    try:
        calories = float(str(raw_calories).replace(",", "."))
    except ValueError:
        raise ValueError("calorias must be a valid number")
    if calories <= 0:
        raise ValueError("calorias must be greater than zero")

    existing = _health_store.find_exercise_duplicate(
        user_id=context.user_id, activity=activity, date=exercise_date
    )
    if existing:
        return {
            "error": "duplicate_exercise_found",
            "message": (
                f"Já existe um registro de '{activity}' em {exercise_date}. "
                "Use edit_exercise com o exercise_id abaixo para atualizar."
            ),
            "existing_exercise": existing,
        }

    exercise = _health_store.create_exercise(
        user_id=context.user_id,
        activity=activity,
        calories=calories,
        date=exercise_date,
        observations=observations,
        done=done,
    )
    return {"status": "created", "exercise": exercise}


def edit_exercise(arguments, context):
    exercise_id = str(arguments.get("exercise_id", "")).strip()
    if not exercise_id:
        raise ValueError("exercise_id is required")

    kwargs: dict = {}
    if "atividade" in arguments or "activity" in arguments:
        activity = str(arguments.get("atividade", arguments.get("activity", ""))).strip()
        if not activity:
            raise ValueError("atividade must be a non-empty string when provided")
        kwargs["activity"] = activity
    if "calorias" in arguments or "calories" in arguments:
        raw_cal = arguments.get("calorias", arguments.get("calories"))
        try:
            kwargs["calories"] = float(str(raw_cal).replace(",", "."))
        except ValueError:
            raise ValueError("calorias must be a valid number")
    if "data" in arguments or "date" in arguments:
        exercise_date = str(arguments.get("data", arguments.get("date", ""))).strip()
        try:
            datetime.date.fromisoformat(exercise_date)
        except ValueError:
            raise ValueError("date must be a valid ISO date (YYYY-MM-DD)")
        kwargs["date"] = exercise_date
    if "observacoes" in arguments or "observations" in arguments:
        kwargs["observations"] = str(arguments.get("observacoes", arguments.get("observations", ""))).strip()
    done = _read_optional_boolean(arguments, "done", "concluido", "concluído")
    if done is not None:
        kwargs["done"] = done

    return _health_store.update_exercise(
        user_id=context.user_id, exercise_id=exercise_id, **kwargs
    )


def analyze_exercises(arguments, context):
    days_back = max(int(arguments.get("days_back", 7)), 0)
    days_forward = max(int(arguments.get("days_forward", 0)), 0)
    limit = min(max(int(arguments.get("limit", 100)), 1), 300)

    include_meals_arg = arguments.get("include_meals", True)
    if isinstance(include_meals_arg, str):
        include_meals = include_meals_arg.strip().lower() not in {"0", "false", "no", "nao", "não"}
    else:
        include_meals = bool(include_meals_arg)

    today = today_in_configured_timezone()
    start_date = today - datetime.timedelta(days=days_back)
    end_date = today + datetime.timedelta(days=days_forward)

    exercises = _health_store.list_exercises_by_date_range(
        user_id=context.user_id,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        limit=limit,
    )
    selected_exercises = exercises[:limit]
    normalized_exercises = []
    completed_entries = pending_entries = pending_future_entries = 0
    total_exercise_calories = planned_exercise_calories = 0.0

    for exercise in selected_exercises:
        ne = dict(exercise)
        is_done = _resolve_exercise_done_value(ne, today)
        ne["done"] = is_done
        normalized_exercises.append(ne)
        cal = float(ne.get("calories") or 0.0)
        if is_done:
            completed_entries += 1
            total_exercise_calories += cal
        else:
            pending_entries += 1
            planned_exercise_calories += cal
            try:
                if datetime.date.fromisoformat(str(ne.get("date") or "")[:10]) > today:
                    pending_future_entries += 1
            except ValueError:
                continue

    total_exercise_calories = round(total_exercise_calories, 2)
    planned_exercise_calories = round(planned_exercise_calories, 2)
    period_days = (end_date - start_date).days + 1
    avg_cal_per_day = round(total_exercise_calories / period_days, 2) if period_days > 0 else 0.0

    by_activity: dict = {}
    for ex in normalized_exercises:
        act = str(ex.get("activity") or "Não informado").strip() or "Não informado"
        by_activity.setdefault(act, {"activity": act, "entries": 0, "calories": 0.0})
        by_activity[act]["entries"] += 1
        by_activity[act]["calories"] += float(ex.get("calories") or 0.0)

    breakdown_by_activity = [
        {"activity": v["activity"], "entries": v["entries"], "calories": round(v["calories"], 2)}
        for v in sorted(by_activity.values(), key=lambda x: x["calories"], reverse=True)
    ]

    total_meal_calories = None
    net_calorie_balance = None
    meal_entries = None
    if include_meals:
        meals = _health_store.list_meals_by_date_range(
            user_id=context.user_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        meal_entries = len(meals)
        total_meal_calories = round(sum(float(m.get("calories") or 0.0) for m in meals), 2)
        net_calorie_balance = round(total_meal_calories - total_exercise_calories, 2)

    return {
        "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "total_entries": len(exercises),
        "returned_entries": len(normalized_exercises),
        "totals": {
            "total_exercise_calories": total_exercise_calories,
            "total_planned_calories": planned_exercise_calories,
            "average_exercise_calories_per_day": avg_cal_per_day,
            "completed_entries": completed_entries,
            "pending_entries": pending_entries,
            "pending_future_entries": pending_future_entries,
            "total_meal_calories": total_meal_calories,
            "net_calorie_balance": net_calorie_balance,
            "meal_entries": meal_entries,
        },
        "breakdown_by_activity": breakdown_by_activity,
        "entries": normalized_exercises,
        "include_meals": include_meals,
    }


def check_daily_logging_status(arguments, context):
    today = today_in_configured_timezone()
    today_iso = today.isoformat()

    meals = _health_store.list_meals_by_date_range(
        user_id=context.user_id,
        start_date=today_iso,
        end_date=today_iso,
    )
    exercises = _health_store.list_exercises_by_date_range(
        user_id=context.user_id,
        start_date=today_iso,
        end_date=today_iso,
    )

    meal_types_logged = {str(m.get("meal_type") or "").strip() for m in meals if str(m.get("meal_type") or "").strip()}
    exercise_names = [str(e.get("activity") or "").strip() for e in exercises if str(e.get("activity") or "").strip()]

    return {
        "today": today_iso,
        "meals_logged": len(meals) > 0,
        "meal_count": len(meals),
        "meal_types_logged": sorted(meal_types_logged),
        "exercises_logged": len(exercises) > 0,
        "exercise_count": len(exercises),
        "exercise_names": exercise_names,
    }
