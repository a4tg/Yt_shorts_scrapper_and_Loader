import os
import uuid
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import server
from auth_service import attempt_limiter
from billing_service import credit_snapshot
from payment_service import SubscriptionRenewalWorker, as_utc, utc_now
from saas_models import CreditLedger, Payment, Subscription, User, WebhookEvent
from yookassa_client import webhook_ip_allowed


class FakeYooKassa:
    configured = True

    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.payments: dict[str, dict[str, object]] = {}
        self.next_status = "pending"

    def create_payment(self, **kwargs):
        self.create_calls.append(kwargs)
        provider_id = f"provider-{len(self.create_calls)}-{uuid.uuid4().hex[:8]}"
        status = self.next_status
        payload = {
            "id": provider_id,
            "status": status,
            "paid": status == "succeeded",
            "amount": {
                "value": f"{int(kwargs['amount_minor']) / 100:.2f}",
                "currency": kwargs["currency"],
            },
            "metadata": kwargs["metadata"],
            "created_at": utc_now().isoformat(),
            "captured_at": utc_now().isoformat() if status == "succeeded" else None,
            "test": True,
            "payment_method": {
                "id": "saved-method-1",
                "type": "bank_card",
                "saved": status == "succeeded",
            },
        }
        if kwargs.get("return_url"):
            payload["confirmation"] = {
                "type": "redirect",
                "confirmation_url": f"https://yoomoney.ru/test/{provider_id}",
            }
        self.payments[provider_id] = payload
        return payload

    def get_payment(self, provider_payment_id: str):
        return self.payments[provider_payment_id]

    def succeed(self, provider_payment_id: str, *, saved: bool = True) -> None:
        payment = self.payments[provider_payment_id]
        payment["status"] = "succeeded"
        payment["paid"] = True
        payment["captured_at"] = utc_now().isoformat()
        payment["payment_method"]["saved"] = saved


def registered_client() -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"payments-{uuid.uuid4().hex}@example.com",
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
    return client, response.json()


def webhook(client: TestClient, provider: FakeYooKassa, provider_id: str, event: str = "payment.succeeded"):
    with patch.dict(os.environ, {"YOOKASSA_WEBHOOK_ENFORCE_IP": "false"}), patch(
        "payment_routes.get_provider", return_value=provider
    ):
        return client.post(
            "/api/payments/yookassa/webhook",
            json={
                "type": "notification",
                "event": event,
                "object": {"id": provider_id, "status": event.split(".")[-1]},
            },
        )


def create_checkout(client: TestClient, provider: FakeYooKassa, plan_id: str = "creator"):
    with patch.dict(
        os.environ,
        {
            "YT_LOADER_PUBLIC_BASE_URL": "https://shorts.example.test",
            "YOOKASSA_WEBHOOK_ENFORCE_IP": "false",
        },
    ), patch("payment_routes.get_provider", return_value=provider):
        return client.post(
            "/api/payments/checkout",
            json={
                "plan_id": plan_id,
                "recurring_consent": True,
                "offer_accepted": True,
            },
        )


def test_checkout_requires_explicit_recurring_consent() -> None:
    client, _user = registered_client()
    provider = FakeYooKassa()
    with patch.dict(
        os.environ,
        {"YT_LOADER_PUBLIC_BASE_URL": "https://shorts.example.test"},
    ), patch("payment_routes.get_provider", return_value=provider):
        response = client.post(
            "/api/payments/checkout",
            json={
                "plan_id": "creator",
                "recurring_consent": False,
                "offer_accepted": True,
            },
        )
    assert response.status_code == 400
    assert "автопродление" in response.json()["detail"]
    assert provider.create_calls == []


def test_checkout_requires_explicit_offer_acceptance() -> None:
    client, _user = registered_client()
    provider = FakeYooKassa()
    with patch.dict(
        os.environ,
        {"YT_LOADER_PUBLIC_BASE_URL": "https://shorts.example.test"},
    ), patch("payment_routes.get_provider", return_value=provider):
        response = client.post(
            "/api/payments/checkout",
            json={
                "plan_id": "creator",
                "recurring_consent": True,
                "offer_accepted": False,
            },
        )
    assert response.status_code == 400
    assert provider.create_calls == []


def test_checkout_is_idempotent_for_double_click_and_scoped_to_user() -> None:
    client, _user = registered_client()
    other_client, _ = registered_client()
    provider = FakeYooKassa()

    first = create_checkout(client, provider)
    second = create_checkout(client, provider)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
    assert len(provider.create_calls) == 1
    assert first.json()["confirmation_url"].startswith("https://yoomoney.ru/")
    assert provider.create_calls[0]["save_payment_method"] is True
    assert first.json()["offer_accepted_at"]
    assert first.json()["recurring_consent_at"]
    assert first.json()["legal_version"] == "test-version"
    with server.SessionLocal() as db:
        payment = db.get(Payment, first.json()["id"])
        assert payment.offer_accepted_at is not None
        assert payment.recurring_consent_at is not None
        assert payment.legal_version == "test-version"
    assert other_client.get(f"/api/payments/{first.json()['id']}").status_code == 404


def test_webhook_uses_provider_status_and_duplicate_does_not_double_grant() -> None:
    client, user = registered_client()
    provider = FakeYooKassa()
    checkout = create_checkout(client, provider).json()
    provider_id = next(iter(provider.payments))

    # A forged succeeded body cannot grant credits while provider GET still says pending.
    pending = webhook(client, provider, provider_id)
    assert pending.status_code == 200
    assert client.get("/api/billing/summary").json()["balance"] == 5

    provider.succeed(provider_id)
    succeeded = webhook(client, provider, provider_id)
    assert succeeded.status_code == 200, succeeded.text
    assert succeeded.json()["status"] == "processed"
    summary = client.get("/api/billing/summary").json()
    assert summary["balance"] == 205
    assert summary["plan"]["id"] == "creator"
    assert summary["auto_renew"] is True

    with server.SessionLocal() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == str(user["id"]))
        )
        period_end = subscription.current_period_end
        grant_count = db.scalar(
            select(func.count())
            .select_from(CreditLedger)
            .where(CreditLedger.payment_id == checkout["id"])
        )
        assert grant_count == 1

    duplicate = webhook(client, provider, provider_id)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    with server.SessionLocal() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == str(user["id"]))
        )
        assert subscription.current_period_end == period_end
        assert credit_snapshot(db, str(user["id"])).balance == 205


def test_verified_amount_mismatch_is_rejected_and_audited() -> None:
    client, user = registered_client()
    provider = FakeYooKassa()
    create_checkout(client, provider)
    provider_id = next(iter(provider.payments))
    provider.succeed(provider_id)
    provider.payments[provider_id]["amount"]["value"] = "1.00"

    response = webhook(client, provider, provider_id)
    assert response.status_code == 409
    with server.SessionLocal() as db:
        assert credit_snapshot(db, str(user["id"])).balance == 5
        event = db.scalar(
            select(WebhookEvent).where(WebhookEvent.object_id == provider_id)
        )
        assert event is not None and event.status == "error"


def test_subscription_can_be_canceled_and_resumed_before_period_end() -> None:
    client, _user = registered_client()
    provider = FakeYooKassa()
    create_checkout(client, provider)
    provider_id = next(iter(provider.payments))
    provider.succeed(provider_id)
    webhook(client, provider, provider_id)

    canceled = client.post("/api/billing/subscription/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["cancel_at_period_end"] is True
    resumed = client.post("/api/billing/subscription/resume")
    assert resumed.status_code == 200
    assert resumed.json()["cancel_at_period_end"] is False


def test_renewal_uses_saved_method_and_grants_next_period_once() -> None:
    client, user = registered_client()
    provider = FakeYooKassa()
    create_checkout(client, provider)
    initial_provider_id = next(iter(provider.payments))
    provider.succeed(initial_provider_id)
    webhook(client, provider, initial_provider_id)

    with server.SessionLocal() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == str(user["id"]))
        )
        subscription.current_period_end = utc_now() - timedelta(seconds=1)
        db.commit()
    provider.next_status = "succeeded"
    worker = SubscriptionRenewalWorker(lambda: server.SessionLocal(), lambda: provider)
    assert worker.run_once() == 1
    assert len(provider.create_calls) == 2
    recurring_call = provider.create_calls[-1]
    assert recurring_call["payment_method_id"] == "saved-method-1"
    assert recurring_call.get("return_url") is None

    with server.SessionLocal() as db:
        assert credit_snapshot(db, str(user["id"])).balance == 405
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == str(user["id"]))
        )
        assert as_utc(subscription.current_period_end) > utc_now()
        renewal_count = db.scalar(
                select(func.count())
                .select_from(Payment)
                .where(
                    Payment.subscription_id == subscription.id,
                    Payment.billing_period_key.is_not(None),
                )
        )
        assert renewal_count == 1
        renewal = db.scalar(
            select(Payment).where(Payment.billing_period_key.is_not(None))
        )
        assert renewal.recurring_consent_at is not None
        assert renewal.legal_version == "test-version"
    assert worker.run_once() == 0


def test_failed_renewal_grace_keeps_access_then_expires() -> None:
    client, user = registered_client()
    provider = FakeYooKassa()
    create_checkout(client, provider)
    provider_id = next(iter(provider.payments))
    provider.succeed(provider_id)
    webhook(client, provider, provider_id)

    with server.SessionLocal() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == str(user["id"]))
        )
        subscription.status = "past_due"
        subscription.current_period_end = utc_now() - timedelta(hours=1)
        subscription.grace_until = utc_now() + timedelta(days=3)
        account = db.get(User, str(user["id"]))
        account.trial_expires_at = utc_now() - timedelta(days=1)
        db.commit()

    grace = client.get("/api/billing/summary")
    assert grace.status_code == 200
    assert grace.json()["subscription_status"] == "grace"
    assert grace.json()["plan"]["id"] == "creator"
    assert grace.json()["grace_until"]

    with server.SessionLocal() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == str(user["id"]))
        )
        subscription.grace_until = utc_now() - timedelta(seconds=1)
        db.commit()
    expired = client.get("/api/billing/summary").json()
    assert expired["subscription_status"] == "expired"
    assert expired["plan"]["id"] == "free"


def test_renewal_recovers_when_webhook_is_missing() -> None:
    client, user = registered_client()
    provider = FakeYooKassa()
    create_checkout(client, provider)
    initial_provider_id = next(iter(provider.payments))
    provider.succeed(initial_provider_id)
    webhook(client, provider, initial_provider_id)

    with server.SessionLocal() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == str(user["id"]))
        )
        subscription.current_period_end = utc_now() - timedelta(seconds=1)
        db.commit()

    worker = SubscriptionRenewalWorker(lambda: server.SessionLocal(), lambda: provider)
    provider.next_status = "pending"
    assert worker.run_once() == 1
    renewal_provider_id = list(provider.payments)[-1]
    assert len(provider.create_calls) == 2
    with server.SessionLocal() as db:
        assert credit_snapshot(db, str(user["id"])).balance == 205

    # No webhook is delivered. The next renewal pass asks YooKassa for the
    # already-created payment instead of creating a second charge.
    provider.succeed(renewal_provider_id)
    assert worker.run_once() == 1
    assert len(provider.create_calls) == 2
    with server.SessionLocal() as db:
        assert credit_snapshot(db, str(user["id"])).balance == 405
        payment = db.scalar(
            select(Payment).where(Payment.provider_payment_id == renewal_provider_id)
        )
        assert payment.status == "succeeded"
    assert worker.run_once() == 0


def test_webhook_ip_allowlist_rejects_untrusted_addresses() -> None:
    with patch.dict(os.environ, {"YOOKASSA_WEBHOOK_ENFORCE_IP": "true"}):
        assert webhook_ip_allowed("185.71.76.10")
        assert webhook_ip_allowed("2a02:5180::1")
        assert not webhook_ip_allowed("203.0.113.10")


def test_return_page_can_safely_sync_status_if_webhook_is_delayed() -> None:
    client, user = registered_client()
    provider = FakeYooKassa()
    checkout = create_checkout(client, provider).json()
    provider_id = next(iter(provider.payments))
    provider.succeed(provider_id)
    with patch("payment_routes.get_provider", return_value=provider):
        response = client.post(f"/api/payments/{checkout['id']}/sync")
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    with server.SessionLocal() as db:
        assert credit_snapshot(db, str(user["id"])).balance == 205
