import uuid

from fastapi.testclient import TestClient

import content_routes
import server
from auth_service import attempt_limiter


PASSWORD = "correct horse battery staple"


def register(prefix: str):
    client = TestClient(server.app)
    response = client.post("/api/auth/register", headers={"Origin": "http://testserver"}, json={
        "email": f"{prefix}-{uuid.uuid4().hex}@example.com", "password": PASSWORD, "display_name": prefix.title(),
    })
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    return client, response.json()


def csrf(client):
    return {"Origin": "http://testserver", "X-CSRF-Token": client.cookies.get("yt_loader_csrf")}


def project_for(client):
    workspace = client.get("/api/workspaces").json()[0]
    project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    return workspace, project


def add_member(owner, workspace_id, user, role="viewer"):
    response = owner.post(f"/api/workspaces/{workspace_id}/members", headers=csrf(owner), json={"email": user["email"], "role": role})
    assert response.status_code == 201, response.text


def seed_graph(owner, project_id):
    content = owner.post(f"/api/projects/{project_id}/content", headers=csrf(owner), json={
        "title": "Запуск кампании", "item_type": "campaign", "priority": "high",
    }).json()
    asset = owner.post(f"/api/projects/{project_id}/files", headers=csrf(owner), files={
        "file": ("campaign.pdf", b"%PDF-1.7\ngraph", "application/pdf"),
    }).json()
    review = owner.post(f"/api/content-attachments/{asset['id']}/reviews", headers=csrf(owner), json={
        "body": "Проверить финальный CTA", "visibility": "team",
    }).json()
    return content, asset, review


def test_live_project_graph_and_manual_tenant_safe_links(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("graph-owner")
    viewer, viewer_user = register("graph-viewer")
    editor, editor_user = register("graph-editor")
    outsider, _ = register("graph-outsider")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], viewer_user)
    add_member(owner, workspace["id"], editor_user, "editor")
    content, asset, review = seed_graph(owner, project["id"])
    direct = owner.post(f"/api/projects/{project['id']}/conversations", headers=csrf(owner), json={
        "kind": "direct", "participant_user_ids": [viewer_user["id"]],
    }).json()

    graph = owner.get(f"/api/projects/{project['id']}/graph")
    assert graph.status_code == 200, graph.text
    node_ids = {node["id"] for node in graph.json()["nodes"]}
    assert {f"project:{project['id']}", f"content:{content['id']}", f"asset:{asset['id']}", f"review:{review['id']}"}.issubset(node_ids)
    assert any(edge["relation"] == "about" for edge in graph.json()["edges"])
    assert outsider.get(f"/api/projects/{project['id']}/graph").status_code == 404
    assert f"conversation:{direct['id']}" in {node["id"] for node in viewer.get(f"/api/projects/{project['id']}/graph").json()["nodes"]}
    assert f"conversation:{direct['id']}" not in {node["id"] for node in editor.get(f"/api/projects/{project['id']}/graph").json()["nodes"]}
    hidden_link = editor.post(f"/api/projects/{project['id']}/entity-links", headers=csrf(editor), json={
        "source_type": "conversation", "source_id": direct["id"], "target_type": "content",
        "target_id": content["id"], "relation_type": "references",
    })
    assert hidden_link.status_code == 400

    denied = viewer.post(f"/api/projects/{project['id']}/entity-links", headers=csrf(viewer), json={
        "source_type": "content", "source_id": content["id"], "target_type": "asset", "target_id": asset["id"], "relation_type": "produces",
    })
    assert denied.status_code == 403
    link = owner.post(f"/api/projects/{project['id']}/entity-links", headers=csrf(owner), json={
        "source_type": "content", "source_id": content["id"], "target_type": "asset", "target_id": asset["id"],
        "relation_type": "produces", "label": "Результат кампании",
    })
    assert link.status_code == 201, link.text
    updated = owner.get(f"/api/projects/{project['id']}/graph").json()
    assert any(edge["id"] == link.json()["id"] and edge["manual"] for edge in updated["edges"])
    duplicate = owner.post(f"/api/projects/{project['id']}/entity-links", headers=csrf(owner), json={
        "source_type": "content", "source_id": content["id"], "target_type": "asset", "target_id": asset["id"], "relation_type": "produces",
    })
    assert duplicate.status_code == 409
    assert owner.delete(f"/api/entity-links/{link.json()['id']}", headers=csrf(owner)).status_code == 204
    assert not any(edge.get("manual") for edge in owner.get(f"/api/projects/{project['id']}/graph").json()["edges"])


def test_diagram_approval_template_edit_and_access(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("diagram-owner")
    viewer, viewer_user = register("diagram-viewer")
    client, client_user = register("diagram-client")
    outsider, _ = register("diagram-outsider")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], viewer_user)
    add_member(owner, workspace["id"], client_user, "client")

    created = owner.post(f"/api/projects/{project['id']}/diagrams", headers=csrf(owner), json={
        "title": "Согласование контента", "diagram_type": "process", "template": "approval",
    })
    assert created.status_code == 201, created.text
    diagram = created.json()
    assert len(diagram["nodes"]) >= 4 and len(diagram["edges"]) == len(diagram["nodes"]) - 1
    assert diagram["visibility"] == "team"
    assert viewer.get(f"/api/diagrams/{diagram['id']}").status_code == 200
    assert client.get(f"/api/diagrams/{diagram['id']}").status_code == 404
    assert client.get(f"/api/projects/{project['id']}/diagrams").json() == []
    assert outsider.get(f"/api/diagrams/{diagram['id']}").status_code == 404

    payload = {
        "title": "Производство ролика", "diagram_type": "flowchart", "description": "От идеи до публикации",
        "visibility": "client",
        "viewport": {"x": 10, "y": 20, "zoom": 1.2},
        "nodes": [
            {"key": "start", "kind": "start", "title": "Идея", "x": 50, "y": 80},
            {"key": "check", "kind": "decision", "title": "Готово?", "x": 320, "y": 80},
            {"key": "end", "kind": "end", "title": "Публикация", "x": 590, "y": 80},
        ],
        "edges": [
            {"source_key": "start", "target_key": "check", "edge_type": "default"},
            {"source_key": "check", "target_key": "end", "edge_type": "success", "label": "Да"},
        ],
    }
    assert viewer.put(f"/api/diagrams/{diagram['id']}", headers=csrf(viewer), json=payload).status_code == 403
    saved = owner.put(f"/api/diagrams/{diagram['id']}", headers=csrf(owner), json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["viewport"]["zoom"] == 1.2
    assert saved.json()["visibility"] == "client"
    assert saved.json()["edges"][1]["label"] == "Да"
    assert client.get(f"/api/diagrams/{diagram['id']}").status_code == 200
    assert client.get(f"/api/projects/{project['id']}/diagrams").json()[0]["id"] == diagram["id"]
    assert owner.get(f"/api/projects/{project['id']}/diagrams").json()[0]["title"] == "Производство ролика"
    graph_nodes = {node["id"] for node in owner.get(f"/api/projects/{project['id']}/graph").json()["nodes"]}
    assert f"diagram:{diagram['id']}" in graph_nodes
    assert owner.delete(f"/api/diagrams/{diagram['id']}", headers=csrf(owner)).status_code == 204
    assert owner.get(f"/api/diagrams/{diagram['id']}").status_code == 404


def test_client_graph_hides_team_only_reviews(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register("graph-visibility-owner")
    client, client_user = register("graph-visibility-client")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], client_user, "client")
    _, _, review = seed_graph(owner, project["id"])
    owner_graph = owner.get(f"/api/projects/{project['id']}/graph").json()
    owner_nodes = {node["id"] for node in owner_graph["nodes"]}
    client_nodes = {node["id"] for node in client.get(f"/api/projects/{project['id']}/graph").json()["nodes"]}
    assert f"review:{review['id']}" in owner_nodes
    review_node = next(node for node in owner_graph["nodes"] if node["id"] == f"review:{review['id']}")
    assert review_node["extra"]["attachment_id"]
    assert f"review:{review['id']}" not in client_nodes


def test_collaborative_graph_state_custom_nodes_history_and_conflicts() -> None:
    owner, _ = register("graph-state-owner")
    client, client_user = register("graph-state-client")
    workspace, project = project_for(owner)
    add_member(owner, workspace["id"], client_user, "client")
    payload = {
        "revision": 0,
        "viewport": {"x": 120, "y": 80, "zoom": 0.8},
        "positions": {
            f"project:{project['id']}": {"x": 900, "y": 620},
        },
        "custom_nodes": [
            {
                "id": "launch-risk",
                "label": "Риск запуска",
                "kind": "note",
                "description": "Проверить оплату до запуска",
                "color": "#ef4444",
                "visibility": "team",
                "x": 1180,
                "y": 420,
            },
            {
                "id": "client-milestone",
                "label": "Веха клиента",
                "kind": "task",
                "color": "#22c55e",
                "visibility": "client",
                "linked_entity_type": "project",
                "linked_entity_id": project["id"],
                "x": 1240,
                "y": 760,
            },
        ],
    }
    saved = owner.put(
        f"/api/projects/{project['id']}/graph-state",
        headers=csrf(owner),
        json=payload,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1

    owner_graph = owner.get(f"/api/projects/{project['id']}/graph").json()
    owner_nodes = {node["id"]: node for node in owner_graph["nodes"]}
    assert owner_graph["viewport"]["zoom"] == 0.8
    assert owner_nodes[f"project:{project['id']}"]["x"] == 900
    assert owner_nodes["custom:launch-risk"]["extra"]["description"] == "Проверить оплату до запуска"
    assert any(edge["relation"] == "linked_to" for edge in owner_graph["edges"])

    client_graph = client.get(f"/api/projects/{project['id']}/graph").json()
    client_nodes = {node["id"] for node in client_graph["nodes"]}
    assert "custom:client-milestone" in client_nodes
    assert "custom:launch-risk" not in client_nodes
    assert client.put(
        f"/api/projects/{project['id']}/graph-state",
        headers=csrf(client),
        json={**payload, "revision": 1},
    ).status_code == 403

    link = owner.post(
        f"/api/projects/{project['id']}/entity-links",
        headers=csrf(owner),
        json={
            "source_type": "custom",
            "source_id": "launch-risk",
            "target_type": "project",
            "target_id": project["id"],
            "relation_type": "blocks",
        },
    )
    assert link.status_code == 201, link.text
    assert any(
        edge["id"] == link.json()["id"]
        for edge in owner.get(f"/api/projects/{project['id']}/graph").json()["edges"]
    )
    assert owner.get(f"/api/projects/{project['id']}/graph").json()["revision"] == 2

    stale = owner.put(
        f"/api/projects/{project['id']}/graph-state",
        headers=csrf(owner),
        json=payload,
    )
    assert stale.status_code == 409
    second_payload = {
        **payload,
        "revision": 2,
        "custom_nodes": [payload["custom_nodes"][1]],
    }
    second = owner.put(
        f"/api/projects/{project['id']}/graph-state",
        headers=csrf(owner),
        json=second_payload,
    )
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 3
    assert not any(
        edge.get("id") == link.json()["id"]
        for edge in owner.get(f"/api/projects/{project['id']}/graph").json()["edges"]
    )

    history = owner.get(f"/api/projects/{project['id']}/graph-history")
    assert history.status_code == 200
    assert [item["revision"] for item in history.json()[:3]] == [3, 2, 1]
    first_revision = next(item for item in history.json() if item["revision"] == 1)
    restored = owner.post(
        f"/api/projects/{project['id']}/graph-history/{first_revision['id']}/restore",
        headers=csrf(owner),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == 4
    assert "custom:launch-risk" in {
        node["id"] for node in owner.get(f"/api/projects/{project['id']}/graph").json()["nodes"]
    }
