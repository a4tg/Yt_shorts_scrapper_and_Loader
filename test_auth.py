import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

import server
from auth_service import attempt_limiter, verify_password
from saas_models import Job, Overlay, User


PASSWORD = "correct horse battery staple"


def register_client() -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"auth-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
        },
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    return client, response.json()


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
    }


def test_protected_api_requires_login() -> None:
    response = TestClient(server.app).get(f"/api/jobs/{uuid.uuid4().hex}")
    assert response.status_code == 401
    assert response.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_workspace_depth_features_are_public_and_disabled_by_default(monkeypatch) -> None:
    names = (
        "WORKSPACE_DEPTH_SHELL",
        "CHAT_ANYWHERE",
        "ASSET_VIEWER",
        "ASSET_REVIEWS",
        "PROJECT_GRAPH",
        "DECISION_INTELLIGENCE",
    )
    for name in names:
        monkeypatch.delenv(f"YT_LOADER_FEATURE_{name}", raising=False)
    response = TestClient(server.app).get("/api/auth/config")
    assert response.status_code == 200
    assert response.json()["features"] == {
        "workspace_depth_shell": False,
        "chat_anywhere": False,
        "asset_viewer": False,
        "asset_reviews": False,
        "project_graph": False,
        "decision_intelligence": False,
    }


def test_workspace_depth_feature_can_be_enabled(monkeypatch) -> None:
    monkeypatch.setenv("YT_LOADER_FEATURE_ASSET_VIEWER", "true")
    response = TestClient(server.app).get("/api/auth/config")
    assert response.json()["features"]["asset_viewer"] is True


def test_registration_uses_argon2_and_server_side_session() -> None:
    client, payload = register_client()

    set_cookie = client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"email": payload["email"], "password": PASSWORD},
    ).headers.get("set-cookie", "")
    assert "yt_loader_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie

    with server.SessionLocal() as db:
        user = db.scalar(select(User).where(User.id == payload["id"]))
        assert user is not None
        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2id$")
        assert verify_password(user.password_hash, PASSWORD)


def test_csrf_and_origin_checks_block_unsafe_requests() -> None:
    client, _ = register_client()
    without_csrf = client.post(
        "/api/channels/import",
        headers={"Origin": "http://testserver"},
        json={"channel_url": "https://youtube.com/@example/shorts", "limit": 1},
    )
    assert without_csrf.status_code == 403

    foreign_origin = client.post(
        "/api/channels/import",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
        },
        json={"channel_url": "https://youtube.com/@example/shorts", "limit": 1},
    )
    assert foreign_origin.status_code == 403


def test_logout_revokes_session() -> None:
    client, _ = register_client()
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert logout.status_code == 200
    assert logout.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    assert client.get("/api/auth/me").status_code == 401


def test_user_cannot_read_another_users_job() -> None:
    owner_client, owner = register_client()
    other_client, _ = register_client()
    job_id = uuid.uuid4().hex
    server.manager.create(
        "import",
        {"channel_url": "https://youtube.com/@example/shorts", "limit": 1},
        str(owner["id"]),
        job_id=job_id,
    )
    try:
        assert owner_client.get(f"/api/jobs/{job_id}").status_code == 200
        # A 404 avoids revealing that another user's UUID exists.
        assert other_client.get(f"/api/jobs/{job_id}").status_code == 404
        assert job_id in {job["id"] for job in owner_client.get("/api/jobs").json()}
        assert job_id not in {job["id"] for job in other_client.get("/api/jobs").json()}
    finally:
        with server.SessionLocal() as db:
            record = db.get(Job, job_id)
            if record:
                db.delete(record)
                db.commit()
        (server.JOBS_DIR / f"{job_id}.json").unlink(missing_ok=True)


def test_overlay_token_is_scoped_to_its_owner() -> None:
    _owner_client, owner = register_client()
    other_client, _ = register_client()
    token = uuid.uuid4().hex
    owner_dir = server.LOGOS_DIR / str(owner["id"])
    owner_dir.mkdir(parents=True, exist_ok=True)
    overlay = owner_dir / f"{token}_private.png"
    overlay.write_bytes(b"private-overlay")
    with server.SessionLocal() as db:
        db.add(
            Overlay(
                id=token,
                user_id=str(owner["id"]),
                original_name="private.png",
                storage_path=str(overlay.resolve()),
                size_bytes=overlay.stat().st_size,
            )
        )
        db.commit()
    try:
        response = other_client.post(
            "/api/videos/download",
            headers=csrf_headers(other_client),
            json={
                "url": "https://youtu.be/abcdefghijk",
                "logo_tokens": [token],
            },
        )
        assert response.status_code == 404
    finally:
        with server.SessionLocal() as db:
            record = db.get(Overlay, token)
            if record:
                db.delete(record)
                db.commit()
        overlay.unlink(missing_ok=True)
        owner_dir.rmdir()
