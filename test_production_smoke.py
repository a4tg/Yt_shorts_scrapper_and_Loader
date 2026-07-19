from __future__ import annotations

import pytest

from deploy.production_smoke import SmokeFailure, run_checks


class FakeClient:
    def __init__(self, *, ai_enabled: bool = True, payments_enabled: bool = True) -> None:
        self.pages: list[str] = []
        self.responses = {
            "/api/health": {"status": "ok"},
            "/api/health/ready": {
                "status": "ok",
                "database": "ok",
                "workers": "ok",
                "disk": "ok",
            },
            "/api/auth/config": {
                "registration_enabled": True,
                "email_verification_required": True,
                "email_delivery_enabled": True,
                "password_reset_enabled": True,
                "legal_acceptance_required": True,
            },
            "/api/legal/config": {
                "complete": True,
                "acceptance_required": True,
                "documents": {
                    "terms": "/terms",
                    "offer": "/offer",
                    "privacy": "/privacy",
                    "personal_data_consent": "/personal-data-consent",
                    "refund_policy": "/refund-policy",
                    "storage_policy": "/storage-policy",
                },
            },
            "/api/auth/login": {"authenticated": True},
            "/api/auth/me": {
                "email": "smoke@example.com",
                "email_verified": True,
            },
            "/api/workspaces": [{"id": "workspace"}],
            "/api/ai/config": {
                "enabled": ai_enabled,
                "features": ["text", "image", "transcription", "clips"],
                "text_model": "text",
                "image_model": "image",
                "transcription_model": "speech",
            },
            "/api/payments/config": {
                "enabled": payments_enabled,
                "legal_ready": True,
            },
            "/api/billing/plans": [{"code": "creator"}, {"code": "studio"}],
        }

    def json(self, path: str, **_kwargs):
        return self.responses[path]

    def page(self, path: str) -> None:
        self.pages.append(path)


def test_production_smoke_covers_complete_commercial_product() -> None:
    client = FakeClient()

    completed = run_checks(
        client,
        require_ai=True,
        commercial=True,
        email="smoke@example.com",
        password="secret",
    )

    assert "AI provider and capabilities" in completed
    assert "YooKassa and commercial plans" in completed
    assert len(client.pages) == 6


def test_production_smoke_fails_when_payment_activation_is_missing() -> None:
    with pytest.raises(SmokeFailure, match="YooKassa payments are disabled"):
        run_checks(
            FakeClient(payments_enabled=False),
            require_ai=True,
            commercial=True,
            email="smoke@example.com",
            password="secret",
        )


def test_production_smoke_requires_a_verified_account_for_private_checks() -> None:
    with pytest.raises(SmokeFailure, match="AAP_SMOKE_EMAIL"):
        run_checks(
            FakeClient(),
            require_ai=True,
            commercial=False,
        )
