import uuid

from fastapi.testclient import TestClient

import server
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
    return client, response.json()


def test_admin_api_is_hidden_from_regular_users() -> None:
    client, _ = register_client()

    assert client.get("/api/admin/overview").status_code == 404
    assert client.get("/api/admin/users").status_code == 404
    assert client.get("/api/admin/payments").status_code == 404


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
    assert users.status_code == 200
    assert users.json()[0]["email"] == payload["email"]
    assert users.json()[0]["is_admin"] is True
    assert payments.status_code == 200
    assert payments.json() == []


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
