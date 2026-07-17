import uuid

from fastapi.testclient import TestClient

import content_routes
import server
from auth_service import attempt_limiter


PASSWORD = "correct horse battery staple"


def register(prefix: str) -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register", headers={"Origin": "http://testserver"},
        json={"email": f"{prefix}-{uuid.uuid4().hex}@example.com", "password": PASSWORD, "display_name": prefix.title()},
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    return client, response.json()


def csrf(client: TestClient) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": client.cookies.get("yt_loader_csrf")}


def project_for(client: TestClient):
    workspace = client.get("/api/workspaces").json()[0]
    project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    return workspace, project


def add_member(owner: TestClient, workspace_id: str, user: dict[str, object], role: str) -> None:
    response = owner.post(
        f"/api/workspaces/{workspace_id}/members", headers=csrf(owner),
        json={"email": user["email"], "role": role},
    )
    assert response.status_code == 201, response.text


def upload_pdf(client: TestClient, project_id: str, name: str = "concept.pdf") -> dict[str, object]:
    response = client.post(
        f"/api/projects/{project_id}/files", headers=csrf(client),
        files={"file": (name, b"%PDF-1.7\nasset review", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_asset_versions_keep_history_and_only_current_version_in_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("version-owner")
    viewer, viewer_user = register("version-viewer")
    outsider, _ = register("version-outsider")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], viewer_user, "viewer")
    first = upload_pdf(owner, project["id"])
    assert first["version_number"] == 1 and first["is_current"] is True

    denied = viewer.post(
        f"/api/content-attachments/{first['id']}/versions", headers=csrf(viewer),
        data={"label": "V2"}, files={"file": ("concept.pdf", b"%PDF-1.7\nsecond", "application/pdf")},
    )
    assert denied.status_code == 403
    created = owner.post(
        f"/api/content-attachments/{first['id']}/versions", headers=csrf(owner),
        data={"label": "Клиентская правка", "notes": "Обновили композицию"},
        files={"file": ("concept.pdf", b"%PDF-1.7\nsecond", "application/pdf")},
    )
    assert created.status_code == 201, created.text
    second = created.json()
    assert second["asset_key"] == first["asset_key"]
    assert second["version_number"] == 2 and second["is_current"] is True
    versions = owner.get(f"/api/content-attachments/{first['id']}/versions")
    assert [item["version_number"] for item in versions.json()["versions"]] == [2, 1]
    assert versions.json()["current_attachment_id"] == second["id"]
    library = owner.get(f"/api/projects/{project['id']}/library").json()
    assert [item["id"] for item in library] == [second["id"]]
    assert owner.get(first["preview_url"]).status_code == 200
    assert outsider.get(f"/api/content-attachments/{first['id']}/versions").status_code == 404
    removed = owner.delete(f"/api/content-attachments/{second['id']}", headers=csrf(owner))
    assert removed.status_code == 204
    restored = owner.get(f"/api/projects/{project['id']}/library").json()
    assert restored[0]["id"] == first["id"] and restored[0]["is_current"] is True


def test_coordinate_timestamp_reviews_status_and_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("review-owner")
    client, client_user = register("review-client")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], client_user, "client")
    asset = upload_pdf(owner, project["id"], "review.pdf")

    invalid = owner.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(owner),
        json={"body": "Где координаты?", "annotation_type": "point"},
    )
    assert invalid.status_code == 400
    point = owner.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(owner),
        json={"body": "Сместить логотип", "annotation_type": "region", "x": .1, "y": .2,
              "width": .3, "height": .25, "visibility": "client", "assignee_user_id": client_user["id"]},
    )
    assert point.status_code == 201, point.text
    review = point.json()
    assert review["x"] == .1 and review["status"] == "open"
    resolved = client.patch(
        f"/api/asset-reviews/{review['id']}", headers=csrf(client), json={"status": "resolved"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved_by"]["id"] == client_user["id"]

    client_team = client.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(client),
        json={"body": "Скрытый комментарий", "visibility": "team"},
    )
    assert client_team.status_code == 403
    client_comment = client.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(client),
        json={"body": "Нужна новая версия", "annotation_type": "page", "page_number": 2, "visibility": "client"},
    )
    assert client_comment.status_code == 201
    listing = client.get(f"/api/content-attachments/{asset['id']}/reviews").json()
    assert listing["review_counts"]["resolved"] == 1
    assert listing["review_counts"]["open"] == 1

    owner_decision = owner.put(
        f"/api/content-attachments/{asset['id']}/approval", headers=csrf(owner),
        json={"decision": "approved", "comment": "Со стороны команды готово"},
    )
    assert owner_decision.status_code == 200
    client_decision = client.put(
        f"/api/content-attachments/{asset['id']}/approval", headers=csrf(client),
        json={"decision": "changes_requested", "comment": "Исправить страницу 2"},
    )
    assert client_decision.status_code == 200
    assert client_decision.json()["approval_state"] == "changes_requested"
    assert len(client_decision.json()["approvals"]) == 2


def test_team_reviews_are_hidden_from_client(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("visibility-owner")
    client, client_user = register("visibility-client")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], client_user, "client")
    asset = upload_pdf(owner, project["id"], "internal.pdf")
    response = owner.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(owner),
        json={"body": "Внутренняя заметка", "visibility": "team"},
    )
    assert response.status_code == 201
    assert len(owner.get(f"/api/content-attachments/{asset['id']}/reviews").json()["reviews"]) == 1
    assert client.get(f"/api/content-attachments/{asset['id']}/reviews").json()["reviews"] == []


def test_review_visibility_assignment_and_decision_roles_are_enforced(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("review-security-owner")
    viewer, viewer_user = register("review-security-viewer")
    client, client_user = register("review-security-client")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], viewer_user, "viewer")
    add_member(owner, workspace["id"], client_user, "client")
    asset = upload_pdf(owner, project["id"], "security.pdf")

    hidden_assignment = owner.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(owner),
        json={
            "body": "Внутренняя задача",
            "visibility": "team",
            "assignee_user_id": client_user["id"],
        },
    )
    assert hidden_assignment.status_code == 400

    hidden = owner.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(owner),
        json={"body": "Только для команды", "visibility": "team"},
    ).json()
    assert client.patch(
        f"/api/asset-reviews/{hidden['id']}", headers=csrf(client),
        json={"status": "resolved"},
    ).status_code == 404
    assert client.delete(
        f"/api/asset-reviews/{hidden['id']}", headers=csrf(client),
    ).status_code == 404

    public = client.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(client),
        json={"body": "Комментарий клиента", "visibility": "client"},
    ).json()
    reply = owner.post(
        f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(owner),
        json={
            "body": "Ответ команды",
            "visibility": "team",
            "parent_review_id": public["id"],
        },
    )
    assert reply.status_code == 201
    assert reply.json()["visibility"] == "client"

    assert viewer.put(
        f"/api/content-attachments/{asset['id']}/approval", headers=csrf(viewer),
        json={"decision": "approved"},
    ).status_code == 403
    assert viewer.delete(
        f"/api/content-attachments/{asset['id']}/approval", headers=csrf(viewer),
    ).status_code == 403

    decided = client.put(
        f"/api/content-attachments/{asset['id']}/approval", headers=csrf(client),
        json={"decision": "changes_requested", "comment": "Нужна правка"},
    )
    assert decided.status_code == 200
    assert any(item["is_own"] for item in decided.json()["approvals"])
    assert client.delete(
        f"/api/content-attachments/{asset['id']}/approval", headers=csrf(client),
    ).status_code == 204
    assert client.get(
        f"/api/content-attachments/{asset['id']}/reviews",
    ).json()["approval_state"] == "pending"
