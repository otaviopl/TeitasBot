"""Tests for health (meals + exercises) endpoints in web_app.app."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

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

    import web_app.dependencies as deps
    deps._user_store = None
    deps._assistant_service = None
    deps._google_oauth = None
    deps._credential_store = None
    deps._health_store = None

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
        res = client.patch("/api/health/exercises/some-id", json={"done": True})
        assert res.status_code in (401, 403)


# ---- Dashboard tests ----

class TestHealthDashboard:
    def test_empty_dashboard(self, client, auth_token):
        res = client.get("/api/health/dashboard", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["meals"] == []
        assert data["exercises"] == []
        assert data["totals"]["calories_consumed"] == 0
        assert data["totals"]["calories_burned"] == 0
        assert "notion_configured" not in data

    def test_dashboard_with_data(self, client, auth_token):
        from web_app.dependencies import get_health_store
        store = get_health_store()
        store.create_meal(
            user_id="web:healthuser", food="Arroz", meal_type="ALMOÇO",
            quantity="200g", calories=300, date="2025-03-06",
        )
        store.create_meal(
            user_id="web:healthuser", food="Frango", meal_type="ALMOÇO",
            quantity="150g", calories=250, date="2025-03-06",
        )
        store.create_exercise(
            user_id="web:healthuser", activity="Corrida", calories=350,
            date="2025-03-06", done=True,
        )

        res = client.get("/api/health/dashboard?date=2025-03-06", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
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
    def test_weekly_empty(self, client, auth_token):
        res = client.get("/api/health/weekly", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert "days" in data
        assert len(data["days"]) == 7
        assert "notion_configured" not in data

    def test_weekly_returns_7_days(self, client, auth_token):
        res = client.get("/api/health/weekly?end_date=2025-03-06", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert len(data["days"]) == 7
        assert data["days"][0]["date"] == "2025-02-28"
        assert data["days"][6]["date"] == "2025-03-06"

    def test_weekly_with_data(self, client, auth_token):
        from web_app.dependencies import get_health_store
        store = get_health_store()
        store.create_meal(
            user_id="web:healthuser", food="Arroz", meal_type="ALMOÇO",
            quantity="200g", calories=300, date="2025-03-06",
        )

        res = client.get("/api/health/weekly?end_date=2025-03-06", headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        last_day = next(d for d in data["days"] if d["date"] == "2025-03-06")
        assert last_day["calories_consumed"] == 300.0


# ---- Create meal tests ----

class TestCreateMeal:
    def test_missing_fields(self, client, auth_token):
        res = client.post("/api/health/meals", json={}, headers=_auth(auth_token))
        assert res.status_code == 422

    def test_invalid_meal_type(self, client, auth_token):
        res = client.post("/api/health/meals", json={
            "food": "Pizza", "meal_type": "INVALID", "quantity": "1 slice", "estimated_calories": 300
        }, headers=_auth(auth_token))
        assert res.status_code == 400
        assert "meal_type" in res.json()["detail"]

    def test_calories_too_high(self, client, auth_token):
        res = client.post("/api/health/meals", json={
            "food": "Something", "meal_type": "ALMOÇO", "quantity": "1 kg", "estimated_calories": 99999
        }, headers=_auth(auth_token))
        assert res.status_code == 400

    def test_create_meal_success(self, client, auth_token):
        res = client.post("/api/health/meals", json={
            "food": "Arroz", "meal_type": "ALMOÇO", "quantity": "200g", "estimated_calories": 300
        }, headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "created"
        assert data["meal"]["food"] == "Arroz"
        assert data["meal"]["calories"] == 300.0
        assert "id" in data["meal"]

    def test_create_meal_appears_in_dashboard(self, client, auth_token):
        client.post("/api/health/meals", json={
            "food": "Banana", "meal_type": "LANCHE", "quantity": "1 un", "estimated_calories": 90
        }, headers=_auth(auth_token))

        from utils.timezone_utils import today_iso_in_configured_timezone
        today = today_iso_in_configured_timezone()
        res = client.get(f"/api/health/dashboard?date={today}", headers=_auth(auth_token))
        assert res.status_code == 200
        foods = [m["food"] for m in res.json()["meals"]]
        assert "Banana" in foods


# ---- Create exercise tests ----

class TestCreateExercise:
    def test_missing_fields(self, client, auth_token):
        res = client.post("/api/health/exercises", json={}, headers=_auth(auth_token))
        assert res.status_code == 422

    def test_create_exercise_success(self, client, auth_token):
        res = client.post("/api/health/exercises", json={
            "activity": "Running", "calories": 350, "done": True
        }, headers=_auth(auth_token))
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "created"
        assert data["exercise"]["activity"] == "Running"
        assert "id" in data["exercise"]

    def test_activity_too_long(self, client, auth_token):
        res = client.post("/api/health/exercises", json={
            "activity": "A" * 201, "calories": 100
        }, headers=_auth(auth_token))
        assert res.status_code == 400

    def test_calories_invalid(self, client, auth_token):
        res = client.post("/api/health/exercises", json={
            "activity": "Yoga", "calories": -5
        }, headers=_auth(auth_token))
        assert res.status_code == 400

    def test_exercise_appears_in_dashboard(self, client, auth_token):
        client.post("/api/health/exercises", json={
            "activity": "Yoga", "calories": 120, "done": True
        }, headers=_auth(auth_token))

        from utils.timezone_utils import today_iso_in_configured_timezone
        today = today_iso_in_configured_timezone()
        res = client.get(f"/api/health/dashboard?date={today}", headers=_auth(auth_token))
        assert res.status_code == 200
        activities = [e["activity"] for e in res.json()["exercises"]]
        assert "Yoga" in activities


# ---- Update exercise tests ----

class TestUpdateExercise:
    def test_no_fields_to_update(self, client, auth_token):
        res = client.patch("/api/health/exercises/some-id", json={}, headers=_auth(auth_token))
        assert res.status_code == 400
        assert "At least one field" in res.json()["detail"]

    def test_not_found(self, client, auth_token):
        res = client.patch("/api/health/exercises/nonexistent-id", json={"done": True}, headers=_auth(auth_token))
        assert res.status_code == 404

    def test_update_exercise_success(self, client, auth_token):
        create_res = client.post("/api/health/exercises", json={
            "activity": "Cycling", "calories": 200, "done": False
        }, headers=_auth(auth_token))
        assert create_res.status_code == 200
        exercise_id = create_res.json()["exercise"]["id"]

        update_res = client.patch(f"/api/health/exercises/{exercise_id}", json={
            "done": True, "calories": 250
        }, headers=_auth(auth_token))
        assert update_res.status_code == 200
        data = update_res.json()
        assert data["done"] is True
        assert data["calories"] == 250.0

