import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

import content_routes
import server
from auth_service import attempt_limiter
from saas_models import FeedbackTicket, ProductEvent


PASSWORD = "correct horse battery staple"


def register_user(prefix: str = "beta") -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"{prefix}-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
        },
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    client.headers.update({
        "Origin": "http://testserver",
        "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
    })
    return client, response.json()


def workspace_and_project(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    workspace = client.get("/api/workspaces").json()[0]
    project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    return workspace, project


def test_onboarding_is_server_persisted_and_demo_project_is_idempotent() -> None:
    client, user = register_user()
    workspace, project = workspace_and_project(client)

    initial = client.get(
        "/api/onboarding",
        params={"workspace_id": workspace["id"], "project_id": project["id"]},
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["dismissed"] is False
    assert [step["done"] for step in initial.json()["steps"]] == [True, False, False, False]

    created = client.post(
        "/api/onboarding/demo",
        json={"workspace_id": workspace["id"]},
    )
    assert created.status_code == 201, created.text
    demo = created.json()
    assert demo["created"] is True

    try:
        repeated = client.post(
            "/api/onboarding/demo",
            json={"workspace_id": workspace["id"]},
        )
        assert repeated.status_code == 201
        assert repeated.json()["id"] == demo["id"]
        assert repeated.json()["created"] is False

        content = client.get(f"/api/projects/{demo['id']}/content")
        library = client.get(f"/api/projects/{demo['id']}/library")
        conversations = client.get(f"/api/projects/{demo['id']}/conversations")
        assert content.status_code == 200
        assert len(content.json()) == 4
        assert library.status_code == 200
        assert [item["name"] for item in library.json()] == ["Демо-бриф запуска.md"]
        assert conversations.status_code == 200
        assert conversations.json()[0]["name"] == "Общий чат"

        dismissed = client.patch(
            "/api/onboarding",
            json={"workspace_id": workspace["id"], "dismissed": True},
        )
        assert dismissed.status_code == 200
        state = client.get(
            "/api/onboarding",
            params={"workspace_id": workspace["id"], "project_id": demo["id"]},
        )
        assert state.json()["dismissed"] is True
        assert state.json()["demo_project_id"] == demo["id"]

        with server.SessionLocal() as db:
            names = db.scalars(
                select(ProductEvent.event_name).where(
                    ProductEvent.user_id == user["id"]
                )
            ).all()
        assert "demo_project_created" in names
        assert "onboarding_dismissed" in names
    finally:
        shutil.rmtree(content_routes.CONTENT_DIR / demo["id"], ignore_errors=True)


def test_feedback_is_private_rate_limited_and_does_not_enter_analytics_payload() -> None:
    client, user = register_user("feedback-owner")
    other_client, _ = register_user("feedback-other")
    workspace, project = workspace_and_project(client)
    secret_message = "В интерфейсе не открывается карточка тестового проекта."

    created = client.post(
        "/api/feedback",
        json={
            "workspace_id": workspace["id"],
            "project_id": project["id"],
            "category": "bug",
            "page": "content",
            "message": secret_message,
        },
    )
    assert created.status_code == 201, created.text
    assert [item["message"] for item in client.get("/api/feedback").json()] == [secret_message]
    assert other_client.get("/api/feedback").json() == []

    with server.SessionLocal() as db:
        ticket = db.scalar(
            select(FeedbackTicket).where(FeedbackTicket.user_id == user["id"])
        )
        event = db.scalar(
            select(ProductEvent).where(
                ProductEvent.user_id == user["id"],
                ProductEvent.event_name == "support_ticket_created",
            )
        )
        assert ticket.message == secret_message
        assert event.properties == {"category": "bug"}
        assert secret_message not in str(event.properties)

    for number in range(4):
        response = client.post(
            "/api/feedback",
            json={
                "workspace_id": workspace["id"],
                "project_id": project["id"],
                "category": "question",
                "page": "support",
                "message": f"Дополнительный вопрос номер {number} для поддержки.",
            },
        )
        assert response.status_code == 201
    limited = client.post(
        "/api/feedback",
        json={
            "workspace_id": workspace["id"],
            "project_id": project["id"],
            "category": "question",
            "page": "support",
            "message": "Шестое обращение должно быть ограничено сервером.",
        },
    )
    assert limited.status_code == 429


def test_product_event_rejects_foreign_workspace_context() -> None:
    owner, _ = register_user("event-owner")
    outsider, _ = register_user("event-outsider")
    workspace, project = workspace_and_project(owner)

    response = outsider.post(
        "/api/product-events",
        json={
            "event_name": "page_view",
            "workspace_id": workspace["id"],
            "project_id": project["id"],
            "page": "content",
        },
    )

    assert response.status_code == 404


def test_client_cannot_seed_demo_project() -> None:
    owner, _ = register_user("demo-owner")
    client_user, client_payload = register_user("demo-client")
    workspace, _ = workspace_and_project(owner)
    added = owner.post(
        f"/api/workspaces/{workspace['id']}/members",
        json={"email": client_payload["email"], "role": "client"},
    )
    assert added.status_code == 201, added.text

    response = client_user.post(
        "/api/onboarding/demo",
        json={"workspace_id": workspace["id"]},
    )

    assert response.status_code == 403
