import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from admin_routes import _reported_ai_cost
from auth_service import attempt_limiter
from saas_models import User


PASSWORD = "correct horse battery staple"


def register_client() -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={"email": f"admin-test-{uuid.uuid4().hex}@example.com", "password": PASSWORD},
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    client.headers.update({
        "Origin": "http://testserver",
        "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
    })
    return client, response.json()


def test_admin_api_is_hidden_from_regular_users() -> None:
    client, _ = register_client()

    assert client.get("/api/admin/overview").status_code == 404
    assert client.get("/api/admin/users").status_code == 404
    assert client.get("/api/admin/payments").status_code == 404
    assert client.get("/api/admin/jobs").status_code == 404
    assert client.get("/api/admin/feedback").status_code == 404
    assert client.get("/api/admin/refunds").status_code == 404
    assert client.get("/api/admin/audit").status_code == 404
    assert client.post("/api/admin/integrations/ai/check").status_code == 404


def test_admin_can_read_commercial_overview() -> None:
    client, payload = register_client()
    with server.SessionLocal() as db:
        user = db.get(User, payload["id"])
        user.is_admin = True
        db.commit()

    overview = client.get("/api/admin/overview")
    users = client.get("/api/admin/users")
    payments = client.get("/api/admin/payments")

    assert overview.status_code == 200, overview.text
    assert overview.json()["users"] == 1
    assert overview.json()["workspaces"] == 1
    assert overview.json()["mrr_minor"] == 0
    assert overview.json()["active_users_7d"] == 0
    assert overview.json()["completed_onboarding"] == 0
    assert overview.json()["open_feedback"] == 0
    assert overview.json()["ai_usage_month"]["reported_cost_rub"] == 0
    assert overview.json()["ai_usage_month"]["budget_rub"] == 5000
    assert users.status_code == 200
    assert users.json()[0]["email"] == payload["email"]
    assert users.json()[0]["is_admin"] is True
    assert payments.status_code == 200
    assert payments.json() == []


def test_ai_cost_summary_does_not_double_count_nested_total() -> None:
    payload = {
        "usage": {
            "transcription": {"cost_rub": 7.8},
            "selection": {"cost_rub": 0.4},
            "cost_rub_total": 8.2,
        }
    }
    assert _reported_ai_cost(payload) == 8.2


def test_admin_can_run_sanitized_ai_connection_check() -> None:
    client, payload = register_client()
    with server.SessionLocal() as db:
        user = db.get(User, payload["id"])
        user.is_admin = True
        db.commit()
    diagnostic = {
        "status": "ok",
        "provider": "aitunnel",
        "model": "deepseek-v4-flash",
        "api_mode": "chat_completions",
        "latency_ms": 125,
    }
    with patch("admin_routes.check_ai_connection", return_value=diagnostic):
        response = client.post("/api/admin/integrations/ai/check")
    assert response.status_code == 200
    assert response.json() == diagnostic
    assert "key" not in str(response.json()).lower()


def test_admin_can_grant_credits_resolve_feedback_and_inspect_failed_jobs() -> None:
    admin_client, admin = register_client()
    user_client, user = register_client()
    with server.SessionLocal() as db:
        record = db.get(User, admin["id"])
        record.is_admin = True
        db.commit()
    workspace = user_client.get("/api/workspaces").json()[0]
    project = user_client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    ticket = user_client.post(
        "/api/feedback",
        json={
            "workspace_id": workspace["id"],
            "project_id": project["id"],
            "category": "question",
            "page": "support",
            "message": "Помогите проверить рабочий процесс пользователя.",
        },
    )
    job = server.manager.create("import", {"limit": 1}, str(user["id"]))
    server.manager.update(
        str(job["id"]),
        status="error",
        error_message="Test worker error",
    )
    before_grant = user_client.get("/api/billing/summary").json()["available"]

    granted = admin_client.post(
        f"/api/admin/users/{user['id']}/credits",
        json={"amount": 10, "reason": "Компенсация за тест закрытой беты."},
    )
    resolved = admin_client.patch(
        f"/api/admin/feedback/{ticket.json()['id']}",
        json={"status": "resolved", "resolution_note": "Сценарий проверен, ошибка устранена."},
    )
    jobs = admin_client.get("/api/admin/jobs", params={"status": "error"})
    audit = admin_client.get("/api/admin/audit")

    assert granted.status_code == 200
    assert granted.json()["available"] == before_grant + 10
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert jobs.status_code == 200
    assert jobs.json()[0]["error"] == "Test worker error"
    assert {entry["action"] for entry in audit.json()} == {
        "credits.grant",
        "feedback.update",
    }


def test_public_commercial_pages_and_favicon_are_available() -> None:
    client = TestClient(server.app)

    landing = client.get("/")
    application = client.get("/app")
    privacy = client.get("/privacy")
    terms = client.get("/terms")
    favicon = client.get("/favicon.ico")

    assert landing.status_code == 200
    assert "Рабочее пространство маркетолога" in landing.text
    assert application.status_code == 200
    assert 'id="auth-screen"' in application.text
    assert privacy.status_code == 200
    assert "Политика конфиденциальности" in privacy.text
    assert terms.status_code == 200
    assert "Условия использования" in terms.text
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
