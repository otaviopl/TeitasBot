"""Tests for health (meals + exercises) endpoints in web_app.app."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

_ORIGINAL_HTTPX_REQUEST = httpx.Client.request


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    """Set up test environment and reset singletons."""
    db_path = str(tmp_path / "test_health.sqlite3")

    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-health-tests")
    monkeypatch.setenv("WEB_JWT_EXPIRY_HOURS", "1")
    monkeypatch.setenv("ASSISTANT_MEMORY_PATH", db_path)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "_mtPmvrlH22JoC3o3KLUObMoMqYxlXs7aeaNO4kHdoE=")
    monkeypatch.setenv("GOOGLE_OAUTH_CALLBACK_URL", "")

    for var in (
        "NOTION_API_KEY", "NOTION_DATABASE_ID", "NOTION_NOTES_DB_ID",
        "NOTION_EXERCISES_DB_ID", "NOTION_MEALS_DB_ID",
        "NOTION_EXPENSES_DB_ID", "NOTION_MONTHLY_BILLS_DB_ID",
    ):
        monkeypatch.setenv(var, "")

    import web_app.dependencies as deps
    deps._user_store = None
    deps._assistant_service = None
    deps._google_oauth = None
    deps._credential_store = None

    monkeypatch.setattr(httpx.Client, "request", _ORIGINAL_HTTPX_REQUEST)


@pytest.fixture
def client():
    from web_app.app import app
    return TestClient(app)


@pytest.fixture
def auth_token(client):
    from web_app.dependencies import get_user_store
    store = get_user_store()
    store.create_user("healthuser", "testpass123", display_name="Health User")
    res = client.post("/api/auth/login", json={"username": "healthuser", "password": "testpass123"})
    assert res.status_code == 200
    return res.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---- Auth tests ----

class TestHealthRequiresAuth:
    def test_dashboard_401(self, client):
        res = client.get("/api/health/dashboard")
        assert res.status_code in (401, 403)

    def test_weekly_401(self, client):
        res = client.get("/api/health/weekly")
        assert res.status_code in (401, 403)

    def test_create_meal_401(self, client):
        res = client.post("/api/health/meals", json={"food": "x", "meal_type": "ALMOÇO", "quantity": "1g", "estimated_calories": 100})
        assert res.status_code in (401, 403)

    def test_create_exercise_401(self, client):
        res = client.post("/api/health/exercises", json={"activity": "x", "calories": 100})
        assert res.status_code in (401, 403)

    def test_update_exercise_401(self, client):
        res = client.patch("/api/health/exercises/page123", json={"done": True})
        assert res.status_code in (401, 403)


# ---- Dashboard tests ----

class TestHealthDashboard:
    def test_notion_not_configured_returns_empty(self, client, auth_token):
        res = client.get("/api/health/dashboard", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["notion_configured"] is False
        assert data["meals"] == []
        assert data["exercises"] == []
        assert data["totals"]["calories_consumed"] == 0

    def test_dashboard_with_data(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_MEALS_DB_ID", "meals-db")
        monkeypatch.setenv("NOTION_EXERCISES_DB_ID", "exercises-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        mock_meals = [
            {"id": "m1", "food": "Arroz", "meal_type": "ALMOÇO", "quantity": "200g", "calories": 300, "date": "2025-03-06"},
            {"id": "m2", "food": "Frango", "meal_type": "ALMOÇO", "quantity": "150g", "calories": 250, "date": "2025-03-06"},
        ]
        mock_exercises = [
            {"id": "e1", "activity": "Corrida", "calories": 350, "done": True, "date": "2025-03-06"},
        ]

        with patch("notion_connector.notion_connector.collect_meals_from_database", return_value=mock_meals), \
             patch("notion_connector.notion_connector.collect_exercises_from_database", return_value=mock_exercises):
            res = client.get("/api/health/dashboard?date=2025-03-06", headers=_auth(auth_token))

        assert res.status_code == 200
        data = res.json()
        assert data["notion_configured"] is True
        assert len(data["meals"]) == 2
        assert len(data["exercises"]) == 1
        assert data["totals"]["calories_consumed"] == 550.0
        assert data["totals"]["calories_burned"] == 350.0
        assert data["totals"]["balance"] == 200.0

    def test_dashboard_invalid_date(self, client, auth_token):
        res = client.get("/api/health/dashboard?date=not-a-date", headers=_auth(auth_token))
        assert res.status_code == 400

    def test_dashboard_defaults_to_today(self, client, auth_token):
        res = client.get("/api/health/dashboard", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert "date" in data


# ---- Weekly tests ----

class TestHealthWeekly:
    def test_notion_not_configured(self, client, auth_token):
        res = client.get("/api/health/weekly", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["notion_configured"] is False
        assert data["days"] == []

    def test_weekly_returns_7_days(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_MEALS_DB_ID", "meals-db")
        monkeypatch.setenv("NOTION_EXERCISES_DB_ID", "exercises-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        with patch("notion_connector.notion_connector.collect_meals_from_database", return_value=[]), \
             patch("notion_connector.notion_connector.collect_exercises_from_database", return_value=[]):
            res = client.get("/api/health/weekly?end_date=2025-03-06", headers=_auth(auth_token))

        assert res.status_code == 200
        data = res.json()
        assert data["notion_configured"] is True
        assert len(data["days"]) == 7
        assert data["days"][0]["date"] == "2025-02-28"
        assert data["days"][6]["date"] == "2025-03-06"


# ---- Create meal tests ----

class TestCreateMeal:
    def test_missing_fields(self, client, auth_token):
        res = client.post("/api/health/meals", json={}, headers=_auth(auth_token))
        assert res.status_code == 422

    def test_invalid_meal_type(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_MEALS_DB_ID", "meals-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        res = client.post("/api/health/meals", json={
            "food": "Pizza", "meal_type": "INVALID", "quantity": "1 slice", "estimated_calories": 300
        }, headers=_auth(auth_token))
        assert res.status_code == 400
        assert "meal_type" in res.json()["detail"]

    def test_calories_too_high(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_MEALS_DB_ID", "meals-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        res = client.post("/api/health/meals", json={
            "food": "Something", "meal_type": "ALMOÇO", "quantity": "1 kg", "estimated_calories": 99999
        }, headers=_auth(auth_token))
        assert res.status_code == 400

    def test_notion_not_configured(self, client, auth_token):
        res = client.post("/api/health/meals", json={
            "food": "Arroz", "meal_type": "ALMOÇO", "quantity": "200g", "estimated_calories": 300
        }, headers=_auth(auth_token))
        assert res.status_code == 400
        assert "not configured" in res.json()["detail"]

    def test_create_meal_success(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_MEALS_DB_ID", "meals-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        mock_result = {"id": "page1", "food": "Arroz", "calories": 300}
        with patch("notion_connector.notion_connector.create_meal_in_meals_db", return_value=mock_result):
            res = client.post("/api/health/meals", json={
                "food": "Arroz", "meal_type": "ALMOÇO", "quantity": "200g", "estimated_calories": 300
            }, headers=_auth(auth_token))

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "created"
        assert data["meal"]["id"] == "page1"


# ---- Create exercise tests ----

class TestCreateExercise:
    def test_missing_fields(self, client, auth_token):
        res = client.post("/api/health/exercises", json={}, headers=_auth(auth_token))
        assert res.status_code == 422

    def test_notion_not_configured(self, client, auth_token):
        res = client.post("/api/health/exercises", json={
            "activity": "Running", "calories": 350
        }, headers=_auth(auth_token))
        assert res.status_code == 400

    def test_create_exercise_success(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_EXERCISES_DB_ID", "exercises-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        mock_result = {"id": "ex1", "activity": "Running", "calories": 350, "done": True}
        with patch("notion_connector.notion_connector.create_exercise_in_exercises_db", return_value=mock_result):
            res = client.post("/api/health/exercises", json={
                "activity": "Running", "calories": 350, "done": True
            }, headers=_auth(auth_token))

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "created"
        assert data["exercise"]["activity"] == "Running"

    def test_activity_too_long(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_EXERCISES_DB_ID", "exercises-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        res = client.post("/api/health/exercises", json={
            "activity": "A" * 201, "calories": 100
        }, headers=_auth(auth_token))
        assert res.status_code == 400


# ---- Update exercise tests ----

class TestUpdateExercise:
    def test_notion_not_configured(self, client, auth_token):
        res = client.patch("/api/health/exercises/page123", json={"done": True}, headers=_auth(auth_token))
        assert res.status_code == 400

    def test_no_fields_to_update(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_EXERCISES_DB_ID", "exercises-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        res = client.patch("/api/health/exercises/page123", json={}, headers=_auth(auth_token))
        assert res.status_code == 400
        assert "At least one field" in res.json()["detail"]

    def test_update_exercise_success(self, client, auth_token, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "fake-key")
        monkeypatch.setenv("NOTION_EXERCISES_DB_ID", "exercises-db")

        import web_app.dependencies as deps
        deps._credential_store = None

        mock_result = {"id": "page123", "updated_fields": ["done"]}
        with patch("notion_connector.notion_connector.update_exercise_in_exercises_db", return_value=mock_result):
            res = client.patch("/api/health/exercises/page123", json={"done": True}, headers=_auth(auth_token))

        assert res.status_code == 200
        data = res.json()
        assert data["updated_fields"] == ["done"]
