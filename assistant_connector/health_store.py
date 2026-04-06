from __future__ import annotations

import datetime
import os
import re
import sqlite3
import threading
import uuid
from typing import Optional


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Quantity normalization (extracted from notion_connector)
# ---------------------------------------------------------------------------

_MEAL_UNIT_ALIASES = {
    "g": "g", "grama": "g", "gramas": "g", "gr": "g",
    "kg": "kg", "quilo": "kg", "quilos": "kg",
    "ml": "ml", "mililitro": "ml", "mililitros": "ml",
    "l": "l", "litro": "l", "litros": "l",
    "un": "unit", "und": "unit", "unidade": "unit", "unidades": "unit",
    "porcao": "portion", "porcoes": "portion", "porcaoes": "portion",
    "porcao(s)": "portion",
    "xicara": "cup", "xicaras": "cup",
    "colher": "tbsp", "colher de sopa": "tbsp", "colheres de sopa": "tbsp",
    "colher de cha": "tsp", "colheres de cha": "tsp",
}


def _normalize_text_for_lookup(value: str) -> str:
    text = str(value or "").strip().lower()
    replacements = str.maketrans({
        "á": "a", "à": "a", "â": "a", "ã": "a", "ä": "a",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "í": "i", "ì": "i", "î": "i", "ï": "i",
        "ó": "o", "ò": "o", "ô": "o", "õ": "o", "ö": "o",
        "ú": "u", "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
    })
    text = text.translate(replacements)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_first_float(raw_value) -> Optional[float]:
    match = re.search(r"(-?[0-9]+(?:[.,][0-9]+)?)", str(raw_value or ""))
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _normalize_quantity_unit(raw_unit: str) -> str:
    normalized = _normalize_text_for_lookup(raw_unit)
    if not normalized:
        return "unit"
    for alias, canonical in _MEAL_UNIT_ALIASES.items():
        if normalized == alias or normalized.startswith(f"{alias} "):
            return canonical
    return normalized.split(" ")[0]


def parse_quantity_details(quantity: str) -> dict:
    """Parse a quantity string like '150 g' into amount and unit."""
    quantity_text = str(quantity or "").strip()
    if not quantity_text:
        raise ValueError("quantity is required")
    amount = _extract_first_float(quantity_text)
    if amount is None:
        raise ValueError("quantity must include a numeric value")
    if amount <= 0:
        raise ValueError("quantity must be greater than zero")
    unit_match = re.search(r"[-+]?[0-9]+(?:[.,][0-9]+)?\s*([^\d].*)?$", quantity_text)
    raw_unit = unit_match.group(1).strip() if unit_match and unit_match.group(1) else ""
    unit = _normalize_quantity_unit(raw_unit)
    return {"raw": quantity_text, "amount": amount, "unit": unit}


def normalize_quantity(quantity_details: dict) -> dict:
    """Normalize to base unit: kg→g, l→ml."""
    amount = float(quantity_details["amount"])
    unit = quantity_details["unit"]
    if unit == "kg":
        amount, unit = amount * 1000.0, "g"
    elif unit == "l":
        amount, unit = amount * 1000.0, "ml"
    if amount <= 0:
        raise ValueError("quantity must be greater than zero")
    return {"amount": round(amount, 2), "unit": unit}


# ---------------------------------------------------------------------------
# HealthStore
# ---------------------------------------------------------------------------

class HealthStore:
    """Thread-safe SQLite store for tasks, meals, exercises, expenses and bills."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "assistant_memory.sqlite3")
            )
        self._db_path = os.path.abspath(db_path)
        self._lock = threading.Lock()
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_tasks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    project TEXT NOT NULL DEFAULT 'Pessoal',
                    due_date TEXT,
                    tags TEXT NOT NULL DEFAULT '[]',
                    done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_health_tasks_user
                    ON health_tasks (user_id, done, due_date)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_meals (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    food TEXT NOT NULL,
                    meal_type TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    normalized_amount REAL,
                    normalized_unit TEXT,
                    calories REAL NOT NULL,
                    date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_health_meals_user_date
                    ON health_meals (user_id, date)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_exercises (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    calories REAL NOT NULL,
                    date TEXT NOT NULL,
                    observations TEXT NOT NULL DEFAULT '',
                    done INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_health_exercises_user_date
                    ON health_exercises (user_id, date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_health_exercises_user_activity
                    ON health_exercises (user_id, activity, date)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS financial_expenses (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Outros',
                    description TEXT NOT NULL DEFAULT '',
                    date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_financial_expenses_user_date
                    ON financial_expenses (user_id, date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_financial_expenses_user_cat
                    ON financial_expenses (user_id, category, date)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS financial_bills (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    bill_name TEXT NOT NULL,
                    budget REAL NOT NULL,
                    paid_amount REAL NOT NULL DEFAULT 0,
                    paid INTEGER NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT 'Outros',
                    due_date TEXT,
                    reference_month TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_financial_bills_user_month
                    ON financial_bills (user_id, reference_month)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_financial_bills_user_paid
                    ON financial_bills (user_id, paid, reference_month)
            """)

    # ---- Tasks ----

    def create_task(
        self,
        user_id: str,
        task_name: str,
        project: str = "Pessoal",
        due_date: Optional[str] = None,
        tags: Optional[list] = None,
    ) -> dict:
        import json
        task_id = uuid.uuid4().hex
        now = _utc_now_iso()
        clean_tags = json.dumps([str(t).strip() for t in (tags or []) if str(t).strip()])
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO health_tasks (id, user_id, task_name, project, due_date, tags, done, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (task_id, user_id, task_name.strip(), project.strip() or "Pessoal",
                 due_date, clean_tags, now, now),
            )
        return self._task_row_to_dict({
            "id": task_id, "user_id": user_id, "task_name": task_name.strip(),
            "project": project.strip() or "Pessoal", "due_date": due_date,
            "tags": clean_tags, "done": 0,
        })

    def list_tasks(
        self,
        user_id: str,
        n_days: int = 0,
        limit: int = 10,
        include_done: bool = False,
    ) -> list[dict]:
        today = datetime.date.today()
        cutoff = (today + datetime.timedelta(days=max(n_days, 0))).isoformat()
        with self._lock, self._connect() as conn:
            if include_done:
                rows = conn.execute(
                    "SELECT * FROM health_tasks WHERE user_id = ? ORDER BY due_date ASC, created_at ASC LIMIT ?",
                    (user_id, max(1, limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM health_tasks
                    WHERE user_id = ? AND done = 0
                      AND (due_date IS NULL OR due_date <= ?)
                    ORDER BY due_date ASC, created_at ASC
                    LIMIT ?
                    """,
                    (user_id, cutoff, max(1, limit)),
                ).fetchall()
        return [self._task_row_to_dict(r) for r in rows]

    def get_task(self, user_id: str, task_id: str) -> Optional[dict]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM health_tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            ).fetchone()
        return self._task_row_to_dict(row) if row else None

    def update_task(self, user_id: str, task_id: str, **kwargs) -> dict:
        import json
        now = _utc_now_iso()
        updates = ["updated_at = ?"]
        params: list = [now]
        if "task_name" in kwargs:
            name = str(kwargs["task_name"]).strip()
            if not name:
                raise ValueError("task_name cannot be empty")
            updates.append("task_name = ?")
            params.append(name)
        if "project" in kwargs:
            updates.append("project = ?")
            params.append(str(kwargs["project"]).strip() or "Pessoal")
        if "due_date" in kwargs:
            updates.append("due_date = ?")
            params.append(kwargs["due_date"])
        if "tags" in kwargs:
            clean_tags = json.dumps([str(t).strip() for t in (kwargs["tags"] or []) if str(t).strip()])
            updates.append("tags = ?")
            params.append(clean_tags)
        if "done" in kwargs:
            updates.append("done = ?")
            params.append(1 if kwargs["done"] else 0)
        params.extend([task_id, user_id])
        sql = f"UPDATE health_tasks SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, params)
            if cursor.rowcount == 0:
                raise ValueError(f"Task {task_id!r} not found")
            row = conn.execute("SELECT * FROM health_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_row_to_dict(row)

    @staticmethod
    def _task_row_to_dict(row) -> dict:
        import json
        r = dict(row)
        try:
            tags = json.loads(r.get("tags") or "[]")
        except (ValueError, TypeError):
            tags = []
        return {
            "id": r["id"],
            "name": r["task_name"],
            "deadline": r.get("due_date"),
            "project": r.get("project", "Pessoal"),
            "tags": tags,
            "done": bool(r.get("done", 0)),
        }

    # ---- Meals ----

    def create_meal(
        self,
        user_id: str,
        food: str,
        meal_type: str,
        quantity: str,
        calories: float,
        date: Optional[str] = None,
        normalized_amount: Optional[float] = None,
        normalized_unit: Optional[str] = None,
    ) -> dict:
        meal_id = uuid.uuid4().hex
        now = _utc_now_iso()
        meal_date = date or datetime.date.today().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO health_meals
                  (id, user_id, food, meal_type, quantity, normalized_amount, normalized_unit, calories, date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (meal_id, user_id, food.strip(), meal_type, quantity, normalized_amount,
                 normalized_unit, calories, meal_date, now),
            )
        return {
            "id": meal_id,
            "food": food.strip(),
            "meal_type": meal_type,
            "quantity": quantity,
            "normalized_amount": normalized_amount,
            "normalized_unit": normalized_unit,
            "calories": calories,
            "date": meal_date,
        }

    def list_meals_by_date_range(
        self,
        user_id: str,
        start_date: str,
        end_date: str,
        limit: int = 300,
    ) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM health_meals
                WHERE user_id = ? AND date >= ? AND date <= ?
                ORDER BY date ASC, created_at ASC
                LIMIT ?
                """,
                (user_id, start_date[:10], end_date[:10], max(1, limit)),
            ).fetchall()
        return [self._meal_row_to_dict(r) for r in rows]

    @staticmethod
    def _meal_row_to_dict(row) -> dict:
        r = dict(row)
        return {
            "id": r["id"],
            "food": r["food"],
            "meal_type": r["meal_type"],
            "quantity": r["quantity"],
            "normalized_amount": r.get("normalized_amount"),
            "normalized_unit": r.get("normalized_unit"),
            "calories": float(r["calories"]),
            "date": r["date"],
        }

    # ---- Exercises ----

    def create_exercise(
        self,
        user_id: str,
        activity: str,
        calories: float,
        date: Optional[str] = None,
        observations: str = "",
        done: Optional[bool] = None,
    ) -> dict:
        exercise_id = uuid.uuid4().hex
        now = _utc_now_iso()
        exercise_date = date or datetime.date.today().isoformat()
        if done is None:
            try:
                done = datetime.date.fromisoformat(exercise_date) <= datetime.date.today()
            except ValueError:
                done = True
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO health_exercises
                  (id, user_id, activity, calories, date, observations, done, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (exercise_id, user_id, activity.strip(), calories, exercise_date,
                 observations or "", 1 if done else 0, now, now),
            )
        return {
            "id": exercise_id,
            "activity": activity.strip(),
            "calories": calories,
            "date": exercise_date,
            "observations": observations or "",
            "done": done,
        }

    def update_exercise(self, user_id: str, exercise_id: str, **kwargs) -> dict:
        now = _utc_now_iso()
        updates = ["updated_at = ?"]
        params: list = [now]
        if "activity" in kwargs:
            a = str(kwargs["activity"]).strip()
            if not a:
                raise ValueError("activity cannot be empty")
            updates.append("activity = ?")
            params.append(a)
        if "calories" in kwargs:
            updates.append("calories = ?")
            params.append(float(kwargs["calories"]))
        if "date" in kwargs:
            updates.append("date = ?")
            params.append(str(kwargs["date"]))
        if "observations" in kwargs:
            updates.append("observations = ?")
            params.append(str(kwargs["observations"] or ""))
        if "done" in kwargs:
            updates.append("done = ?")
            params.append(1 if kwargs["done"] else 0)
        params.extend([exercise_id, user_id])
        sql = f"UPDATE health_exercises SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, params)
            if cursor.rowcount == 0:
                raise ValueError(f"Exercise {exercise_id!r} not found")
            row = conn.execute("SELECT * FROM health_exercises WHERE id = ?", (exercise_id,)).fetchone()
        return self._exercise_row_to_dict(row)

    def list_exercises_by_date_range(
        self,
        user_id: str,
        start_date: str,
        end_date: str,
        limit: int = 300,
    ) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM health_exercises
                WHERE user_id = ? AND date >= ? AND date <= ?
                ORDER BY date ASC, created_at ASC
                LIMIT ?
                """,
                (user_id, start_date[:10], end_date[:10], max(1, limit)),
            ).fetchall()
        return [self._exercise_row_to_dict(r) for r in rows]

    def find_exercise_duplicate(
        self, user_id: str, activity: str, date: str
    ) -> Optional[dict]:
        normalized = activity.strip().lower()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM health_exercises WHERE user_id = ? AND date = ?",
                (user_id, date[:10]),
            ).fetchall()
        for row in rows:
            if str(row["activity"] or "").strip().lower() == normalized:
                return self._exercise_row_to_dict(row)
        return None

    @staticmethod
    def _exercise_row_to_dict(row) -> dict:
        r = dict(row)
        return {
            "id": r["id"],
            "activity": r["activity"],
            "calories": float(r["calories"]),
            "date": r["date"],
            "observations": r.get("observations", ""),
            "done": bool(r.get("done", 1)),
        }

    # ---- Expenses ----

    def create_expense(
        self,
        user_id: str,
        name: str,
        amount: float,
        category: str = "Outros",
        description: str = "",
        date: Optional[str] = None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        expense_id = uuid.uuid4().hex
        now = _utc_now_iso()
        expense_date = date or datetime.date.today().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO financial_expenses
                  (id, user_id, name, amount, category, description, date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (expense_id, user_id, name.strip(), amount,
                 category or "Outros", description or "", expense_date, now),
            )
        return {
            "id": expense_id,
            "name": name.strip(),
            "amount": amount,
            "category": category or "Outros",
            "description": description or "",
            "date": expense_date,
        }

    def list_expenses_by_date_range(
        self,
        user_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM financial_expenses
                WHERE user_id = ? AND date >= ? AND date <= ?
                ORDER BY date ASC, created_at ASC
                """,
                (user_id, start_date[:10], end_date[:10]),
            ).fetchall()
        return [self._expense_row_to_dict(r) for r in rows]

    @staticmethod
    def _expense_row_to_dict(row) -> dict:
        r = dict(row)
        return {
            "id": r["id"],
            "name": r["name"],
            "amount": float(r["amount"]),
            "category": r.get("category", "Outros"),
            "description": r.get("description", ""),
            "date": r["date"],
        }

    # ---- Bills ----

    def create_bill(
        self,
        user_id: str,
        bill_name: str,
        budget: float,
        category: str = "Outros",
        due_date: Optional[str] = None,
        reference_month: Optional[str] = None,
    ) -> dict:
        if budget <= 0:
            raise ValueError("budget must be greater than zero")
        bill_id = uuid.uuid4().hex
        now = _utc_now_iso()
        if reference_month is None:
            reference_month = datetime.date.today().strftime("%Y-%m")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO financial_bills
                  (id, user_id, bill_name, budget, paid_amount, paid, category, due_date, reference_month, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (bill_id, user_id, bill_name.strip(), budget,
                 category or "Outros", due_date, reference_month, now, now),
            )
        return {
            "id": bill_id,
            "bill_name": bill_name.strip(),
            "budget": budget,
            "paid_amount": 0.0,
            "paid": False,
            "category": category or "Outros",
            "due_date": due_date,
            "reference_month": reference_month,
        }

    def list_bills_by_month(
        self,
        user_id: str,
        reference_month: str,
        unpaid_only: bool = False,
    ) -> list[dict]:
        with self._lock, self._connect() as conn:
            if unpaid_only:
                rows = conn.execute(
                    """
                    SELECT * FROM financial_bills
                    WHERE user_id = ? AND reference_month = ? AND paid = 0
                    ORDER BY due_date ASC, created_at ASC
                    """,
                    (user_id, reference_month),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM financial_bills
                    WHERE user_id = ? AND reference_month = ?
                    ORDER BY due_date ASC, created_at ASC
                    """,
                    (user_id, reference_month),
                ).fetchall()
        return [self._bill_row_to_dict(r) for r in rows]

    def update_bill_payment(
        self,
        user_id: str,
        bill_id: str,
        paid: bool,
        paid_amount: Optional[float] = None,
    ) -> dict:
        now = _utc_now_iso()
        updates = ["paid = ?", "updated_at = ?"]
        params: list = [1 if paid else 0, now]
        if paid_amount is not None:
            updates.append("paid_amount = ?")
            params.append(float(paid_amount))
        params.extend([bill_id, user_id])
        sql = f"UPDATE financial_bills SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, params)
            if cursor.rowcount == 0:
                raise ValueError(f"Bill {bill_id!r} not found")
            row = conn.execute("SELECT * FROM financial_bills WHERE id = ?", (bill_id,)).fetchone()
        return self._bill_row_to_dict(row)

    def delete_bill(self, user_id: str, bill_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM financial_bills WHERE id = ? AND user_id = ?",
                (bill_id, user_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _bill_row_to_dict(row) -> dict:
        r = dict(row)
        return {
            "id": r["id"],
            "bill_name": r["bill_name"],
            "budget": float(r["budget"]),
            "paid_amount": float(r.get("paid_amount", 0)),
            "paid": bool(r.get("paid", 0)),
            "category": r.get("category", "Outros"),
            "due_date": r.get("due_date"),
            "reference_month": r["reference_month"],
        }
