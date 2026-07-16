import uuid

from fastapi.testclient import TestClient

import server
from auth_service import attempt_limiter


PASSWORD = "correct horse battery staple"


def register_user(prefix: str) -> tuple[TestClient, dict[str, object]]:
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


def workspace_project(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    workspace = client.get("/api/workspaces").json()[0]
    project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]
    return workspace, project


def add_member(
    owner: TestClient, workspace_id: str, user: dict[str, object], role: str = "editor"
) -> None:
    response = owner.post(
        f"/api/workspaces/{workspace_id}/members",
        headers=csrf(owner),
        json={"email": user["email"], "role": role},
    )
    assert response.status_code == 201, response.text


def test_project_general_chat_unread_and_read_state() -> None:
    owner, _ = register_user("owner")
    member, member_user = register_user("member")
    workspace, project = workspace_project(owner)
    add_member(owner, workspace["id"], member_user)

    owner_chats = owner.get(f"/api/projects/{project['id']}/conversations")
    member_chats = member.get(f"/api/projects/{project['id']}/conversations")
    assert owner_chats.status_code == 200
    assert member_chats.status_code == 200
    general = owner_chats.json()[0]
    assert general["name"] == "Общий чат"
    assert general["is_project_wide"] is True

    posted = owner.post(
        f"/api/conversations/{general['id']}/messages",
        headers=csrf(owner), json={"body": "Первое сообщение проекта"},
    )
    assert posted.status_code == 201, posted.text
    member_chat = member.get(f"/api/projects/{project['id']}/conversations").json()[0]
    assert member_chat["unread_count"] == 1
    messages = member.get(f"/api/conversations/{general['id']}/messages")
    assert messages.status_code == 200
    assert messages.json()["messages"][0]["body"] == "Первое сообщение проекта"
    assert member.post(f"/api/conversations/{general['id']}/read", headers=csrf(member)).status_code == 204
    assert member.get(f"/api/projects/{project['id']}/conversations").json()[0]["unread_count"] == 0


def test_direct_and_private_group_conversation_access() -> None:
    owner, _ = register_user("owner")
    member, member_user = register_user("member")
    outsider, outsider_user = register_user("outsider")
    workspace, project = workspace_project(owner)
    add_member(owner, workspace["id"], member_user)
    add_member(owner, workspace["id"], outsider_user, "viewer")

    direct = owner.post(
        f"/api/projects/{project['id']}/conversations",
        headers=csrf(owner),
        json={"kind": "direct", "participant_user_ids": [member_user["id"]]},
    )
    assert direct.status_code == 201, direct.text
    assert direct.json()["kind"] == "direct"
    repeated = owner.post(
        f"/api/projects/{project['id']}/conversations",
        headers=csrf(owner),
        json={"kind": "direct", "participant_user_ids": [member_user["id"]]},
    )
    assert repeated.json()["id"] == direct.json()["id"]
    assert outsider.get(f"/api/conversations/{direct.json()['id']}/messages").status_code == 404

    group = owner.post(
        f"/api/projects/{project['id']}/conversations",
        headers=csrf(owner),
        json={
            "kind": "group", "name": "Редакционная группа",
            "participant_user_ids": [member_user["id"]],
        },
    )
    assert group.status_code == 201, group.text
    assert member.get(f"/api/conversations/{group.json()['id']}/messages").status_code == 200
    assert outsider.get(f"/api/conversations/{group.json()['id']}/messages").status_code == 404


def test_message_reply_attachment_edit_delete_and_content_context(tmp_path, monkeypatch) -> None:
    import content_routes

    monkeypatch.setattr(content_routes, "CONTENT_DIR", tmp_path)
    owner, _ = register_user("owner")
    _, project = workspace_project(owner)
    conversation = owner.get(f"/api/projects/{project['id']}/conversations").json()[0]
    uploaded = owner.post(
        f"/api/projects/{project['id']}/files",
        headers=csrf(owner),
        files={"file": ("brief.pdf", b"%PDF-1.7\nchat brief", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    first = owner.post(
        f"/api/conversations/{conversation['id']}/messages",
        headers=csrf(owner),
        json={"body": "Посмотрите бриф", "attachment_id": uploaded.json()["id"]},
    )
    assert first.status_code == 201, first.text
    attachment_only = owner.post(
        f"/api/conversations/{conversation['id']}/messages",
        headers=csrf(owner), json={"attachment_id": uploaded.json()["id"]},
    )
    assert attachment_only.status_code == 201
    assert attachment_only.json()["attachment_name"] == "brief.pdf"
    reply = owner.post(
        f"/api/conversations/{conversation['id']}/messages",
        headers=csrf(owner),
        json={"body": "Принято в работу", "reply_to_message_id": first.json()["id"]},
    )
    assert reply.status_code == 201
    assert reply.json()["reply_to"]["body"] == "Посмотрите бриф"
    edited = owner.patch(
        f"/api/messages/{reply.json()['id']}",
        headers=csrf(owner), json={"body": "Принято в производство"},
    )
    assert edited.status_code == 200
    assert edited.json()["edited_at"] is not None
    assert owner.delete(f"/api/messages/{reply.json()['id']}", headers=csrf(owner)).status_code == 204
    messages = owner.get(f"/api/conversations/{conversation['id']}/messages").json()["messages"]
    assert messages[-1]["deleted_at"] is not None
    assert messages[-1]["body"] is None
    assert owner.delete(
        f"/api/content-attachments/{uploaded.json()['id']}", headers=csrf(owner)
    ).status_code == 204
    attachment_message = next(
        item for item in owner.get(f"/api/conversations/{conversation['id']}/messages").json()["messages"]
        if item["id"] == attachment_only.json()["id"]
    )
    assert attachment_message["attachment"] is None
    assert attachment_message["attachment_name"] == "brief.pdf"

    item = owner.post(
        f"/api/projects/{project['id']}/content",
        headers=csrf(owner), json={"title": "Ролик для запуска", "item_type": "video"},
    ).json()
    context = owner.post(f"/api/content/{item['id']}/conversation", headers=csrf(owner))
    assert context.status_code == 201
    assert context.json()["kind"] == "context"
    assert context.json()["content_title"] == "Ролик для запуска"
    repeated = owner.post(f"/api/content/{item['id']}/conversation", headers=csrf(owner))
    assert repeated.json()["id"] == context.json()["id"]


def test_mentions_reactions_pins_and_realtime_access() -> None:
    owner, _ = register_user("owner")
    member, member_user = register_user("member")
    outsider, outsider_user = register_user("outsider")
    workspace, project = workspace_project(owner)
    add_member(owner, workspace["id"], member_user)
    conversation = owner.get(f"/api/projects/{project['id']}/conversations").json()[0]

    created = owner.post(
        f"/api/conversations/{conversation['id']}/messages",
        headers=csrf(owner),
        json={"body": "@Member проверьте материал", "mentioned_user_ids": [member_user["id"]]},
    )
    assert created.status_code == 201, created.text
    message = created.json()
    assert message["mentions"] == [{"id": member_user["id"], "name": "Member"}]

    invalid_mention = owner.post(
        f"/api/conversations/{conversation['id']}/messages",
        headers=csrf(owner),
        json={"body": "Чужое упоминание", "mentioned_user_ids": [outsider_user["id"]]},
    )
    assert invalid_mention.status_code == 400

    reacted = member.post(
        f"/api/messages/{message['id']}/reactions",
        headers=csrf(member), json={"emoji": "👍"},
    )
    assert reacted.status_code == 201, reacted.text
    assert reacted.json()["reactions"][0]["count"] == 1
    assert reacted.json()["reactions"][0]["reacted_by_me"] is True
    duplicate = member.post(
        f"/api/messages/{message['id']}/reactions",
        headers=csrf(member), json={"emoji": "👍"},
    )
    assert duplicate.json()["reactions"][0]["count"] == 1
    assert member.delete(
        f"/api/messages/{message['id']}/reactions",
        headers=csrf(member), params={"emoji": "👍"},
    ).status_code == 204

    pinned = member.post(f"/api/messages/{message['id']}/pin", headers=csrf(member))
    assert pinned.status_code == 200
    assert pinned.json()["is_pinned"] is True
    assert pinned.json()["pinned_by"]["id"] == member_user["id"]
    pins = owner.get(f"/api/conversations/{conversation['id']}/pinned-messages")
    assert [item["id"] for item in pins.json()] == [message["id"]]
    assert member.delete(f"/api/messages/{message['id']}/pin", headers=csrf(member)).status_code == 204

    assert outsider.get(f"/api/projects/{project['id']}/message-events").status_code == 404
