import uuid

from fastapi.testclient import TestClient

import content_routes
import server
from auth_service import attempt_limiter


PASSWORD = "correct horse battery staple"


def register_user() -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"content-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
            "display_name": "Content maker",
        },
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    return client, response.json()


def csrf(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
    }


def current_project(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    workspace = client.get("/api/workspaces").json()[0]
    project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    return workspace, project


def test_content_lifecycle_calendar_filters_and_revision_history() -> None:
    client, user = register_user()
    _, project = current_project(client)
    workflow = client.get(f"/api/projects/{project['id']}/approval-workflow").json()
    idea = workflow["stages"][0]
    planned = "2026-08-10T09:30:00+03:00"

    created = client.post(
        f"/api/projects/{project['id']}/content",
        headers=csrf(client),
        json={
            "title": "Запуск летней кампании",
            "item_type": "post",
            "body": "Первый вариант текста",
            "stage_id": idea["id"],
            "channel": "telegram",
            "tags": ["launch", "Launch", " summer "],
            "priority": "high",
            "planned_at": planned,
            "assignee_user_id": user["id"],
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["stage"]["key"] == "idea"
    assert item["tags"] == ["launch", "summer"]
    assert item["assignee"]["id"] == user["id"]

    listed = client.get(
        f"/api/projects/{project['id']}/content",
        params={"q": "летней", "item_type": "post"},
    )
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()] == [item["id"]]

    updated = client.patch(
        f"/api/content/{item['id']}",
        headers=csrf(client),
        json={"title": "Запуск кампании", "body": "Финальный текст"},
    )
    assert updated.status_code == 200
    assert updated.json()["body"] == "Финальный текст"

    revisions = client.get(f"/api/content/{item['id']}/revisions")
    assert revisions.status_code == 200
    assert [revision["version"] for revision in revisions.json()] == [2, 1]

    archived = client.delete(f"/api/content/{item['id']}", headers=csrf(client))
    assert archived.status_code == 204
    assert client.get(f"/api/projects/{project['id']}/content").json() == []
    assert client.get(
        f"/api/projects/{project['id']}/content", params={"status": "archived"}
    ).json()[0]["id"] == item["id"]


def test_content_permissions_and_tenant_isolation() -> None:
    owner, _ = register_user()
    viewer, viewer_user = register_user()
    outsider, _ = register_user()
    workspace, project = current_project(owner)
    member = owner.post(
        f"/api/workspaces/{workspace['id']}/members",
        headers=csrf(owner),
        json={"email": viewer_user["email"], "role": "viewer"},
    )
    assert member.status_code == 201

    created = owner.post(
        f"/api/projects/{project['id']}/content",
        headers=csrf(owner),
        json={"title": "Командный материал", "item_type": "document"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    assert viewer.get(f"/api/content/{item_id}").status_code == 200
    denied = viewer.patch(
        f"/api/content/{item_id}", headers=csrf(viewer), json={"title": "Чужая правка"}
    )
    assert denied.status_code == 403
    assert outsider.get(f"/api/content/{item_id}").status_code == 404
    assert outsider.get(f"/api/projects/{project['id']}/content").status_code == 404


def test_content_attachments_feed_project_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    client, _ = register_user()
    _, project = current_project(client)
    created = client.post(
        f"/api/projects/{project['id']}/content",
        headers=csrf(client),
        json={"title": "Бриф", "item_type": "document"},
    ).json()

    uploaded = client.post(
        f"/api/content/{created['id']}/attachments",
        headers=csrf(client),
        files={"file": ("brief.pdf", b"example-pdf-data", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["name"] == "brief.pdf"
    assert attachment["size_bytes"] == len(b"example-pdf-data")

    detail = client.get(f"/api/content/{created['id']}").json()
    assert detail["attachments"][0]["id"] == attachment["id"]
    library = client.get(f"/api/projects/{project['id']}/library")
    assert library.status_code == 200
    assert library.json()[0]["content_title"] == "Бриф"

    downloaded = client.get(attachment["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"example-pdf-data"
    deleted = client.delete(
        f"/api/content-attachments/{attachment['id']}", headers=csrf(client)
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{project['id']}/library").json() == []


def test_imported_video_can_be_linked_once_to_the_content_plan() -> None:
    client, _ = register_user()
    _, project = current_project(client)
    payload = {
        "title": "VK clip",
        "item_type": "video",
        "channel": "vk",
        "source_platform": "vk",
        "source_id": "-77521_162222515",
        "source_url": "https://vk.com/video-77521_162222515",
    }
    first = client.post(
        f"/api/projects/{project['id']}/content", headers=csrf(client), json=payload
    )
    duplicate = client.post(
        f"/api/projects/{project['id']}/content", headers=csrf(client), json=payload
    )
    assert first.status_code == 201, first.text
    assert first.json()["source_platform"] == "vk"
    assert first.json()["source_url"] == payload["source_url"]
    assert duplicate.status_code == 409
