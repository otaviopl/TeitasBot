from __future__ import annotations

import datetime
import re

from assistant_connector.tools.health_tools import _health_store
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
    month_value = str(arguments.get("month", "")).strip()
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
    month_value = str(arguments.get("month", "")).strip()
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
    month_value = str(arguments.get("month", "")).strip()
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
