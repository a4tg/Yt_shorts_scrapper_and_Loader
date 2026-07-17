import os
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

import payment_routes
import server
from auth_service import attempt_limiter
from legal_service import legal_config
from saas_models import User


PASSWORD = "correct horse battery staple"


class ConfiguredProvider:
    configured = True


def test_all_legal_documents_are_public_and_include_configured_seller() -> None:
    client = TestClient(server.app)
    for path in (
        "/terms",
        "/offer",
        "/privacy",
        "/personal-data-consent",
        "/refund-policy",
        "/storage-policy",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert "Test Seller" in response.text
        assert "support@example.com" in response.text

    config = client.get("/api/legal/config")
    assert config.status_code == 200
    assert config.json()["complete"] is True
    assert config.json()["version"] == "test-version"


def test_registration_can_require_and_records_explicit_legal_acceptance() -> None:
    client = TestClient(server.app)
    payload = {
        "email": f"legal-{uuid.uuid4().hex}@example.com",
        "password": PASSWORD,
    }
    with patch.dict(os.environ, {"YT_LOADER_REQUIRE_LEGAL_ACCEPTANCE": "true"}):
        rejected = client.post(
            "/api/auth/register",
            headers={"Origin": "http://testserver"},
            json=payload,
        )
        attempt_limiter.clear("register:testclient")
        accepted = client.post(
            "/api/auth/register",
            headers={"Origin": "http://testserver"},
            json={
                **payload,
                "terms_accepted": True,
                "privacy_accepted": True,
            },
        )
        attempt_limiter.clear("register:testclient")

    assert rejected.status_code == 400
    assert accepted.status_code == 201
    with server.SessionLocal() as db:
        user = db.get(User, accepted.json()["id"])
        assert user.legal_accepted_at is not None
        assert user.legal_version == "test-version"


def test_payments_stay_disabled_until_owner_explicitly_enables_them() -> None:
    client = TestClient(server.app)
    registered = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"payment-gate-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
        },
    )
    attempt_limiter.clear("register:testclient")
    assert registered.status_code == 201
    with patch.dict(
        os.environ,
        {"YT_LOADER_ENABLE_PAYMENTS": "false"},
    ), patch.object(payment_routes, "get_provider", return_value=ConfiguredProvider()):
        config = client.get("/api/payments/config")

    assert config.status_code == 200
    assert config.json()["enabled"] is False
    assert config.json()["legal_ready"] is True
    assert legal_config().complete is True
