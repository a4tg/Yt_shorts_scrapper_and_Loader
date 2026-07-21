import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import admin_routes
import server
from auth_service import attempt_limiter
from billing_service import credit_snapshot, grant_credits
from saas_models import (
    AdminAuditLog,
    CreditLedger,
    Payment,
    PaymentRefund,
    Subscription,
    User,
)
from yookassa_client import YooKassaAPIError


PASSWORD = "correct horse battery staple"


class FakeRefundProvider:
    configured = True

    def __init__(self, status: str = "succeeded") -> None:
        self.status = status
        self.refunds: dict[str, dict[str, object]] = {}
        self.idempotency: dict[str, str] = {}
        self.fail_once = False
        self.calls = 0

    def create_refund(self, **kwargs) -> dict[str, object]:
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            raise YooKassaAPIError("temporary refund failure")
        key = kwargs["idempotency_key"]
        refund_id = self.idempotency.setdefault(key, f"refund-{uuid.uuid4().hex}")
        payload = {
            "id": refund_id,
            "payment_id": kwargs["provider_payment_id"],
            "status": self.status,
            "amount": {
                "value": f"{kwargs['amount_minor'] / 100:.2f}",
                "currency": kwargs["currency"],
            },
        }
        if self.status == "canceled":
            payload["cancellation_details"] = {"reason": "provider_declined"}
        self.refunds[refund_id] = payload
        return dict(payload)

    def get_refund(self, provider_refund_id: str) -> dict[str, object]:
        payload = dict(self.refunds[provider_refund_id])
        payload["status"] = self.status
        return payload


def register_user(prefix: str) -> tuple[TestClient, dict[str, object]]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"{prefix}-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
        },
    )
    attempt_limiter.clear("register:testclient")
    assert response.status_code == 201, response.text
    client.headers.update({
        "Origin": "http://testserver",
        "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
    })
    return client, response.json()


def make_admin(client_payload: dict[str, object]) -> None:
    with server.SessionLocal() as db:
        user = db.get(User, client_payload["id"])
        user.is_admin = True
        db.commit()


def paid_subscription(user_id: str) -> str:
    with server.SessionLocal() as db, db.begin():
        subscription = Subscription(
            user_id=user_id,
            plan_id="creator",
            provider="yookassa",
            provider_subscription_id=f"subscription-{uuid.uuid4()}",
            status="active",
            current_period_start=datetime.now(timezone.utc),
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            payment_method_id="method-test",
        )
        db.add(subscription)
        db.flush()
        payment = Payment(
            user_id=user_id,
            plan_id="creator",
            subscription_id=subscription.id,
            provider="yookassa",
            provider_payment_id=f"payment-{uuid.uuid4()}",
            idempotency_key=f"checkout-{uuid.uuid4()}",
            status="succeeded",
            amount_minor=149000,
            currency="RUB",
            credits=200,
            paid_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        db.flush()
        grant_credits(
            db,
            user_id,
            200,
            operation_type="subscription_credit",
            description="Creator monthly credits",
            idempotency_key=f"payment:{payment.provider_payment_id}",
            payment_id=payment.id,
        )
        return payment.id


def test_admin_full_refund_is_idempotent_reverses_credits_and_cancels_subscription(
    monkeypatch,
) -> None:
    admin_client, admin = register_user("refund-admin")
    make_admin(admin)
    _, customer = register_user("refund-customer")
    payment_id = paid_subscription(str(customer["id"]))
    provider = FakeRefundProvider()
    monkeypatch.setattr(admin_routes, "get_refund_provider", lambda: provider)

    first = admin_client.post(
        f"/api/admin/payments/{payment_id}/refund",
        json={"reason": "Пользователь запросил полный возврат."},
    )
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "succeeded"

    repeated = admin_client.post(
        f"/api/admin/payments/{payment_id}/refund",
        json={"reason": "Пользователь запросил полный возврат."},
    )
    assert repeated.status_code == 202
    assert repeated.json()["id"] == first.json()["id"]
    assert provider.calls == 1

    with server.SessionLocal() as db:
        payment = db.get(Payment, payment_id)
        subscription = db.get(Subscription, payment.subscription_id)
        refund = db.scalar(
            select(PaymentRefund).where(PaymentRefund.payment_id == payment_id)
        )
        reversal_count = db.scalar(
            select(func.count(CreditLedger.id)).where(
                CreditLedger.payment_id == payment_id,
                CreditLedger.operation_type == "payment_refund",
            )
        )
        audit_count = db.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.action == "payment.refund",
                AdminAuditLog.target_id == payment_id,
            )
        )
        assert payment.refunded_at is not None
        assert refund.status == "succeeded"
        assert subscription.status == "canceled"
        assert credit_snapshot(db, str(customer["id"])).available == 20
        assert reversal_count == 1
        assert audit_count == 2


def test_canceled_refund_restores_held_credits(monkeypatch) -> None:
    admin_client, admin = register_user("cancel-admin")
    make_admin(admin)
    _, customer = register_user("cancel-customer")
    payment_id = paid_subscription(str(customer["id"]))
    provider = FakeRefundProvider(status="canceled")
    monkeypatch.setattr(admin_routes, "get_refund_provider", lambda: provider)

    response = admin_client.post(
        f"/api/admin/payments/{payment_id}/refund",
        json={"reason": "Тест отменённого возврата провайдера."},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "canceled"
    with server.SessionLocal() as db:
        assert credit_snapshot(db, str(customer["id"])).available == 220
        restored = db.scalar(
            select(func.count(CreditLedger.id)).where(
                CreditLedger.payment_id == payment_id,
                CreditLedger.operation_type == "refund_reversal_canceled",
            )
        )
        assert restored == 1


def test_failed_provider_call_can_be_retried_without_second_credit_reversal(
    monkeypatch,
) -> None:
    admin_client, admin = register_user("retry-admin")
    make_admin(admin)
    _, customer = register_user("retry-customer")
    payment_id = paid_subscription(str(customer["id"]))
    provider = FakeRefundProvider()
    provider.fail_once = True
    monkeypatch.setattr(admin_routes, "get_refund_provider", lambda: provider)

    failed = admin_client.post(
        f"/api/admin/payments/{payment_id}/refund",
        json={"reason": "Проверка повторяемого запроса возврата."},
    )
    assert failed.status_code == 502
    with server.SessionLocal() as db:
        assert credit_snapshot(db, str(customer["id"])).available == 20
        failed_audit = db.scalar(
            select(func.count(AdminAuditLog.id)).where(
                AdminAuditLog.action == "payment.refund_failed",
                AdminAuditLog.target_id == payment_id,
            )
        )
        assert failed_audit == 1

    retried = admin_client.post(
        f"/api/admin/payments/{payment_id}/refund",
        json={"reason": "Проверка повторяемого запроса возврата."},
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "succeeded"
    with server.SessionLocal() as db:
        reversals = db.scalar(
            select(func.count(CreditLedger.id)).where(
                CreditLedger.payment_id == payment_id,
                CreditLedger.operation_type == "payment_refund",
            )
        )
        assert reversals == 1
