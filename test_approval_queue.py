from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient

import content_routes
import server
from auth_service import attempt_limiter


PASSWORD = "correct horse battery staple"


def register(prefix: str) -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"{prefix}-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
            "display_name": prefix.title(),
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


def project_for(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    workspace = client.get("/api/workspaces").json()[0]
    project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    return workspace, project


def add_member(
    owner: TestClient,
    workspace_id: str,
    user: dict[str, object],
    role: str,
) -> None:
    response = owner.post(
        f"/api/workspaces/{workspace_id}/members",
        headers=csrf(owner),
        json={"email": user["email"], "role": role},
    )
    assert response.status_code == 201, response.text


def upload_pdf(
    client: TestClient,
    project_id: str,
    name: str = "approval.pdf",
) -> dict[str, object]:
    response = client.post(
        f"/api/projects/{project_id}/files",
        headers=csrf(client),
        files={"file": (name, b"%PDF-1.7\napproval queue", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_assigned_approval_round_tracks_decisions_and_history(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("queue-owner")
    client, client_user = register("queue-client")
    editor, editor_user = register("queue-editor")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], client_user, "client")
    add_member(owner, workspace["id"], editor_user, "editor")
    asset = upload_pdf(owner, project["id"])
    due_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    created = owner.post(
        f"/api/content-attachments/{asset['id']}/approval-request",
        headers=csrf(owner),
        json={
            "assignee_user_id": client_user["id"],
            "due_at": due_at,
            "visibility": "client",
            "note": "Финальная проверка",
        },
    )
    assert created.status_code == 201, created.text
    approval_request = created.json()
    assert approval_request["status"] == "pending"
    assert approval_request["assignee"]["id"] == client_user["id"]
    assert approval_request["note"] == "Финальная проверка"

    client_queue = client.get(
        f"/api/projects/{project['id']}/approval-queue"
    ).json()
    assert client_queue["summary"]["pending"] == 1
    assert client_queue["requests"][0]["id"] == approval_request["id"]

    editor_decision = editor.put(
        f"/api/content-attachments/{asset['id']}/approval",
        headers=csrf(editor),
        json={"decision": "approved", "comment": "Со стороны команды готово"},
    )
    assert editor_decision.status_code == 200
    still_pending = owner.get(
        f"/api/projects/{project['id']}/approval-queue"
    ).json()
    assert still_pending["requests"][0]["status"] == "pending"

    client_decision = client.put(
        f"/api/content-attachments/{asset['id']}/approval",
        headers=csrf(client),
        json={"decision": "approved", "comment": "Принимаю"},
    )
    assert client_decision.status_code == 200
    approved = owner.get(
        f"/api/projects/{project['id']}/approval-queue"
    ).json()
    assert approved["summary"]["approved"] == 1
    assert approved["requests"][0]["status"] == "approved"

    history = owner.get(
        f"/api/approval-requests/{approval_request['id']}/history"
    )
    assert history.status_code == 200
    assert [event["event_type"] for event in history.json()] == [
        "decision",
        "decision",
        "requested",
    ]

    cleared = client.delete(
        f"/api/content-attachments/{asset['id']}/approval",
        headers=csrf(client),
    )
    assert cleared.status_code == 204
    pending_again = owner.get(
        f"/api/projects/{project['id']}/approval-queue"
    ).json()
    assert pending_again["requests"][0]["status"] == "pending"

    changes = client.put(
        f"/api/content-attachments/{asset['id']}/approval",
        headers=csrf(client),
        json={"decision": "changes_requested", "comment": "Поправить титр"},
    )
    assert changes.status_code == 200
    needs_changes = owner.get(
        f"/api/projects/{project['id']}/approval-queue"
    ).json()
    assert needs_changes["summary"]["changes_requested"] == 1


def test_queue_visibility_permissions_deadline_and_reopen(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("queue-security-owner")
    client, client_user = register("queue-security-client")
    viewer, viewer_user = register("queue-security-viewer")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], client_user, "client")
    add_member(owner, workspace["id"], viewer_user, "viewer")
    asset = upload_pdf(owner, project["id"], "internal.pdf")
    overdue_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    denied = viewer.post(
        f"/api/content-attachments/{asset['id']}/approval-request",
        headers=csrf(viewer),
        json={"visibility": "team"},
    )
    assert denied.status_code == 403
    invalid_assignee = owner.post(
        f"/api/content-attachments/{asset['id']}/approval-request",
        headers=csrf(owner),
        json={"visibility": "team", "assignee_user_id": client_user["id"]},
    )
    assert invalid_assignee.status_code == 400

    created = owner.post(
        f"/api/content-attachments/{asset['id']}/approval-request",
        headers=csrf(owner),
        json={"visibility": "team", "due_at": overdue_at},
    )
    assert created.status_code == 201, created.text
    approval_request = created.json()
    assert approval_request["overdue"] is True
    assert client.get(
        f"/api/projects/{project['id']}/approval-queue"
    ).json()["requests"] == []
    overdue = owner.get(
        f"/api/projects/{project['id']}/approval-queue?status=overdue"
    ).json()
    assert overdue["summary"]["overdue"] == 1
    assert len(overdue["requests"]) == 1

    cancelled = owner.patch(
        f"/api/approval-requests/{approval_request['id']}",
        headers=csrf(owner),
        json={"status": "cancelled"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    reopened = owner.post(
        f"/api/content-attachments/{asset['id']}/approval-request",
        headers=csrf(owner),
        json={"visibility": "client", "assignee_user_id": client_user["id"]},
    )
    assert reopened.status_code == 201
    assert reopened.json()["status"] == "pending"
    assert client.get(
        f"/api/approval-requests/{approval_request['id']}/history"
    ).status_code == 200


def test_finished_round_requires_a_new_file_version(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("queue-version-owner")
    workspace, project = project_for(owner)
    first = upload_pdf(owner, project["id"], "campaign.pdf")
    created = owner.post(
        f"/api/content-attachments/{first['id']}/approval-request",
        headers=csrf(owner),
        json={},
    )
    assert created.status_code == 201
    assert owner.put(
        f"/api/content-attachments/{first['id']}/approval",
        headers=csrf(owner),
        json={"decision": "approved"},
    ).status_code == 200
    duplicate = owner.post(
        f"/api/content-attachments/{first['id']}/approval-request",
        headers=csrf(owner),
        json={},
    )
    assert duplicate.status_code == 409

    second_response = owner.post(
        f"/api/content-attachments/{first['id']}/versions",
        headers=csrf(owner),
        files={"file": ("campaign.pdf", b"%PDF-1.7\nversion two", "application/pdf")},
    )
    assert second_response.status_code == 201
    second = second_response.json()
    next_round = owner.post(
        f"/api/content-attachments/{second['id']}/approval-request",
        headers=csrf(owner),
        json={},
    )
    assert next_round.status_code == 201
    assert next_round.json()["status"] == "pending"
