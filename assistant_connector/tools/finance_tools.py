from __future__ import annotations

import calendar
import datetime
import re

from assistant_connector.tools.health_tools import _health_store, _read_optional_boolean
from utils.timezone_utils import today_in_configured_timezone, today_iso_in_configured_timezone

_CATEGORY_KEYWORDS = {
    "Alimentação": ("mercado", "restaurante", "ifood", "lanche", "almoço", "jantar", "cafe"),
    "Transporte": ("uber", "99", "taxi", "ônibus", "onibus", "combustivel", "gasolina", "pedagio"),
    "Moradia": ("aluguel", "condominio", "energia", "luz", "agua", "internet", "gás", "gas"),
    "Saúde": ("farmacia", "remedio", "consulta", "exame", "plano de saude", "hospital"),
    "Lazer": ("cinema", "streaming", "show", "viagem", "bar"),
}
_CATEGORY_ALIASES = {
    "alimentacao": "Alimentação", "alimentação": "Alimentação", "mercado": "Alimentação",
    "transporte": "Transporte", "mobilidade": "Transporte",
    "moradia": "Moradia", "casa": "Moradia",
    "saude": "Saúde", "saúde": "Saúde",
    "lazer": "Lazer",
    "outros": "Outros",
}


def _infer_expense_category(description: str) -> str:
    normalized = description.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in normalized for kw in keywords):
            return category
    return "Outros"


def _normalize_expense_category(raw_category, description: str) -> str:
    category = str(raw_category or "").strip()
    if not category:
        return _infer_expense_category(description)
    normalized = category.lower()
    return _CATEGORY_ALIASES.get(normalized, category.title())


def _month_bounds(target_date: datetime.date):
    month_start = target_date.replace(day=1)
    if month_start.month == 12:
        next_month = datetime.date(month_start.year + 1, 1, 1)
    else:
        next_month = datetime.date(month_start.year, month_start.month + 1, 1)
    return month_start, (next_month - datetime.timedelta(days=1))


def _shift_months(target_date: datetime.date, delta_months: int) -> datetime.date:
    total_months = (target_date.year * 12 + (target_date.month - 1)) + delta_months
    year = total_months // 12
    month = total_months % 12 + 1
    return datetime.date(year, month, 1)


def _months_until(target_date: datetime.date, reference_date: datetime.date) -> float:
    delta_days = (target_date - reference_date).days
    if delta_days <= 0:
        return 0.0
    return max(delta_days / 30.4375, 0.0)


def _currency_or_none(value):
    if value is None:
        return None
    return round(float(value), 2)


# ---------------------------------------------------------------------------
# Expense tools
# ---------------------------------------------------------------------------

def register_expense(arguments, context):
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
    expense = _health_store.create_expense(
        user_id=context.user_id,
        name=description,
        amount=amount,
        category=category,
        description=description,
        date=expense_date.isoformat(),
    )
    return {
        "status": "created",
        "expense_id": expense["id"],
        "expense": {
            "date": expense["date"],
            "amount": expense["amount"],
            "category": expense["category"],
            "description": expense["description"],
        },
    }


def analyze_expenses(arguments, context):
    month_value = str(arguments.get("month") or "").strip()
    day_value = str(
        arguments.get("date", arguments.get("day", arguments.get("expense_date", "")))
    ).strip()
    limit = min(max(int(arguments.get("limit", 50)), 1), 300)

    target_day = None
    if day_value:
        try:
            target_day = datetime.date.fromisoformat(day_value)
        except ValueError as e:
            raise ValueError("date must follow YYYY-MM-DD") from e

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

    expenses = _health_store.list_expenses_by_date_range(
        user_id=context.user_id,
        start_date=month_start.isoformat(),
        end_date=month_end.isoformat(),
    )

    selected_expenses = expenses
    if target_day is not None:
        target_day_key = target_day.isoformat()
        selected_expenses = [
            e for e in expenses if str(e.get("date", "")).strip()[:10] == target_day_key
        ]

    if not expenses:
        return {
            "month": month_key,
            "total_spent": 0.0,
            "expenses_count": 0,
            "breakdown_by_category": [],
            "top_expense": None,
            "daily_breakdown": [],
            "applied_date_filter": target_day.isoformat() if target_day else None,
            "selected_total_spent": 0.0,
            "selected_expenses_count": 0,
            "selected_top_expense": None,
            "returned_count": 0,
            "expenses": [],
        }

    total_spent = round(sum(e["amount"] for e in expenses), 2)
    by_category: dict = {}
    by_day: dict = {}
    for e in expenses:
        cat = e["category"]
        by_category[cat] = by_category.get(cat, 0.0) + e["amount"]
        dk = str(e.get("date", "")).strip()[:10]
        by_day.setdefault(dk, {"date": dk, "total": 0.0, "count": 0})
        by_day[dk]["total"] += float(e.get("amount", 0.0))
        by_day[dk]["count"] += 1

    breakdown = [
        {"category": cat, "total": round(amt, 2)}
        for cat, amt in sorted(by_category.items(), key=lambda x: x[1], reverse=True)
    ]
    daily_breakdown = [
        {"date": v["date"], "total": round(v["total"], 2), "count": v["count"]}
        for v in sorted(by_day.values(), key=lambda x: x["date"])
    ]
    top_expense = max(expenses, key=lambda e: e["amount"]) if expenses else None
    if top_expense:
        top_expense = {
            "date": top_expense["date"],
            "amount": round(top_expense["amount"], 2),
            "category": top_expense["category"],
            "description": top_expense.get("description", ""),
        }

    selected_total = round(sum(float(e.get("amount", 0.0)) for e in selected_expenses), 2)
    selected_top = (
        max(selected_expenses, key=lambda e: float(e.get("amount", 0.0)))
        if selected_expenses else None
    )
    if selected_top:
        selected_top = {
            "date": selected_top["date"],
            "amount": round(float(selected_top["amount"]), 2),
            "category": selected_top["category"],
            "description": selected_top.get("description", ""),
        }

    sorted_selected = sorted(
        selected_expenses,
        key=lambda e: (str(e.get("date", "")).strip(), -float(e.get("amount", 0.0))),
    )
    returned = sorted_selected[:limit]

    return {
        "month": month_key,
        "total_spent": total_spent,
        "expenses_count": len(expenses),
        "breakdown_by_category": breakdown,
        "top_expense": top_expense,
        "daily_breakdown": daily_breakdown,
        "applied_date_filter": target_day.isoformat() if target_day else None,
        "selected_total_spent": selected_total,
        "selected_expenses_count": len(selected_expenses),
        "selected_top_expense": selected_top,
        "returned_count": len(returned),
        "expenses": returned,
    }


# ---------------------------------------------------------------------------
# Bills tools
# ---------------------------------------------------------------------------

def list_bills(arguments, context):
    month_value = str(arguments.get("month") or "").strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
        target_date = datetime.date.fromisoformat(f"{month_value}-01")
    else:
        target_date = today_in_configured_timezone().replace(day=1)
    limit = min(max(int(arguments.get("limit", 30)), 1), 100)
    unpaid_only_raw = arguments.get("unpaid_only", True)
    if isinstance(unpaid_only_raw, str):
        unpaid_only = unpaid_only_raw.strip().lower() not in {"0", "false", "no", "nao"}
    else:
        unpaid_only = bool(unpaid_only_raw)

    reference_month = target_date.strftime("%Y-%m")
    bills = _health_store.list_bills_by_month(
        user_id=context.user_id,
        reference_month=reference_month,
        unpaid_only=unpaid_only,
    )
    return {
        "month": reference_month,
        "total": len(bills),
        "returned": min(len(bills), limit),
        "bills": bills[:limit],
    }


def pay_bill(arguments, context):
    bill_id = str(arguments.get("bill_id", "")).strip()
    if not bill_id:
        raise ValueError("bill_id is required")

    paid_amount = arguments.get("paid_amount")
    normalized_paid_amount = None
    if paid_amount is not None:
        normalized_paid_amount = float(str(paid_amount).replace(",", "."))
        if normalized_paid_amount < 0:
            raise ValueError("paid_amount must be >= 0")

    result = _health_store.update_bill_payment(
        user_id=context.user_id,
        bill_id=bill_id,
        paid=True,
        paid_amount=normalized_paid_amount,
    )
    return {
        "status": "updated",
        "bill_id": result["id"],
        "paid": result["paid"],
        "paid_amount": result["paid_amount"],
    }


def analyze_bills(arguments, context):
    month_value = str(arguments.get("month") or "").strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
        target_date = datetime.date.fromisoformat(f"{month_value}-01")
    else:
        target_date = today_in_configured_timezone().replace(day=1)

    reference_month = target_date.strftime("%Y-%m")
    bills = _health_store.list_bills_by_month(
        user_id=context.user_id,
        reference_month=reference_month,
        unpaid_only=False,
    )
    if not bills:
        return {
            "month": reference_month,
            "total_bills": 0,
            "paid_count": 0,
            "unpaid_count": 0,
            "total_budget": 0.0,
            "total_paid_amount": 0.0,
            "pending_budget": 0.0,
            "breakdown_by_category": [],
        }

    total_budget = round(sum(b["budget"] for b in bills), 2)
    total_paid_amount = round(sum(b["paid_amount"] for b in bills), 2)
    paid_count = sum(1 for b in bills if b["paid"])
    unpaid_count = len(bills) - paid_count
    pending_budget = round(sum(b["budget"] for b in bills if not b["paid"]), 2)

    by_category: dict = {}
    for b in bills:
        cat = b["category"]
        cv = by_category.setdefault(cat, {"category": cat, "total_budget": 0.0, "total_paid": 0.0, "unpaid_count": 0})
        cv["total_budget"] += b["budget"]
        cv["total_paid"] += b["paid_amount"]
        if not b["paid"]:
            cv["unpaid_count"] += 1

    breakdown_by_category = [
        {
            "category": v["category"],
            "total_budget": round(v["total_budget"], 2),
            "total_paid": round(v["total_paid"], 2),
            "unpaid_count": v["unpaid_count"],
        }
        for v in sorted(by_category.values(), key=lambda x: x["total_budget"], reverse=True)
    ]

    return {
        "month": reference_month,
        "total_bills": len(bills),
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "total_budget": total_budget,
        "total_paid_amount": total_paid_amount,
        "pending_budget": pending_budget,
        "breakdown_by_category": breakdown_by_category,
    }


# ---------------------------------------------------------------------------
# Financial goals
# ---------------------------------------------------------------------------

def register_financial_goal(arguments, context):
    title = str(arguments.get("title", "")).strip()
    goal_type = str(arguments.get("goal_type", "")).strip().lower()
    target_date = str(arguments.get("target_date", "")).strip() or None
    if target_date:
        datetime.date.fromisoformat(target_date)

    result = _health_store.create_financial_goal(
        user_id=context.user_id,
        title=title,
        goal_type=goal_type,
        target_amount=_read_optional_float(arguments.get("target_amount")),
        current_amount=_read_optional_float(arguments.get("current_amount")) or 0.0,
        monthly_contribution=_read_optional_float(arguments.get("monthly_contribution")),
        monthly_limit=_read_optional_float(arguments.get("monthly_limit")),
        target_date=target_date,
    )
    return {"status": "created", "goal": result}


def list_financial_goals(arguments, context):
    goals = _health_store.list_financial_goals(user_id=context.user_id)
    return {"total": len(goals), "goals": goals}


def edit_financial_goal(arguments, context):
    goal_id = str(arguments.get("goal_id", "")).strip()
    if not goal_id:
        raise ValueError("goal_id is required")

    kwargs = {}
    for field in ("title", "target_amount", "current_amount", "monthly_contribution", "monthly_limit"):
        if field in arguments:
            if field == "title":
                kwargs[field] = str(arguments.get(field, "")).strip()
            else:
                kwargs[field] = _read_optional_float(arguments.get(field))
    if "target_date" in arguments:
        target_date = str(arguments.get("target_date", "")).strip()
        if target_date:
            datetime.date.fromisoformat(target_date)
            kwargs["target_date"] = target_date
    if not kwargs:
        raise ValueError("At least one field to update is required")

    result = _health_store.update_financial_goal(
        user_id=context.user_id,
        goal_id=goal_id,
        **kwargs,
    )
    return {"status": "updated", "goal": result}


def delete_financial_goal(arguments, context):
    goal_id = str(arguments.get("goal_id", "")).strip()
    if not goal_id:
        raise ValueError("goal_id is required")
    deleted = _health_store.delete_financial_goal(user_id=context.user_id, goal_id=goal_id)
    if not deleted:
        raise ValueError(f"Financial goal {goal_id!r} not found")
    return {"status": "deleted", "goal_id": goal_id}


def analyze_financial_goals(arguments, context):
    month_value = str(arguments.get("month") or "").strip()
    return {
        "month": _normalize_analysis_month(month_value),
        "goals": build_financial_goal_analysis(
            user_id=context.user_id,
            month=month_value or None,
            store=_health_store,
        ),
    }


def build_financial_goal_analysis(*, user_id: str, month: str | None = None, store=None) -> list[dict]:
    target_month = _normalize_analysis_month(month)
    target_date = datetime.date.fromisoformat(f"{target_month}-01")
    reference_today = today_in_configured_timezone()
    goals = (store or _health_store).list_financial_goals(user_id=user_id)
    if not goals:
        return []

    expenses_this_month = (store or _health_store).list_expenses_by_month(user_id=user_id, month=target_month)
    total_spent_this_month = round(sum(float(item.get("amount", 0.0)) for item in expenses_this_month), 2)

    recent_months = [_shift_months(target_date, -offset).strftime("%Y-%m") for offset in range(0, 3)]
    recent_totals = []
    for month_key in recent_months:
        month_expenses = (store or _health_store).list_expenses_by_month(user_id=user_id, month=month_key)
        recent_totals.append(round(sum(float(item.get("amount", 0.0)) for item in month_expenses), 2))
    average_monthly_spend = round(sum(recent_totals) / len(recent_totals), 2) if recent_totals else 0.0

    analyses = []
    for goal in goals:
        if goal["goal_type"] == "savings":
            analyses.append(
                _analyze_savings_goal(
                    goal=goal,
                    reference_today=reference_today,
                    average_monthly_spend=average_monthly_spend,
                )
            )
        else:
            analyses.append(
                _analyze_spending_goal(
                    goal=goal,
                    month_key=target_month,
                    target_date=target_date,
                    reference_today=reference_today,
                    total_spent_this_month=total_spent_this_month,
                )
            )
    return analyses


def _analyze_savings_goal(*, goal: dict, reference_today: datetime.date, average_monthly_spend: float) -> dict:
    target_amount = float(goal.get("target_amount") or 0.0)
    current_amount = float(goal.get("current_amount") or 0.0)
    remaining_amount = round(max(target_amount - current_amount, 0.0), 2)
    target_date = datetime.date.fromisoformat(goal["target_date"]) if goal.get("target_date") else None
    months_left = _months_until(target_date, reference_today) if target_date else 0.0
    required_monthly = round((remaining_amount / months_left), 2) if months_left > 0 else remaining_amount
    planned_monthly = _currency_or_none(goal.get("monthly_contribution")) or 0.0
    projected_completion_date = None
    projected_amount_at_target = current_amount
    if planned_monthly > 0 and target_date is not None:
        projected_amount_at_target = round(current_amount + (planned_monthly * months_left), 2)
        if remaining_amount > 0:
            months_to_finish = remaining_amount / planned_monthly
            projected_completion_date = (
                reference_today + datetime.timedelta(days=round(months_to_finish * 30.4375))
            ).isoformat()

    progress_percent = round(min((current_amount / target_amount) * 100, 100), 1) if target_amount > 0 else 0.0
    if remaining_amount <= 0:
        status = "achieved"
    elif planned_monthly <= 0:
        status = "needs_plan"
    elif target_date is not None and projected_completion_date and projected_completion_date <= target_date.isoformat():
        status = "on_track"
    else:
        status = "at_risk"

    return {
        **goal,
        "status": status,
        "progress_percent": progress_percent,
        "remaining_amount": remaining_amount,
        "months_left": round(months_left, 1),
        "required_monthly_saving": required_monthly,
        "planned_monthly_saving": round(planned_monthly, 2),
        "projected_amount_at_target_date": round(projected_amount_at_target, 2),
        "projected_completion_date": projected_completion_date,
        "average_monthly_spend": average_monthly_spend,
        "monthly_gap": round(planned_monthly - required_monthly, 2),
    }


def _analyze_spending_goal(
    *,
    goal: dict,
    month_key: str,
    target_date: datetime.date,
    reference_today: datetime.date,
    total_spent_this_month: float,
) -> dict:
    monthly_limit = float(goal.get("monthly_limit") or 0.0)
    year, month = target_date.year, target_date.month
    days_in_month = calendar.monthrange(year, month)[1]
    is_current_month = reference_today.year == year and reference_today.month == month
    elapsed_days = reference_today.day if is_current_month else days_in_month
    projected_monthly_spend = round(
        total_spent_this_month if elapsed_days <= 0 else (total_spent_this_month / elapsed_days) * days_in_month,
        2,
    )
    remaining_budget = round(monthly_limit - total_spent_this_month, 2)
    projected_gap = round(monthly_limit - projected_monthly_spend, 2)
    status = "on_track" if projected_monthly_spend <= monthly_limit else "at_risk"
    if not is_current_month and total_spent_this_month <= monthly_limit:
        status = "achieved"

    progress_percent = round(min((total_spent_this_month / monthly_limit) * 100, 100), 1) if monthly_limit > 0 else 0.0
    return {
        **goal,
        "status": status,
        "analysis_month": month_key,
        "progress_percent": progress_percent,
        "spent_this_month": round(total_spent_this_month, 2),
        "remaining_budget": remaining_budget,
        "projected_monthly_spend": projected_monthly_spend,
        "projected_gap": projected_gap,
    }


def _normalize_analysis_month(month_value: str | None) -> str:
    clean_value = str(month_value or "").strip()
    if clean_value:
        if not re.fullmatch(r"\d{4}-\d{2}", clean_value):
            raise ValueError("month must follow YYYY-MM")
        return clean_value
    return today_in_configured_timezone().strftime("%Y-%m")


def _read_optional_float(value):
    if value is None or value == "":
        return None
    return float(str(value).replace(",", "."))


# ---------------------------------------------------------------------------
# Expenses: list, edit, delete
# ---------------------------------------------------------------------------

def list_expenses(arguments, context):
    month_value = str(arguments.get("month") or "").strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
    else:
        month_value = today_in_configured_timezone().strftime("%Y-%m")
    expenses = _health_store.list_expenses_by_month(
        user_id=context.user_id,
        month=month_value,
    )
    return {"month": month_value, "total": len(expenses), "expenses": expenses}


def edit_expense(arguments, context):
    expense_id = str(arguments.get("expense_id", "")).strip()
    if not expense_id:
        raise ValueError("expense_id is required")
    kwargs = {}
    if "description" in arguments or "name" in arguments:
        kwargs["name"] = str(arguments.get("description", arguments.get("name", ""))).strip()
    if "amount" in arguments:
        kwargs["amount"] = float(str(arguments["amount"]).replace(",", "."))
    if "category" in arguments:
        desc = kwargs.get("name", "")
        kwargs["category"] = _normalize_expense_category(arguments["category"], desc)
    if "expense_date" in arguments or "date" in arguments:
        d = str(arguments.get("expense_date", arguments.get("date", ""))).strip()
        if d:
            datetime.date.fromisoformat(d[:10])
            kwargs["date"] = d[:10]
    if not kwargs:
        raise ValueError("At least one field to update is required")
    result = _health_store.update_expense(user_id=context.user_id, expense_id=expense_id, **kwargs)
    return {"status": "updated", "expense": result}


def delete_expense(arguments, context):
    expense_id = str(arguments.get("expense_id", "")).strip()
    if not expense_id:
        raise ValueError("expense_id is required")
    deleted = _health_store.delete_expense(user_id=context.user_id, expense_id=expense_id)
    if not deleted:
        raise ValueError(f"Expense {expense_id!r} not found")
    return {"status": "deleted", "expense_id": expense_id}


# ---------------------------------------------------------------------------
# Bills: register, edit, delete
# ---------------------------------------------------------------------------

def register_bill(arguments, context):
    bill_name = str(arguments.get("bill_name", arguments.get("nome", ""))).strip()
    if not bill_name:
        raise ValueError("bill_name is required")
    budget = arguments.get("budget", arguments.get("valor", None))
    if budget is None:
        raise ValueError("budget is required")
    budget = float(str(budget).replace(",", "."))
    if budget <= 0:
        raise ValueError("budget must be > 0")
    raw_cat = str(arguments.get("category", arguments.get("categoria", "Outros"))).strip()
    category = _normalize_expense_category(raw_cat, bill_name)
    due_date = arguments.get("due_date", arguments.get("vencimento"))
    if due_date:
        due_date = str(due_date).strip()[:10]
        datetime.date.fromisoformat(due_date)
    else:
        due_date = None
    ref_month = arguments.get("reference_month", arguments.get("mes"))
    if ref_month:
        ref_month = str(ref_month).strip()[:7]
    else:
        ref_month = today_in_configured_timezone().strftime("%Y-%m")
    result = _health_store.create_bill(
        user_id=context.user_id,
        bill_name=bill_name,
        budget=budget,
        category=category,
        due_date=due_date,
        reference_month=ref_month,
    )
    return {"status": "created", "bill": result}


def edit_bill(arguments, context):
    bill_id = str(arguments.get("bill_id", "")).strip()
    if not bill_id:
        raise ValueError("bill_id is required")
    kwargs = {}
    if "bill_name" in arguments or "nome" in arguments:
        kwargs["bill_name"] = str(arguments.get("bill_name", arguments.get("nome", ""))).strip()
    if "budget" in arguments or "valor" in arguments:
        kwargs["budget"] = float(str(arguments.get("budget", arguments.get("valor", "0"))).replace(",", "."))
    if "category" in arguments or "categoria" in arguments:
        desc = kwargs.get("bill_name", "")
        kwargs["category"] = _normalize_expense_category(
            str(arguments.get("category", arguments.get("categoria", ""))), desc)
    if "due_date" in arguments or "vencimento" in arguments:
        d = str(arguments.get("due_date", arguments.get("vencimento", ""))).strip()
        if d:
            datetime.date.fromisoformat(d[:10])
            kwargs["due_date"] = d[:10]
    if "paid" in arguments:
        kwargs["paid"] = _read_optional_boolean(arguments, "paid")
    if "paid_amount" in arguments:
        kwargs["paid_amount"] = float(str(arguments["paid_amount"]).replace(",", "."))
    if not kwargs:
        raise ValueError("At least one field to update is required")
    result = _health_store.update_bill(user_id=context.user_id, bill_id=bill_id, **kwargs)
    return {"status": "updated", "bill": result}


def delete_bill(arguments, context):
    bill_id = str(arguments.get("bill_id", "")).strip()
    if not bill_id:
        raise ValueError("bill_id is required")
    deleted = _health_store.delete_bill(user_id=context.user_id, bill_id=bill_id)
    if not deleted:
        raise ValueError(f"Bill {bill_id!r} not found")
    return {"status": "deleted", "bill_id": bill_id}


# ---------------------------------------------------------------------------
# Imported expenses analysis
# ---------------------------------------------------------------------------

def analyze_card_expenses(arguments, context):
    """Analyze expenses imported from Nubank credit card CSV (fatura) for a given month."""
    month_value = str(arguments.get("month") or "").strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
        target_date = datetime.date.fromisoformat(f"{month_value}-01")
    else:
        target_date = today_in_configured_timezone().replace(day=1)

    month_start, month_end = _month_bounds(target_date)
    month_key = target_date.strftime("%Y-%m")

    expenses = _health_store.list_card_expenses_by_date_range(
        user_id=context.user_id,
        start_date=month_start.isoformat(),
        end_date=month_end.isoformat(),
    )

    if not expenses:
        return {
            "month": month_key,
            "source": "csv_nubank_card",
            "total_spent": 0.0,
            "count": 0,
            "breakdown_by_category": [],
            "expenses": [],
        }

    total_spent = round(sum(e["amount"] for e in expenses), 2)
    by_category: dict = {}
    for e in expenses:
        cat = e["category"]
        by_category[cat] = by_category.get(cat, 0.0) + e["amount"]

    breakdown = [
        {"category": cat, "total": round(amt, 2)}
        for cat, amt in sorted(by_category.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "month": month_key,
        "source": "csv_nubank_card",
        "total_spent": total_spent,
        "count": len(expenses),
        "breakdown_by_category": breakdown,
        "expenses": expenses,
    }


def analyze_imported_expenses(arguments, context):
    """Analyze expenses imported via Nubank CSV for a given month or date range."""
    month_value = str(arguments.get("month") or "").strip()
    if month_value:
        if not re.fullmatch(r"\d{4}-\d{2}", month_value):
            raise ValueError("month must follow YYYY-MM")
        target_date = datetime.date.fromisoformat(f"{month_value}-01")
    else:
        target_date = today_in_configured_timezone().replace(day=1)

    month_start, month_end = _month_bounds(target_date)
    month_key = target_date.strftime("%Y-%m")

    expenses = _health_store.list_imported_expenses_by_date_range(
        user_id=context.user_id,
        start_date=month_start.isoformat(),
        end_date=month_end.isoformat(),
    )

    if not expenses:
        return {
            "month": month_key,
            "source": "csv_nubank",
            "total_spent": 0.0,
            "count": 0,
            "breakdown_by_category": [],
            "expenses": [],
        }

    total_spent = round(sum(e["amount"] for e in expenses), 2)
    by_category: dict = {}
    for e in expenses:
        cat = e["category"]
        by_category[cat] = by_category.get(cat, 0.0) + e["amount"]

    breakdown = [
        {"category": cat, "total": round(amt, 2)}
        for cat, amt in sorted(by_category.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "month": month_key,
        "source": "csv_nubank",
        "total_spent": total_spent,
        "count": len(expenses),
        "breakdown_by_category": breakdown,
        "expenses": expenses,
    }
