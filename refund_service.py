from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from billing_service import grant_credits, revoke_credits
from saas_models import Payment, PaymentRefund, Subscription
from yookassa_client import value_to_minor


class RefundProvider(Protocol):
    configured: bool

    def create_refund(self, **kwargs) -> dict[str, Any]: ...
    def get_refund(self, provider_refund_id: str) -> dict[str, Any]: ...


class RefundValidationError(RuntimeError):
    pass


class RefundNotConfiguredError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def refund_payload(refund: PaymentRefund) -> dict[str, object]:
    return {
        "id": refund.id,
        "payment_id": refund.payment_id,
        "user_id": refund.user_id,
        "provider_refund_id": refund.provider_refund_id,
        "status": refund.status,
        "amount_minor": refund.amount_minor,
        "currency": refund.currency,
        "credits_reversed": refund.credits_reversed,
        "reason": refund.reason,
        "failure_reason": refund.failure_reason,
        "created_at": refund.created_at.isoformat(),
        "completed_at": refund.completed_at.isoformat() if refund.completed_at else None,
    }


def _verified_fields(
    payload: dict[str, Any],
) -> tuple[str, str, str, int, str]:
    refund_id = str(payload.get("id") or "")
    payment_id = str(payload.get("payment_id") or "")
    status = str(payload.get("status") or "")
    amount = payload.get("amount") if isinstance(payload.get("amount"), dict) else {}
    amount_minor = value_to_minor(dict(amount).get("value"))
    currency = str(dict(amount).get("currency") or "")
    if (
        not refund_id
        or not payment_id
        or status not in {"pending", "succeeded", "canceled"}
    ):
        raise RefundValidationError("ЮKassa вернула некорректный объект возврата.")
    return refund_id, payment_id, status, amount_minor, currency


def apply_verified_refund(
    db: Session,
    refund: PaymentRefund,
    payment: Payment,
    payload: dict[str, Any],
) -> bool:
    provider_id, payment_provider_id, status, amount_minor, currency = _verified_fields(payload)
    if payment_provider_id != payment.provider_payment_id:
        raise RefundValidationError("Возврат относится к другому платежу.")
    if refund.provider_refund_id and refund.provider_refund_id != provider_id:
        raise RefundValidationError("Идентификатор возврата не совпадает.")
    if amount_minor != refund.amount_minor or currency != refund.currency:
        raise RefundValidationError("Сумма или валюта возврата не совпадает.")
    refund.provider_refund_id = provider_id
    refund.provider_details = {"status": status}
    refund.failure_reason = None
    if status == "succeeded":
        if refund.status == "succeeded":
            return False
        refund.status = "succeeded"
        refund.completed_at = utc_now()
        payment.refunded_at = refund.completed_at
        if payment.subscription_id:
            subscription = db.get(Subscription, payment.subscription_id)
            if subscription:
                subscription.status = "canceled"
                subscription.cancel_at_period_end = True
                subscription.canceled_at = refund.completed_at
                subscription.current_period_end = refund.completed_at
        return True
    if status == "canceled":
        if refund.status != "canceled":
            grant_credits(
                db,
                refund.user_id,
                refund.credits_reversed,
                operation_type="refund_reversal_canceled",
                description=f"Кредиты восстановлены после отмены возврата {refund.id}",
                idempotency_key=f"refund-restore:{refund.id}",
                payment_id=payment.id,
            )
        refund.status = "canceled"
        refund.completed_at = utc_now()
        failure = payload.get("cancellation_details")
        refund.failure_reason = str(
            dict(failure).get("reason") if isinstance(failure, dict) else "Возврат отменён"
        )[:1000]
        return True
    refund.status = "pending"
    return True


def request_full_refund(
    session_factory: Callable[[], Session],
    provider: RefundProvider,
    payment_id: str,
    admin_user_id: str,
    reason: str,
) -> dict[str, object]:
    if not provider.configured:
        raise RefundNotConfiguredError("ЮKassa не настроена.")
    normalized_reason = reason.strip()
    if len(normalized_reason) < 10:
        raise RefundValidationError("Укажите причину возврата минимум из 10 символов.")
    with session_factory() as db, db.begin():
        payment = db.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if (
            payment is None
            or payment.status != "succeeded"
            or not payment.provider_payment_id
        ):
            raise RefundValidationError("Вернуть можно только подтверждённый платёж.")
        existing = db.scalar(
            select(PaymentRefund)
            .where(PaymentRefund.payment_id == payment.id)
            .with_for_update()
        )
        if existing and existing.status in {"succeeded", "canceled"}:
            return refund_payload(existing)
        if existing is None:
            refund = PaymentRefund(
                id=str(uuid.uuid4()),
                payment_id=payment.id,
                user_id=payment.user_id,
                requested_by_user_id=admin_user_id,
                idempotency_key=f"refund:{uuid.uuid4()}",
                status="creating",
                amount_minor=payment.amount_minor,
                currency=payment.currency,
                credits_reversed=payment.credits,
                reason=normalized_reason[:500],
            )
            db.add(refund)
            db.flush()
            revoke_credits(
                db,
                payment.user_id,
                payment.credits,
                operation_type="payment_refund",
                description=f"Удержание кредитов для возврата платежа {payment.id}",
                idempotency_key=f"refund-revoke:{refund.id}",
                payment_id=payment.id,
            )
        else:
            refund = existing
            if refund.reason != normalized_reason[:500]:
                refund.reason = normalized_reason[:500]
        refund_id = refund.id
        provider_payment_id = str(payment.provider_payment_id)
        amount_minor = refund.amount_minor
        currency = refund.currency
        idempotency_key = refund.idempotency_key

    try:
        verified = provider.create_refund(
            provider_payment_id=provider_payment_id,
            amount_minor=amount_minor,
            currency=currency,
            description=f"Возврат All As Planned: {normalized_reason}",
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        with session_factory() as db, db.begin():
            refund = db.get(PaymentRefund, refund_id)
            if refund:
                refund.status = "error"
                refund.failure_reason = str(exc)[:1000]
        raise

    with session_factory() as db, db.begin():
        refund = db.scalar(
            select(PaymentRefund).where(PaymentRefund.id == refund_id).with_for_update()
        )
        payment = db.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        apply_verified_refund(db, refund, payment, verified)
        return refund_payload(refund)


def sync_refund(
    session_factory: Callable[[], Session],
    provider: RefundProvider,
    refund_id: str,
) -> dict[str, object]:
    if not provider.configured:
        raise RefundNotConfiguredError("ЮKassa не настроена.")
    with session_factory() as db:
        refund = db.get(PaymentRefund, refund_id)
        if refund is None or not refund.provider_refund_id:
            raise RefundValidationError("Возврат ещё не создан в ЮKassa.")
        provider_refund_id = refund.provider_refund_id
    verified = provider.get_refund(provider_refund_id)
    with session_factory() as db, db.begin():
        refund = db.scalar(
            select(PaymentRefund).where(PaymentRefund.id == refund_id).with_for_update()
        )
        payment = db.scalar(
            select(Payment).where(Payment.id == refund.payment_id).with_for_update()
        )
        apply_verified_refund(db, refund, payment, verified)
        return refund_payload(refund)
