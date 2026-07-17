import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import decision_routes
import server
from auth_service import attempt_limiter
from decision_intelligence import classify_text, extract_due_at, impact_score


PASSWORD = "correct horse battery staple"


def register(prefix: str):
    client = TestClient(server.app)
    response = client.post("/api/auth/register", headers={"Origin": "http://testserver"}, json={
        "email": f"{prefix}-{uuid.uuid4().hex}@example.com",
        "password": PASSWORD,
        "display_name": prefix.title(),
    })
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    return client, response.json()


def csrf(client):
    return {"Origin": "http://testserver", "X-CSRF-Token": client.cookies.get("yt_loader_csrf")}


def context(client):
    workspace = client.get("/api/workspaces").json()[0]
    project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    return workspace, project


def add_member(owner, workspace_id, user, role="viewer"):
    response = owner.post(f"/api/workspaces/{workspace_id}/members", headers=csrf(owner), json={
        "email": user["email"], "role": role,
    })
    assert response.status_code == 201, response.text


def test_rules_understand_decisions_actions_risks_questions_and_dates() -> None:
    assert "decision" in {kind for kind, _ in classify_text("Решили выпускать ролик в пятницу")}
    assert "commitment" in {kind for kind, _ in classify_text("Я сделаю обложку завтра")}
    assert "risk" in {kind for kind, _ in classify_text("Риск: подрядчик задержит монтаж")}
    assert "question" in {kind for kind, _ in classify_text("Кто согласует финал?")}
    reference = datetime(2026, 7, 17, 9)
    due = extract_due_at("Нужно подготовить завтра", reference)
    assert due == datetime(2026, 7, 18, 18, tzinfo=timezone.utc)
    assert impact_score("risk", "urgent", datetime(2026, 7, 16), now=reference) >= 14


def test_extract_is_idempotent_and_never_reads_private_chats() -> None:
    owner, _ = register("intel-owner")
    editor, editor_user = register("intel-editor")
    workspace, project = context(owner)
    add_member(owner, workspace["id"], editor_user, "editor")
    conversations = owner.get(f"/api/projects/{project['id']}/conversations").json()
    general = next(item for item in conversations if item["is_project_wide"])
    for body in (
        "Решили использовать вертикальный формат 9:16.",
        "Я сделаю финальную обложку завтра.",
        "Риск: можем не успеть к публикации.",
    ):
        assert owner.post(f"/api/conversations/{general['id']}/messages", headers=csrf(owner), json={"body": body}).status_code == 201
    private = owner.post(f"/api/projects/{project['id']}/conversations", headers=csrf(owner), json={
        "kind": "direct", "participant_user_ids": [editor_user["id"]],
    }).json()
    secret = "Решили сократить бюджет SECRET-PRIVATE-42"
    owner.post(f"/api/conversations/{private['id']}/messages", headers=csrf(owner), json={"body": secret})

    extracted = owner.post(f"/api/projects/{project['id']}/insights/extract", headers=csrf(owner), json={"use_ai": False})
    assert extracted.status_code == 200, extracted.text
    assert extracted.json()["inserted"] >= 3
    items = owner.get(f"/api/projects/{project['id']}/insights").json()
    assert {"decision", "commitment", "risk"}.issubset({item["kind"] for item in items})
    assert all("SECRET-PRIVATE-42" not in (item.get("description") or "") for item in items)
    repeated = owner.post(f"/api/projects/{project['id']}/insights/extract", headers=csrf(owner), json={"use_ai": False})
    assert repeated.json()["inserted"] == 0

    attention = owner.get(f"/api/projects/{project['id']}/attention")
    assert attention.status_code == 200
    assert attention.json()["stats"]["open_insights"] >= 3
    graph = owner.get(f"/api/projects/{project['id']}/graph").json()
    assert any(node["entity_type"] == "insight" for node in graph["nodes"])


def test_manual_insight_lifecycle_permissions_and_client_visibility() -> None:
    owner, _ = register("manual-owner")
    viewer, viewer_user = register("manual-viewer")
    client, client_user = register("manual-client")
    outsider, _ = register("manual-outsider")
    workspace, project = context(owner)
    add_member(owner, workspace["id"], viewer_user, "viewer")
    add_member(owner, workspace["id"], client_user, "client")

    team = owner.post(f"/api/projects/{project['id']}/insights", headers=csrf(owner), json={
        "kind": "action", "title": "Подготовить медиаплан", "description": "Собрать каналы и сроки",
        "assignee_user_id": viewer_user["id"], "priority": "high", "visibility": "team",
    })
    assert team.status_code == 201, team.text
    client_item = client.post(f"/api/projects/{project['id']}/insights", headers=csrf(client), json={
        "kind": "question", "title": "Уточнить логотип", "visibility": "client",
    })
    assert client_item.status_code == 201, client_item.text
    assert team.json()["id"] not in {item["id"] for item in client.get(f"/api/projects/{project['id']}/insights").json()}
    assert client_item.json()["id"] in {item["id"] for item in client.get(f"/api/projects/{project['id']}/insights").json()}
    assert client.patch(f"/api/insights/{team.json()['id']}", headers=csrf(client), json={"status": "done"}).status_code == 404
    assert client.delete(f"/api/insights/{team.json()['id']}", headers=csrf(client)).status_code == 404
    assert outsider.get(f"/api/projects/{project['id']}/attention").status_code == 404
    assert viewer.post(f"/api/projects/{project['id']}/insights/extract", headers=csrf(viewer), json={}).status_code == 403
    assert viewer.post(f"/api/projects/{project['id']}/insights", headers=csrf(viewer), json={
        "kind": "action", "title": "Наблюдатель не должен создавать сигнал",
    }).status_code == 403
    assert owner.post(f"/api/projects/{project['id']}/insights", headers=csrf(owner), json={
        "kind": "action", "title": "Скрытая задача клиента",
        "visibility": "team", "assignee_user_id": client_user["id"],
    }).status_code == 400

    assert viewer.patch(
        f"/api/insights/{team.json()['id']}", headers=csrf(viewer),
        json={"title": "Попытка переписать сигнал"},
    ).status_code == 403
    done = viewer.patch(f"/api/insights/{team.json()['id']}", headers=csrf(viewer), json={"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert done.json()["completed_at"]
    assert owner.delete(f"/api/insights/{client_item.json()['id']}", headers=csrf(owner)).status_code == 204


def test_briefing_has_deterministic_fallback_and_sanitized_ai(monkeypatch) -> None:
    owner, _ = register("briefing-owner")
    _, project = context(owner)
    owner.post(f"/api/projects/{project['id']}/insights", headers=csrf(owner), json={
        "kind": "risk", "title": "Задержка монтажа", "priority": "urgent", "visibility": "team",
    })
    fallback = owner.post(f"/api/projects/{project['id']}/briefings", headers=csrf(owner), json={"use_ai": False})
    assert fallback.status_code == 201, fallback.text
    assert fallback.json()["provider"] == "rules"
    assert "Задержка монтажа" in str(fallback.json()["risks"])

    monkeypatch.setattr(decision_routes, "ai_enabled", lambda: True)
    monkeypatch.setattr(decision_routes, "generate_text", lambda *args, **kwargs: {
        "text": '{"summary":"AI summary","highlights":["bad",{"title":"Signal","detail":"Useful"}],"risks":[],"next_actions":[]}',
        "model": "test-model",
    })
    generated = owner.post(f"/api/projects/{project['id']}/briefings", headers=csrf(owner), json={"use_ai": True})
    assert generated.status_code == 201, generated.text
    assert generated.json()["provider"] == "openai"
    assert generated.json()["highlights"] == [{"title": "Signal", "detail": "Useful"}]
    assert len(owner.get(f"/api/projects/{project['id']}/briefings").json()) == 2


def test_client_cannot_use_ai_briefing_or_link_internal_diagram(monkeypatch) -> None:
    owner, _ = register("client-intel-owner")
    client, client_user = register("client-intel-client")
    workspace, project = context(owner)
    add_member(owner, workspace["id"], client_user, "client")
    diagram = owner.post(
        f"/api/projects/{project['id']}/diagrams", headers=csrf(owner),
        json={"title": "Внутренний план", "visibility": "team"},
    ).json()

    linked = client.post(
        f"/api/projects/{project['id']}/insights", headers=csrf(client),
        json={
            "kind": "question",
            "title": "Ссылка на скрытую схему",
            "visibility": "client",
            "links": [{
                "entity_type": "diagram",
                "entity_id": diagram["id"],
                "relation_type": "impacts",
            }],
        },
    )
    assert linked.status_code == 400

    monkeypatch.setattr(decision_routes, "ai_enabled", lambda: True)

    def unexpected_ai_call(*args, **kwargs):
        raise AssertionError("Client briefing must not call the paid AI provider")

    monkeypatch.setattr(decision_routes, "generate_text", unexpected_ai_call)
    briefing = client.post(
        f"/api/projects/{project['id']}/briefings", headers=csrf(client),
        json={"use_ai": True, "visibility": "client"},
    )
    assert briefing.status_code == 201, briefing.text
    assert briefing.json()["provider"] == "rules"
