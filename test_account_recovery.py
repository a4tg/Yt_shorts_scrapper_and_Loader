import os
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

import server
from auth_service import attempt_limiter
from saas_models import AccountToken, UserSession
from test_auth import PASSWORD, csrf_headers


MAIL_ENV = {
    "YT_LOADER_PUBLIC_BASE_URL": "https://shorts.example.test",
    "SMTP_HOST": "smtp.example.test",
    "SMTP_FROM_EMAIL": "no-reply@example.test",
}


def register(email: str | None = None) -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": email or f"recovery-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
        },
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    return client, response.json()


def test_verification_token_is_hashed_single_use_and_blocks_paid_features() -> None:
    with patch.dict(os.environ, {**MAIL_ENV, "YT_LOADER_REQUIRE_EMAIL_VERIFICATION": "true"}), patch(
        "auth_routes.send_verification_email"
    ) as sender:
        client, user = register()
        sender.assert_called_once()
        raw_token = sender.call_args.args[1]
        assert user["email_verified"] is False

        with server.SessionLocal() as db:
            stored = db.scalar(select(AccountToken).where(AccountToken.user_id == user["id"]))
            assert stored is not None
            assert stored.token_hash != raw_token

        assert client.get("/api/billing/summary").status_code == 403
        assert client.post(
            "/api/payments/checkout",
            headers=csrf_headers(client),
            json={"plan_id": "creator"},
        ).status_code == 403

        confirmed = client.post(
            "/api/auth/verification/confirm",
            headers={"Origin": "http://testserver"},
            json={"token": raw_token},
        )
        assert confirmed.status_code == 200
        assert client.get("/api/billing/summary").status_code == 200
        assert client.post(
            "/api/auth/verification/confirm",
            headers={"Origin": "http://testserver"},
            json={"token": raw_token},
        ).status_code == 400


def test_password_reset_revokes_sessions_and_does_not_reveal_unknown_email() -> None:
    new_password = "a different secure password"
    with patch.dict(os.environ, MAIL_ENV), patch("auth_routes.send_password_reset_email") as sender:
        client, user = register()
        forgot = client.post(
            "/api/auth/password/forgot",
            headers={"Origin": "http://testserver"},
            json={"email": user["email"]},
        )
        attempt_limiter.clear("forgot-password:testclient")
        assert forgot.status_code == 202
        assert forgot.json() == {"status": "accepted"}
        raw_token = sender.call_args.args[1]

        reset = client.post(
            "/api/auth/password/reset",
            headers={"Origin": "http://testserver"},
            json={"token": raw_token, "password": new_password},
        )
        assert reset.status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        with server.SessionLocal() as db:
            assert all(
                session.revoked_at is not None
                for session in db.scalars(
                    select(UserSession).where(UserSession.user_id == str(user["id"]))
                ).all()
            )

        old_login = client.post(
            "/api/auth/login",
            headers={"Origin": "http://testserver"},
            json={"email": user["email"], "password": PASSWORD},
        )
        assert old_login.status_code == 401
        new_login = client.post(
            "/api/auth/login",
            headers={"Origin": "http://testserver"},
            json={"email": user["email"], "password": new_password},
        )
        assert new_login.status_code == 200
        assert client.post(
            "/api/auth/password/reset",
            headers={"Origin": "http://testserver"},
            json={"token": raw_token, "password": PASSWORD},
        ).status_code == 400

        sender.reset_mock()
        unknown = client.post(
            "/api/auth/password/forgot",
            headers={"Origin": "http://testserver"},
            json={"email": "unknown@example.com"},
        )
        attempt_limiter.clear("forgot-password:testclient")
        assert unknown.status_code == 202
        assert unknown.json() == forgot.json()
        sender.assert_not_called()


def test_authenticated_password_change_rotates_current_session() -> None:
    client, user = register()
    old_cookie = client.cookies.get("yt_loader_session")
    response = client.post(
        "/api/auth/password/change",
        headers=csrf_headers(client),
        json={"current_password": PASSWORD, "new_password": "changed secure password"},
    )
    assert response.status_code == 200
    assert client.cookies.get("yt_loader_session") != old_cookie
    assert client.get("/api/auth/me").status_code == 200
    with server.SessionLocal() as db:
        sessions = db.scalars(
            select(UserSession).where(UserSession.user_id == str(user["id"]))
        ).all()
        assert sum(session.revoked_at is None for session in sessions) == 1


def test_registration_refuses_unrecoverable_verified_accounts() -> None:
    with patch.dict(
        os.environ,
        {"YT_LOADER_REQUIRE_EMAIL_VERIFICATION": "true", "SMTP_HOST": "", "SMTP_FROM_EMAIL": ""},
    ):
        client = TestClient(server.app)
        response = client.post(
            "/api/auth/register",
            headers={"Origin": "http://testserver"},
            json={"email": "cannot-deliver@example.com", "password": PASSWORD},
        )
        assert response.status_code == 503
