import json
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import server
from auth_service import attempt_limiter
from saas_models import Job


PASSWORD = "correct horse battery staple"
VK_CHANNEL = "https://vk.com/video/@mobidevices"
VK_VIDEO = "https://vk.com/video-77521_162222515"
RUTUBE_VIDEO = "https://rutube.ru/video/0123456789abcdef0123456789abcdef/"


def register_user() -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"sources-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
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


def project_id(client: TestClient) -> str:
    workspace = client.get("/api/workspaces").json()[0]
    return client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]["id"]


def test_vk_source_import_is_project_scoped_and_platform_is_persisted() -> None:
    client, _ = register_user()
    project = project_id(client)
    response = client.post(
        "/api/sources/import",
        headers=csrf(client),
        json={"source_url": VK_CHANNEL, "platform": "auto", "limit": 25, "project_id": project},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["project_id"] == project
    with server.SessionLocal() as db:
        record = db.get(Job, job["id"])
        assert record.request_payload["platform"] == "vk"
        assert record.request_payload["source_url"] == VK_CHANNEL


def test_source_import_rejects_platform_mismatch_and_unknown_hosts() -> None:
    client, _ = register_user()
    mismatch = client.post(
        "/api/sources/import",
        headers=csrf(client),
        json={"source_url": VK_CHANNEL, "platform": "rutube", "limit": 10},
    )
    unknown = client.post(
        "/api/sources/import",
        headers=csrf(client),
        json={"source_url": "https://example.com/videos", "platform": "auto", "limit": 10},
    )
    assert mismatch.status_code == 400
    assert unknown.status_code == 400


def test_vk_and_rutube_direct_videos_are_accepted_by_download_api() -> None:
    client, _ = register_user()
    for url, expected_platform in ((VK_VIDEO, "vk"), (RUTUBE_VIDEO, "rutube")):
        response = client.post(
            "/api/videos/download",
            headers=csrf(client),
            json={"url": url, "max_height": 1080, "metadata_mode": "strip"},
        )
        assert response.status_code == 202, response.text
        with server.SessionLocal() as db:
            record = db.get(Job, response.json()["id"])
            assert expected_platform in record.request_payload["url"]


def test_source_preview_returns_sanitized_metadata() -> None:
    client, _ = register_user()
    expected = {
        "url": VK_VIDEO,
        "platform": "vk",
        "id": "-77521_162222515",
        "title": "Demo",
        "thumbnail": "https://example.test/thumb.jpg",
        "duration": 10,
        "uploader": "Channel",
    }
    with patch("server.probe_source_video", return_value=expected) as preview:
        response = client.get("/api/sources/preview", params={"url": VK_VIDEO})
    assert response.status_code == 200
    assert response.json() == expected
    preview.assert_called_once_with(VK_VIDEO)


def test_import_items_support_paginated_web_and_legacy_responses() -> None:
    client, user = register_user()
    job_id = uuid.uuid4().hex
    items = [
        {
            "id": f"video-{index}",
            "title": f"Video {index}",
            "view_count": index * 100,
            "published_at": "2026-07-17",
        }
        for index in range(25)
    ]
    path = server.IMPORTS_DIR / f"{job_id}.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    with server.SessionLocal() as db:
        db.add(Job(
            id=job_id,
            user_id=str(user["id"]),
            kind="import",
            status="done",
            request_payload={"limit": 25},
        ))
        db.commit()
    try:
        first = client.get(
            f"/api/imports/{job_id}/items",
            params={"page": 1, "page_size": 12},
        )
        assert first.status_code == 200
        assert len(first.json()["items"]) == 12
        assert first.json()["pagination"] == {
            "page": 1,
            "page_size": 12,
            "total": 25,
            "pages": 3,
            "has_previous": False,
            "has_next": True,
        }
        last = client.get(
            f"/api/imports/{job_id}/items",
            params={"page": 3, "page_size": 12},
        )
        assert len(last.json()["items"]) == 1
        assert last.json()["pagination"]["has_next"] is False
        legacy = client.get(f"/api/imports/{job_id}/items")
        assert isinstance(legacy.json(), list)
        assert len(legacy.json()) == 25
        assert client.get(
            f"/api/imports/{job_id}/items",
            params={"page": 0},
        ).status_code == 400
    finally:
        with server.SessionLocal() as db:
            record = db.get(Job, job_id)
            if record:
                db.delete(record)
                db.commit()
        path.unlink(missing_ok=True)


def test_thumbnail_proxy_uses_cdn_allowlist_and_image_content_type() -> None:
    client, _ = register_user()
    denied = client.get(
        "/api/sources/thumbnail", params={"url": "https://example.com/private.jpg"}
    )
    assert denied.status_code == 400

    upstream = MagicMock()
    upstream.status_code = 200
    upstream.headers = {"content-type": "image/jpeg"}
    upstream.iter_bytes.return_value = [b"safe-image"]
    context = MagicMock()
    context.__enter__.return_value = upstream
    with patch("server.httpx.stream", return_value=context):
        allowed = client.get(
            "/api/sources/thumbnail",
            params={"url": "https://sun9-1.userapi.com/thumb.jpg"},
        )
    assert allowed.status_code == 200
    assert allowed.headers["content-type"] == "image/jpeg"
    assert allowed.content == b"safe-image"
