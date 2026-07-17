import uuid

from fastapi.testclient import TestClient

import server
from auth_service import attempt_limiter


def registered_client() -> TestClient:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"batch-{uuid.uuid4().hex}@example.com",
            "password": "correct horse battery staple",
        },
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    client.headers.update(
        {
            "Origin": "http://testserver",
            "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
        }
    )
    return client


def default_project_id(client: TestClient) -> str:
    workspace = client.get("/api/workspaces").json()[0]
    return client.get(
        f"/api/workspaces/{workspace['id']}/projects"
    ).json()[0]["id"]


def batch_payload(project_id: str, count: int = 3) -> dict[str, object]:
    return {
        "items": [
            {
                "url": f"https://www.youtube.com/shorts/batch{id_number:06d}",
                "project_id": project_id,
                "logo_tokens": [],
            }
            for id_number in range(count)
        ]
    }


def test_batch_endpoint_creates_all_jobs_at_once_and_prevents_double_launch() -> None:
    client = registered_client()
    project_id = default_project_id(client)
    payload = batch_payload(project_id)

    first = client.post("/api/videos/download/batch", json=payload)
    assert first.status_code == 202, first.text
    first_body = first.json()
    assert first_body["created_count"] == 3
    assert first_body["duplicate_count"] == 0
    assert first_body["credits_reserved"] == 3
    assert len(first_body["jobs"]) == 3
    positions = [job["queue_position"] for job in first_body["jobs"]]
    assert positions == list(range(positions[0], positions[0] + 3))

    repeated = client.post("/api/videos/download/batch", json=payload)
    assert repeated.status_code == 202, repeated.text
    repeated_body = repeated.json()
    assert repeated_body["created_count"] == 0
    assert repeated_body["duplicate_count"] == 3
    assert [job["id"] for job in repeated_body["jobs"]] == [
        job["id"] for job in first_body["jobs"]
    ]

    statuses = client.post(
        "/api/jobs/statuses",
        json={"ids": [job["id"] for job in first_body["jobs"]]},
    )
    assert statuses.status_code == 200
    assert [job["status"] for job in statuses.json()] == ["queued"] * 3


def test_batch_endpoint_rejects_more_than_twenty_items() -> None:
    client = registered_client()
    project_id = default_project_id(client)
    response = client.post(
        "/api/videos/download/batch",
        json=batch_payload(project_id, count=21),
    )
    assert response.status_code == 422
