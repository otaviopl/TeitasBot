from __future__ import annotations

import datetime
import re

from notion_connector import notion_connector
from utils.timezone_utils import today_in_configured_timezone, today_iso_in_configured_timezone

_CATEGORY_KEYWORDS = {
    "Alimentação": ("mercado", "restaurante", "ifood", "lanche", "almoço", "jantar", "cafe"),
    "Transporte": ("uber", "99", "taxi", "ônibus", "onibus", "combustivel", "gasolina", "pedagio"),
    "Moradia": ("aluguel", "condominio", "energia", "luz", "agua", "internet", "gás", "gas"),
    "Saúde": ("farmacia", "remedio", "consulta", "exame", "plano de saude", "hospital"),
    "Lazer": ("cinema", "streaming", "show", "viagem", "bar"),
}
_CATEGORY_ALIASES = {
    "alimentacao": "Alimentação",
    "alimentação": "Alimentação",
    "mercado": "Alimentação",
    "transporte": "Transporte",
    "mobilidade": "Transporte",
    "moradia": "Moradia",
    "casa": "Moradia",
    "saude": "Saúde",
    "saúde": "Saúde",
    "lazer": "Lazer",
    "outros": "Outros",
}
_SUGGESTION_SUGAR_KEYWORDS = ("refrigerante", "suco", "bolo", "doce", "chocolate", "sorvete")
_SUGGESTION_VEGETABLE_KEYWORDS = ("salada", "alface", "brocolis", "brócolis", "legume", "verdura")
_ALLOWED_MEAL_CATEGORIES = ("ALMOÇO", "JANTAR", "LANCHE", "CAFÉ DA MANHÃ", "SUPLEMENTO")
_MEAL_CATEGORY_ALIASES = {
    "almoco": "ALMOÇO",
    "almoço": "ALMOÇO",
    "jantar": "JANTAR",
    "lanche": "LANCHE",
    "cafe da manha": "CAFÉ DA MANHÃ",
    "café da manhã": "CAFÉ DA MANHÃ",
    "cafe da manhã": "CAFÉ DA MANHÃ",
    "café da manha": "CAFÉ DA MANHÃ",
    "cafe": "CAFÉ DA MANHÃ",
    "breakfast": "CAFÉ DA MANHÃ",
    "suplemento": "SUPLEMENTO",
    "suplementos": "SUPLEMENTO",
    "supplement": "SUPLEMENTO",
    "supplements": "SUPLEMENTO",
}


def _infer_expense_category(description):
    normalized = description.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return "Outros"


def _normalize_expense_category(raw_category, description):
    category = str(raw_category or "").strip()
    if not category:
        return _infer_expense_category(description)
    normalized = category.lower()
    return _CATEGORY_ALIASES.get(normalized, category.title())


def _month_bounds(target_date):
    month_start = target_date.replace(day=1)
    if month_start.month == 12:
        next_month = datetime.date(month_start.year + 1, 1, 1)
    else:
        next_month = datetime.date(month_start.year, month_start.month + 1, 1)
    return month_start, (next_month - datetime.timedelta(days=1))


def _normalize_meal_category(raw_value):
    meal_category = str(raw_value or "").strip()
    if not meal_category:
        raise ValueError("refeicao is required")

    normalized = meal_category.lower()
    normalized = (
        normalized.replace("á", "a")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ã", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
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


def _resolve_exercise_done_value(exercise, today):
    done_value = _read_optional_boolean({"done": exercise.get("done")}, "done")
    if done_value is not None:
        return done_value

    exercise_date = str(exercise.get("date") or "").strip()
    try:
        parsed_date = datetime.date.fromisoformat(exercise_date[:10])
    except ValueError:
        return False
    return parsed_date <= today


def list_notion_tasks(arguments, context):
    n_days = max(int(arguments.get("n_days", 0)), 0)
    limit = int(arguments.get("limit", 10))
    limit = min(max(limit, 1), 50)

    tasks = notion_connector.collect_tasks_from_control_panel(
        n_days=n_days,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "total": len(tasks),
        "returned": min(limit, len(tasks)),
        "tasks": tasks[:limit],
    }


def list_notion_notes(arguments, context):
    days_back = max(int(arguments.get("days_back", 5)), 0)
    days_forward = max(int(arguments.get("days_forward", 5)), 0)
    limit = int(arguments.get("limit", 20))
    limit = min(max(limit, 1), 100)

    notes = notion_connector.collect_notes_around_today(
        days_back=days_back,
        days_forward=days_forward,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "total": len(notes),
        "returned": min(limit, len(notes)),
        "notes": notes[:limit],
    }


def create_notion_task(arguments, context):
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

    clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]

    return notion_connector.create_task_in_control_panel(
        {
            "task_name": task_name,
            "project": project,
            "due_date": due_date,
            "tags": clean_tags,
        },
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )


def create_notion_note(arguments, context):
    note_name = str(arguments.get("note_name", "")).strip()
    if not note_name:
        raise ValueError("note_name is required")

    tag = str(arguments.get("tag", "GENERAL")).strip() or "GENERAL"
    observations = str(arguments.get("observations", ""))
    url = str(arguments.get("url", "")).strip()

    return notion_connector.create_note_in_notes_db(
        {
            "note_name": note_name,
            "tag": tag,
            "observations": observations,
            "url": url,
        },
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )


def edit_notion_item(arguments, context):
    item_type = str(arguments.get("item_type", "")).strip().lower()
    if item_type not in {"task", "card"}:
        raise ValueError("item_type must be 'task' or 'card'")

    page_id = str(arguments.get("page_id", "")).strip()
    if not page_id:
        raise ValueError("page_id is required")

    payload = {
        "item_type": item_type,
        "page_id": page_id,
    }
    content = None
    if "content" in arguments:
        raw_content = str(arguments.get("content", ""))
        if raw_content.strip():
            content = raw_content
            payload["content"] = raw_content
    if "content_mode" in arguments and content is not None:
        content_mode = str(arguments.get("content_mode", "")).strip().lower()
        if content_mode and content_mode not in {"append", "replace"}:
            raise ValueError("content_mode must be 'append' or 'replace'")
        if content_mode:
            payload["content_mode"] = content_mode

    if item_type == "task":
        if "task_name" in arguments:
            task_name = str(arguments.get("task_name", "")).strip()
            if task_name:
                payload["task_name"] = task_name
        if "due_date" in arguments:
            due_date = str(arguments.get("due_date", "")).strip()
            if due_date:
                try:
                    datetime.date.fromisoformat(due_date)
                except ValueError:
                    raise ValueError("due_date must be a valid ISO date (YYYY-MM-DD)")
                payload["due_date"] = due_date
        if "project" in arguments:
            project = str(arguments.get("project", "")).strip()
            if project:
                payload["project"] = project
        if "tags" in arguments:
            tags = arguments.get("tags", [])
            if not isinstance(tags, list):
                raise ValueError("tags must be a list")
            clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
            if clean_tags:
                payload["tags"] = clean_tags
        if "done" in arguments:
            payload["done"] = bool(arguments.get("done"))
        if set(payload.keys()) == {"item_type", "page_id"}:
            raise ValueError("At least one task field is required")
    else:
        if "note_name" in arguments:
            note_name = str(arguments.get("note_name", "")).strip()
            if note_name:
                payload["note_name"] = note_name
        if "tag" in arguments:
            tag = str(arguments.get("tag", "")).strip()
            if tag:
                payload["tag"] = tag
        if "observations" in arguments:
            observations = str(arguments.get("observations", ""))
            if observations.strip():
                payload["observations"] = observations
        if "url" in arguments:
            url = str(arguments.get("url", "")).strip()
            if url:
                payload["url"] = url
        if "date" in arguments:
            date_value = str(arguments.get("date", "")).strip()
            if date_value:
                try:
                    datetime.date.fromisoformat(date_value)
                except ValueError:
                    raise ValueError("date must be a valid ISO date (YYYY-MM-DD)")
                payload["date"] = date_value
        if set(payload.keys()) == {"item_type", "page_id"}:
            raise ValueError("At least one card field is required")

    return notion_connector.update_notion_page(payload, project_logger=context.project_logger, user_id=context.user_id, credential_store=context.user_credential_store)


def register_financial_expense(arguments, context):
    description = str(arguments.get("description", "")).strip()
    if not description:
        raise ValueError("description is required")

    raw_amount = str(arguments.get("amount", "")).strip().replace(",", ".")
    try:
        amount = float(raw_amount)
    except ValueError:
        raise ValueError("amount must be a valid number")
    if amount <= 0:
        raise ValueError("amount must be greater than zero")

    expense_date_raw = str(arguments.get("expense_date", today_iso_in_configured_timezone())).strip()
    try:
        expense_date = datetime.date.fromisoformat(expense_date_raw)
    except ValueError:
        raise ValueError("expense_date must be a valid ISO date (YYYY-MM-DD)")
    category = _normalize_expense_category(arguments.get("category"), description)
    create_result = notion_connector.create_expense_in_expenses_db(
        {
            "name": f"Despesa {expense_date.isoformat()}",
            "date": expense_date.isoformat(),
            "category": category,
            "description": description,
            "amount": amount,
        },
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "status": "created",
        "expense_id": create_result.get("id"),
        "expense": {
            "date": expense_date.isoformat(),
            "amount": amount,
            "category": category,
            "description": description,
        },
    }


def register_notion_meal(arguments, context):
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

    created_meal = notion_connector.create_meal_in_meals_db(
        {
            "food": food,
            "meal_type": meal_type,
            "quantity": quantity,
            "date": meal_date,
            "estimated_calories": estimated_calories,
        },
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "status": "created",
        "meal": created_meal,
    }


def _find_existing_exercise(activity, exercise_date, context):
    """Search for an existing exercise matching activity and date."""
    date_str = str(exercise_date)
    start_datetime = f"{date_str}T00:00:00Z"
    end_datetime = f"{date_str}T23:59:59Z"
    try:
        exercises = notion_connector.collect_exercises_from_database(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            project_logger=context.project_logger,
            user_id=context.user_id,
            credential_store=context.user_credential_store,
        )
    except Exception:
        return None
    normalized_activity = activity.strip().lower()
    for exercise in exercises:
        existing_activity = str(exercise.get("activity") or "").strip().lower()
        if existing_activity == normalized_activity:
            return exercise
    return None


def register_notion_exercise(arguments, context):
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

    existing = _find_existing_exercise(activity, exercise_date, context)
    if existing:
        return {
            "error": "duplicate_exercise_found",
            "message": (
                f"Já existe um registro de '{activity}' em {exercise_date}. "
                "Use edit_notion_exercise com o page_id abaixo para atualizar."
            ),
            "existing_exercise": existing,
        }

    created_exercise = notion_connector.create_exercise_in_exercises_db(
        {
            "activity": activity,
            "calories": calories,
            "date": exercise_date,
            "observations": observations,
            "done": done,
        },
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "status": "created",
        "exercise": created_exercise,
    }


def edit_notion_exercise(arguments, context):
    page_id = str(arguments.get("page_id", "")).strip()
    if not page_id:
        raise ValueError("page_id is required")

    kwargs = {}
    if "atividade" in arguments or "activity" in arguments:
        activity = str(arguments.get("atividade", arguments.get("activity", ""))).strip()
        if not activity:
            raise ValueError("atividade must be a non-empty string when provided")
        kwargs["activity"] = activity
    if "calorias" in arguments or "calories" in arguments:
        raw_calories = arguments.get("calorias", arguments.get("calories"))
        try:
            kwargs["calories"] = float(str(raw_calories).replace(",", "."))
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

    return notion_connector.update_exercise_in_exercises_db(
        page_id,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
        **kwargs,
    )


def analyze_notion_exercises(arguments, context):
    days_back = max(int(arguments.get("days_back", 7)), 0)
    days_forward = max(int(arguments.get("days_forward", 0)), 0)
    limit = int(arguments.get("limit", 100))
    limit = min(max(limit, 1), 300)

    include_meals_arg = arguments.get("include_meals", True)
    if isinstance(include_meals_arg, str):
        include_meals = include_meals_arg.strip().lower() not in {"0", "false", "no", "nao", "não"}
    else:
        include_meals = bool(include_meals_arg)

    today = today_in_configured_timezone()
    start_date = today - datetime.timedelta(days=days_back)
    end_date = today + datetime.timedelta(days=days_forward)
    start_datetime = f"{start_date.isoformat()}T00:00:00Z"
    end_datetime = f"{end_date.isoformat()}T23:59:59Z"

    exercises = notion_connector.collect_exercises_from_database(
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    selected_exercises = exercises[:limit]
    normalized_exercises = []
    completed_entries = 0
    pending_entries = 0
    pending_future_entries = 0

    total_exercise_calories = 0.0
    planned_exercise_calories = 0.0
    for exercise in selected_exercises:
        normalized_exercise = dict(exercise)
        is_done = _resolve_exercise_done_value(normalized_exercise, today)
        normalized_exercise["done"] = is_done
        normalized_exercises.append(normalized_exercise)

        calories_value = float(normalized_exercise.get("calories") or 0.0)
        if is_done:
            completed_entries += 1
            total_exercise_calories += calories_value
        else:
            pending_entries += 1
            planned_exercise_calories += calories_value
            exercise_date = str(normalized_exercise.get("date") or "").strip()
            try:
                if datetime.date.fromisoformat(exercise_date[:10]) > today:
                    pending_future_entries += 1
            except ValueError:
                continue
    total_exercise_calories = round(total_exercise_calories, 2)
    planned_exercise_calories = round(planned_exercise_calories, 2)
    period_days = (end_date - start_date).days + 1
    average_exercise_calories_per_day = round(total_exercise_calories / period_days, 2) if period_days > 0 else 0.0

    by_activity = {}
    for exercise in normalized_exercises:
        activity = str(exercise.get("activity") or "Não informado").strip() or "Não informado"
        by_activity.setdefault(activity, {"activity": activity, "entries": 0, "calories": 0.0})
        by_activity[activity]["entries"] += 1
        by_activity[activity]["calories"] += float(exercise.get("calories") or 0.0)

    breakdown_by_activity = [
        {
            "activity": payload["activity"],
            "entries": payload["entries"],
            "calories": round(payload["calories"], 2),
        }
        for payload in sorted(by_activity.values(), key=lambda item: item["calories"], reverse=True)
    ]

    total_meal_calories = None
    net_calorie_balance = None
    meal_entries = None
    if include_meals:
        meals = notion_connector.collect_meals_from_database(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            project_logger=context.project_logger,
            user_id=context.user_id,
            credential_store=context.user_credential_store,
        )
        meal_entries = len(meals)
        total_meal_calories = round(sum(float(meal.get("calories") or 0.0) for meal in meals), 2)
        net_calorie_balance = round(total_meal_calories - total_exercise_calories, 2)

    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "total_entries": len(exercises),
        "returned_entries": len(normalized_exercises),
        "totals": {
            "total_exercise_calories": total_exercise_calories,
            "total_planned_calories": planned_exercise_calories,
            "average_exercise_calories_per_day": average_exercise_calories_per_day,
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


def analyze_notion_meals(arguments, context):
    days_back = max(int(arguments.get("days_back", 7)), 0)
    days_forward = max(int(arguments.get("days_forward", 0)), 0)
    limit = int(arguments.get("limit", 100))
    limit = min(max(limit, 1), 300)

    today = today_in_configured_timezone()
    start_date = today - datetime.timedelta(days=days_back)
    end_date = today + datetime.timedelta(days=days_forward)
    start_datetime = f"{start_date.isoformat()}T00:00:00Z"
    end_datetime = f"{end_date.isoformat()}T23:59:59Z"

    meals = notion_connector.collect_meals_from_database(
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    selected_meals = meals[:limit]
    total_calories = round(sum(float(meal.get("calories") or 0.0) for meal in selected_meals), 2)

    by_meal_type = {}
    by_food = {}
    covered_days = set()
    for meal in selected_meals:
        meal_day = str(meal.get("date") or "")[:10]
        if meal_day:
            covered_days.add(meal_day)
        else:
            created_time = str(meal.get("created_time") or "")
            if created_time:
                covered_days.add(created_time[:10])
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
        {
            "meal_type": payload["meal_type"],
            "entries": payload["entries"],
            "calories": round(payload["calories"], 2),
        }
        for payload in sorted(by_meal_type.values(), key=lambda item: item["calories"], reverse=True)
    ]
    top_foods = [
        {
            "food": payload["food"],
            "entries": payload["entries"],
            "calories": round(payload["calories"], 2),
        }
        for payload in sorted(by_food.values(), key=lambda item: item["calories"], reverse=True)[:5]
    ]
    days_count = len(covered_days)
    average_calories_per_day = round(total_calories / days_count, 2) if days_count else 0.0
    insights = _build_meal_insights(selected_meals, meal_breakdown, average_calories_per_day)

    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "total_entries": len(meals),
        "returned_entries": len(selected_meals),
        "days_with_entries": days_count,
        "total_calories": total_calories,
        "average_calories_per_day": average_calories_per_day,
        "meal_breakdown": meal_breakdown,
        "top_foods": top_foods,
        "insights": insights,
        "meals": selected_meals,
    }


def check_daily_logging_status(arguments, context):
    today = today_in_configured_timezone()
    today_iso = today.isoformat()
    start_datetime = f"{today_iso}T00:00:00Z"
    end_datetime = f"{today_iso}T23:59:59Z"

    meals = notion_connector.collect_meals_from_database(
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    exercises = notion_connector.collect_exercises_from_database(
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )

    meal_types_logged = set()
    for meal in meals:
        meal_type = str(meal.get("meal_type") or "").strip()
        if meal_type:
            meal_types_logged.add(meal_type)

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


def _build_meal_insights(meals, meal_breakdown, average_calories_per_day):
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
        (entry for entry in meal_breakdown if str(entry.get("meal_type", "")).strip().lower() in dinner_aliases),
        None,
    )
    total_calories = sum(float(meal.get("calories") or 0.0) for meal in meals)
    if dinner_entry and total_calories > 0 and (dinner_entry["calories"] / total_calories) >= 0.45:
        insights.append(
            "Mais de 45% das calorias estão concentradas no jantar/ceia. Rebalanceie já: antecipe calorias para café da manhã e almoço."
        )

    normalized_foods = " ".join(str(meal.get("food") or "").lower() for meal in meals)
    sugary_occurrences = sum(1 for keyword in _SUGGESTION_SUGAR_KEYWORDS if keyword in normalized_foods)
    if sugary_occurrences >= 2:
        insights.append(
            "Consumo frequente de itens açucarados detectado. Reduza ativamente doces e bebidas açucaradas nesta semana."
        )

    has_vegetables = any(keyword in normalized_foods for keyword in _SUGGESTION_VEGETABLE_KEYWORDS)
    if not has_vegetables:
        insights.append(
            "Não há indícios de verduras/legumes nas refeições registradas. Defina regra mínima: incluir vegetais no almoço e no jantar todos os dias."
        )

    if not insights:
        insights.append("Boa consistência alimentar. Mantenha disciplina e preserve variedade nutricional diariamente.")
    return insights


def analyze_monthly_expenses(arguments, context):
    month_value = str(arguments.get("month", "")).strip()
    day_value = str(
        arguments.get(
            "date",
            arguments.get("day", arguments.get("expense_date", "")),
        )
    ).strip()
    limit = int(arguments.get("limit", 50))
    limit = min(max(limit, 1), 300)

    target_day = None
    if day_value:
        try:
            target_day = datetime.date.fromisoformat(day_value)
        except ValueError as error:
            raise ValueError("date must follow YYYY-MM-DD") from error

    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
        target_date = datetime.date.fromisoformat(f"{month_value}-01")
    elif target_day is not None:
        target_date = target_day.replace(day=1)
    else:
        target_date = today_in_configured_timezone().replace(day=1)
    if target_day is not None and target_day.strftime("%Y-%m") != target_date.strftime("%Y-%m"):
        raise ValueError("date must belong to the requested month")
    month_key = target_date.strftime("%Y-%m")
    month_start, month_end = _month_bounds(target_date)
    expenses = notion_connector.collect_expenses_from_expenses_db(
        start_date=month_start.isoformat(),
        end_date=month_end.isoformat(),
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )

    selected_expenses = expenses
    if target_day is not None:
        target_day_key = target_day.isoformat()
        selected_expenses = [
            expense
            for expense in expenses
            if str(expense.get("date", "")).strip()[:10] == target_day_key
        ]

    if not expenses:
        return {
            "month": month_key,
            "total_spent": 0.0,
            "expenses_count": 0,
            "breakdown_by_category": [],
            "top_expense": None,
            "daily_breakdown": [],
            "applied_date_filter": target_day.isoformat() if target_day is not None else None,
            "selected_total_spent": 0.0,
            "selected_expenses_count": 0,
            "selected_top_expense": None,
            "returned_count": 0,
            "expenses": [],
        }

    total_spent = round(sum(expense["amount"] for expense in expenses), 2)
    by_category = {}
    by_day = {}
    for expense in expenses:
        category = expense["category"]
        by_category[category] = by_category.get(category, 0.0) + expense["amount"]
        day_key = str(expense.get("date", "")).strip()[:10]
        by_day.setdefault(day_key, {"date": day_key, "total": 0.0, "count": 0})
        by_day[day_key]["total"] += float(expense.get("amount", 0.0))
        by_day[day_key]["count"] += 1
    breakdown = [
        {"category": category, "total": round(amount, 2)}
        for category, amount in sorted(by_category.items(), key=lambda item: item[1], reverse=True)
    ]
    daily_breakdown = [
        {
            "date": payload["date"],
            "total": round(payload["total"], 2),
            "count": payload["count"],
        }
        for payload in sorted(by_day.values(), key=lambda item: item["date"])
    ]
    top_expense = max(expenses, key=lambda expense: expense["amount"]) if expenses else None
    if top_expense:
        top_expense = {
            "date": top_expense["date"],
            "amount": round(top_expense["amount"], 2),
            "category": top_expense["category"],
            "description": top_expense["description"],
        }

    selected_total_spent = round(
        sum(float(expense.get("amount", 0.0)) for expense in selected_expenses),
        2,
    )
    selected_top_expense = (
        max(selected_expenses, key=lambda expense: float(expense.get("amount", 0.0)))
        if selected_expenses else None
    )
    if selected_top_expense:
        selected_top_expense = {
            "date": selected_top_expense["date"],
            "amount": round(float(selected_top_expense["amount"]), 2),
            "category": selected_top_expense["category"],
            "description": selected_top_expense["description"],
        }
    sorted_selected_expenses = sorted(
        selected_expenses,
        key=lambda expense: (
            str(expense.get("date", "")).strip(),
            -float(expense.get("amount", 0.0)),
        ),
    )
    returned_expenses = sorted_selected_expenses[:limit]

    return {
        "month": month_key,
        "total_spent": total_spent,
        "expenses_count": len(expenses),
        "breakdown_by_category": breakdown,
        "top_expense": top_expense,
        "daily_breakdown": daily_breakdown,
        "applied_date_filter": target_day.isoformat() if target_day is not None else None,
        "selected_total_spent": selected_total_spent,
        "selected_expenses_count": len(selected_expenses),
        "selected_top_expense": selected_top_expense,
        "returned_count": len(returned_expenses),
        "expenses": returned_expenses,
    }


def list_unpaid_monthly_bills(arguments, context):
    month_value = str(arguments.get("month", "")).strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
        target_date = datetime.date.fromisoformat(f"{month_value}-01")
    else:
        target_date = today_in_configured_timezone().replace(day=1)
    limit = int(arguments.get("limit", 30))
    limit = min(max(limit, 1), 100)

    month_start, month_end = _month_bounds(target_date)
    bills = notion_connector.collect_monthly_bills_from_database(
        start_date=month_start.isoformat(),
        end_date=month_end.isoformat(),
        unpaid_only=True,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "month": target_date.strftime("%Y-%m"),
        "total": len(bills),
        "returned": min(len(bills), limit),
        "bills": bills[:limit],
    }


def mark_monthly_bill_as_paid(arguments, context):
    page_id = str(arguments.get("page_id", "")).strip()
    if not page_id:
        raise ValueError("page_id is required")

    paid_amount = arguments.get("paid_amount")
    normalized_paid_amount = None
    if paid_amount is not None:
        normalized_paid_amount = float(str(paid_amount).replace(",", "."))
        if normalized_paid_amount < 0:
            raise ValueError("paid_amount must be >= 0")

    payment_date = str(arguments.get("payment_date", "")).strip() or None
    if payment_date:
        datetime.date.fromisoformat(payment_date)

    result = notion_connector.update_monthly_bill_payment(
        page_id=page_id,
        paid=True,
        paid_amount=normalized_paid_amount,
        payment_date=payment_date,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    return {
        "status": "updated",
        "bill_id": result.get("id"),
        "paid": result.get("paid", True),
        "paid_amount": result.get("paid_amount"),
        "payment_date": result.get("payment_date"),
    }


def analyze_monthly_bills(arguments, context):
    month_value = str(arguments.get("month", "")).strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
        target_date = datetime.date.fromisoformat(f"{month_value}-01")
    else:
        target_date = today_in_configured_timezone().replace(day=1)

    month_start, month_end = _month_bounds(target_date)
    bills = notion_connector.collect_monthly_bills_from_database(
        start_date=month_start.isoformat(),
        end_date=month_end.isoformat(),
        unpaid_only=False,
        project_logger=context.project_logger,
        user_id=context.user_id,
        credential_store=context.user_credential_store,
    )
    if not bills:
        return {
            "month": target_date.strftime("%Y-%m"),
            "total_bills": 0,
            "paid_count": 0,
            "unpaid_count": 0,
            "total_budget": 0.0,
            "total_paid_amount": 0.0,
            "pending_budget": 0.0,
            "breakdown_by_category": [],
        }

    total_budget = round(sum(bill["budget"] for bill in bills), 2)
    total_paid_amount = round(sum(bill["paid_amount"] for bill in bills), 2)
    paid_count = sum(1 for bill in bills if bill["paid"])
    unpaid_count = len(bills) - paid_count
    pending_budget = round(sum(bill["budget"] for bill in bills if not bill["paid"]), 2)

    by_category = {}
    for bill in bills:
        category = bill["category"]
        category_values = by_category.setdefault(
            category,
            {"category": category, "total_budget": 0.0, "total_paid": 0.0, "unpaid_count": 0},
        )
        category_values["total_budget"] += bill["budget"]
        category_values["total_paid"] += bill["paid_amount"]
        if not bill["paid"]:
            category_values["unpaid_count"] += 1
    breakdown_by_category = [
        {
            "category": item["category"],
            "total_budget": round(item["total_budget"], 2),
            "total_paid": round(item["total_paid"], 2),
            "unpaid_count": item["unpaid_count"],
        }
        for item in sorted(by_category.values(), key=lambda value: value["total_budget"], reverse=True)
    ]

    return {
        "month": target_date.strftime("%Y-%m"),
        "total_bills": len(bills),
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "total_budget": total_budget,
        "total_paid_amount": total_paid_amount,
        "pending_budget": pending_budget,
        "breakdown_by_category": breakdown_by_category,
    }
