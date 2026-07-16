import uuid

from fastapi.testclient import TestClient

import content_routes
import server
from auth_service import attempt_limiter
from saas_models import Job


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
        files={"file": ("brief.pdf", b"%PDF-1.7\nexample-pdf-data", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["name"] == "brief.pdf"
    assert attachment["size_bytes"] == len(b"%PDF-1.7\nexample-pdf-data")

    detail = client.get(f"/api/content/{created['id']}").json()
    assert detail["attachments"][0]["id"] == attachment["id"]
    library = client.get(f"/api/projects/{project['id']}/library")
    assert library.status_code == 200
    assert library.json()[0]["content_title"] == "Бриф"

    downloaded = client.get(attachment["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-1.7\nexample-pdf-data"
    deleted = client.delete(
        f"/api/content-attachments/{attachment['id']}", headers=csrf(client)
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{project['id']}/library").json() == []


def test_content_attachment_rejects_unsupported_and_disguised_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    client, _ = register_user()
    _, project = current_project(client)
    item = client.post(
        f"/api/projects/{project['id']}/content",
        headers=csrf(client),
        json={"title": "Безопасная загрузка", "item_type": "document"},
    ).json()

    unsupported = client.post(
        f"/api/content/{item['id']}/attachments",
        headers=csrf(client),
        files={"file": ("payload.exe", b"MZ-not-allowed", "application/octet-stream")},
    )
    disguised = client.post(
        f"/api/content/{item['id']}/attachments",
        headers=csrf(client),
        files={"file": ("photo.png", b"MZ-not-a-png", "image/png")},
    )

    assert unsupported.status_code == 415
    assert disguised.status_code == 415
    assert client.get(f"/api/projects/{project['id']}/library").json() == []
    assert not list(tmp_path.rglob("*.part"))


def test_attachment_preview_is_inline_tenant_isolated_and_exposes_text_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register_user()
    outsider, _ = register_user()
    _, project = current_project(owner)
    uploaded = owner.post(
        f"/api/projects/{project['id']}/files", headers=csrf(owner),
        files={"file": ("notes.md", "# План\n\nБезопасный просмотр".encode(), "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset["preview"] == {
        "kind": "text", "can_preview": True, "inline_url": False, "data_url": True,
    }
    data = owner.get(asset["preview_data_url"])
    assert data.status_code == 200
    assert data.json()["text"].startswith("# План")
    assert owner.get(asset["preview_url"]).status_code == 415
    assert outsider.get(asset["preview_data_url"]).status_code == 404
    assert owner.get(f"/api/content-attachments/{asset['id']}").json()["preview"]["kind"] == "text"
    assert outsider.get(f"/api/content-attachments/{asset['id']}").status_code == 404

    image = owner.post(
        f"/api/projects/{project['id']}/files", headers=csrf(owner),
        files={"file": ("pixel.png", b"\x89PNG\r\n\x1a\npreview", "image/png")},
    ).json()
    preview = owner.get(image["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-disposition"] == "inline"
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    ranged = owner.get(image["preview_url"], headers={"Range": "bytes=0-7"})
    assert ranged.status_code in {200, 206}
    assert ranged.content.startswith(b"\x89PNG")
    assert outsider.get(image["preview_url"]).status_code == 404


def test_csv_preview_returns_bounded_table_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    client, _ = register_user()
    _, project = current_project(client)
    uploaded = client.post(
        f"/api/projects/{project['id']}/files", headers=csrf(client),
        files={"file": ("metrics.csv", "Канал,Просмотры\nVK,42\nYouTube,100".encode(), "text/csv")},
    )
    assert uploaded.status_code == 201, uploaded.text
    preview = client.get(uploaded.json()["preview_data_url"])
    assert preview.status_code == 200
    assert preview.json()["kind"] == "table"
    assert preview.json()["columns"] == ["Канал", "Просмотры"]
    assert preview.json()["rows"] == [["VK", "42"], ["YouTube", "100"]]


def test_project_folders_direct_upload_rename_and_move(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    client, _ = register_user()
    _, project = current_project(client)
    parent = client.post(
        f"/api/projects/{project['id']}/folders",
        headers=csrf(client), json={"name": "Кампания"},
    )
    assert parent.status_code == 201, parent.text
    child = client.post(
        f"/api/projects/{project['id']}/folders",
        headers=csrf(client), json={"name": "Исходники", "parent_id": parent.json()["id"]},
    )
    assert child.status_code == 201, child.text

    uploaded = client.post(
        f"/api/projects/{project['id']}/files",
        headers=csrf(client), data={"folder_id": child.json()["id"]},
        files={"file": ("brief.pdf", b"%PDF-1.7\nproject brief", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["folder_id"] == child.json()["id"]
    assert uploaded.json()["content_item_id"] is None

    renamed = client.patch(
        f"/api/project-files/{uploaded.json()['id']}",
        headers=csrf(client), json={"name": "Главный бриф.pdf", "folder_id": None},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Главный бриф.pdf"
    assert renamed.json()["folder_id"] is None
    assert client.patch(
        f"/api/project-files/{uploaded.json()['id']}",
        headers=csrf(client), json={"name": "Главный бриф.exe"},
    ).status_code == 400

    assert client.delete(f"/api/project-folders/{child.json()['id']}", headers=csrf(client)).status_code == 204
    assert client.delete(f"/api/project-folders/{parent.json()['id']}", headers=csrf(client)).status_code == 204
    library = client.get(f"/api/projects/{project['id']}/library").json()
    assert library[0]["name"] == "Главный бриф.pdf"
    assert library[0]["content_title"] is None


def test_save_ai_text_result_as_named_project_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    client, user = register_user()
    workspace, project = current_project(client)
    job_id = str(uuid.uuid4())
    with server.SessionLocal() as db:
        db.add(Job(
            id=job_id, user_id=user["id"], workspace_id=workspace["id"], project_id=project["id"],
            kind="ai_text", status="done", request_payload={},
            result_payload={"text": "# Готовая публикация\n\nТекст от AI"},
            credits_reserved=0, credits_spent=0,
        ))
        db.commit()

    saved = client.post(
        f"/api/jobs/{job_id}/save-to-project",
        headers=csrf(client), json={"name": "Публикация для запуска.md"},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["source_type"] == "ai"
    assert saved.json()["name"] == "Публикация для запуска.md"
    downloaded = client.get(saved.json()["download_url"])
    assert downloaded.status_code == 200
    assert "Текст от AI" in downloaded.text


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
