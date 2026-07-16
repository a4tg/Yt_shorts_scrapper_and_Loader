import uuid

from fastapi.testclient import TestClient

import server
from auth_service import attempt_limiter


PASSWORD = "correct horse battery staple"


def register_user() -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"workspace-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
            "display_name": "Команда контента",
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


def test_registration_creates_personal_workspace_project_and_workflow() -> None:
    client, _ = register_user()
    workspaces = client.get("/api/workspaces")
    assert workspaces.status_code == 200
    assert len(workspaces.json()) == 1
    workspace = workspaces.json()[0]
    assert workspace["role"] == "owner"
    assert workspace["project_count"] == 1
    assert workspace["member_count"] == 1

    projects = client.get(f"/api/workspaces/{workspace['id']}/projects")
    assert projects.status_code == 200
    assert projects.json()[0]["name"] == "Первый проект"

    workflow = client.get(f"/api/projects/{projects.json()[0]['id']}/approval-workflow")
    assert workflow.status_code == 200
    assert [stage["key"] for stage in workflow.json()["stages"]] == [
        "idea",
        "draft",
        "review",
        "approved",
        "published",
    ]


def test_owner_can_create_workspace_project_and_customize_approval_stages() -> None:
    client, _ = register_user()
    workspace_response = client.post(
        "/api/workspaces", headers=csrf(client), json={"name": "Бренды агентства"}
    )
    assert workspace_response.status_code == 201
    workspace = workspace_response.json()

    project_response = client.post(
        f"/api/workspaces/{workspace['id']}/projects",
        headers=csrf(client),
        json={"name": "Весенняя кампания", "description": "Запуск продукта", "color": "#12abef"},
    )
    assert project_response.status_code == 201
    project = project_response.json()
    assert project["color"] == "#12abef"

    workflow_response = client.put(
        f"/api/projects/{project['id']}/approval-workflow",
        headers=csrf(client),
        json={
            "name": "Согласование клиента",
            "stages": [
                {"name": "Черновик", "color": "#64748b", "required_role": "editor"},
                {"name": "Проверка клиента", "color": "#f59e0b", "required_role": "client"},
                {"name": "Готово", "color": "#22c55e", "required_role": "admin", "is_terminal": True},
            ],
        },
    )
    assert workflow_response.status_code == 200
    assert workflow_response.json()["name"] == "Согласование клиента"
    assert [stage["name"] for stage in workflow_response.json()["stages"]] == [
        "Черновик",
        "Проверка клиента",
        "Готово",
    ]


def test_roles_scope_projects_and_member_management() -> None:
    owner_client, _ = register_user()
    viewer_client, viewer = register_user()
    workspace = owner_client.get("/api/workspaces").json()[0]

    added = owner_client.post(
        f"/api/workspaces/{workspace['id']}/members",
        headers=csrf(owner_client),
        json={"email": viewer["email"], "role": "viewer"},
    )
    assert added.status_code == 201
    member = added.json()
    assert member["role"] == "viewer"

    assert viewer_client.get(f"/api/workspaces/{workspace['id']}/projects").status_code == 200
    denied = viewer_client.post(
        f"/api/workspaces/{workspace['id']}/projects",
        headers=csrf(viewer_client),
        json={"name": "Недоступный проект"},
    )
    assert denied.status_code == 403

    promoted = owner_client.patch(
        f"/api/workspaces/{workspace['id']}/members/{member['id']}",
        headers=csrf(owner_client),
        json={"role": "editor"},
    )
    assert promoted.status_code == 200
    allowed = viewer_client.post(
        f"/api/workspaces/{workspace['id']}/projects",
        headers=csrf(viewer_client),
        json={"name": "Проект редактора"},
    )
    assert allowed.status_code == 201

    removed = owner_client.delete(
        f"/api/workspaces/{workspace['id']}/members/{member['id']}",
        headers=csrf(owner_client),
    )
    assert removed.status_code == 204
    assert viewer_client.get(f"/api/workspaces/{workspace['id']}/projects").status_code == 404


def test_workspace_ids_do_not_disclose_other_tenants() -> None:
    first, _ = register_user()
    second, _ = register_user()
    workspace = first.get("/api/workspaces").json()[0]

    assert second.get(f"/api/workspaces/{workspace['id']}/projects").status_code == 404
    assert second.get(f"/api/workspaces/{workspace['id']}/members").status_code == 404


def test_project_jobs_are_shared_with_members_and_require_editor_to_create() -> None:
    owner_client, owner = register_user()
    viewer_client, viewer = register_user()
    workspace = owner_client.get("/api/workspaces").json()[0]
    project = owner_client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]

    added = owner_client.post(
        f"/api/workspaces/{workspace['id']}/members",
        headers=csrf(owner_client),
        json={"email": viewer["email"], "role": "viewer"},
    )
    assert added.status_code == 201

    created = server.manager.create(
        "import",
        {"channel_url": "https://youtube.com/@example/shorts", "limit": 1},
        str(owner["id"]),
        workspace_id=workspace["id"],
        project_id=project["id"],
    )
    assert created["workspace_id"] == workspace["id"]
    assert created["project_id"] == project["id"]
    assert viewer_client.get(f"/api/jobs/{created['id']}").status_code == 200
    listed = viewer_client.get(f"/api/jobs?project_id={project['id']}")
    assert listed.status_code == 200
    assert created["id"] in {job["id"] for job in listed.json()}

    denied = viewer_client.post(
        "/api/channels/import",
        headers=csrf(viewer_client),
        json={
            "channel_url": "https://youtube.com/@example/shorts",
            "limit": 1,
            "project_id": project["id"],
        },
    )
    assert denied.status_code == 403
