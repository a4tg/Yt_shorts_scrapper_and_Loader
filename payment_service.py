import calendar
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from billing_service import grant_credits
from saas_models import Payment, Plan, Subscription, User, WebhookEvent
from yookassa_client import YooKassaAPIError, value_to_minor


logger = logging.getLogger(__name__)


class PaymentProvider(Protocol):
    configured: bool

    def create_payment(self, **kwargs) -> dict[str, Any]: ...
    def get_payment(self, provider_payment_id: str) -> dict[str, Any]: ...


class PaymentNotConfiguredError(RuntimeError):
    pass


class PaymentValidationError(RuntimeError):
    pass


class ActiveSubscriptionError(RuntimeError):
    pass


class PaymentInProgressError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def add_month(value: datetime) -> datetime:
    value = as_utc(value) or utc_now()
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def public_base_url() -> str:
    value = os.getenv("YT_LOADER_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not value:
        raise PaymentNotConfiguredError("Не задан YT_LOADER_PUBLIC_BASE_URL.")
    parsed = urlparse(value)
    insecure_allowed = os.getenv("YT_LOADER_ALLOW_INSECURE_PAYMENT_RETURN", "false").lower() in {
        "1", "true", "yes", "on"
    }
    if parsed.scheme != "https" and not insecure_allowed:
        raise PaymentNotConfiguredError("Публичный URL оплаты должен использовать HTTPS.")
    if not parsed.netloc or parsed.username or parsed.password:
        raise PaymentNotConfiguredError("Некорректный YT_LOADER_PUBLIC_BASE_URL.")
    return value


def validate_confirmation_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    configured = os.getenv(
        "YOOKASSA_CONFIRMATION_HOSTS", "yoomoney.ru,yookassa.ru"
    )
    allowed = tuple(item.strip().casefold().lstrip(".") for item in configured.split(",") if item.strip())
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or not any(hostname == host or hostname.endswith(f".{host}") for host in allowed)
    ):
        raise PaymentValidationError("ЮKassa вернула недоверенный URL подтверждения.")
    return value


def payment_payload(payment: Payment) -> dict[str, object]:
    return {
        "id": payment.id,
        "plan_id": payment.plan_id,
        "status": payment.status,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "credits": payment.credits,
        "confirmation_url": payment.confirmation_url,
        "created_at": payment.created_at.isoformat(),
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "refunded_at": payment.refunded_at.isoformat() if payment.refunded_at else None,
        "failure_reason": payment.failure_reason,
        "offer_accepted_at": (
            payment.offer_accepted_at.isoformat() if payment.offer_accepted_at else None
        ),
        "recurring_consent_at": (
            payment.recurring_consent_at.isoformat()
            if payment.recurring_consent_at else None
        ),
        "legal_version": payment.legal_version,
    }


def _provider_fields(payload: dict[str, Any]) -> tuple[str, str, int, str, dict[str, str]]:
    provider_id = str(payload.get("id") or "")
    status = str(payload.get("status") or "")
    amount = payload.get("amount") if isinstance(payload.get("amount"), dict) else {}
    currency = str(dict(amount).get("currency") or "")
    amount_minor = value_to_minor(dict(amount).get("value"))
    metadata_raw = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata = {str(key): str(value) for key, value in dict(metadata_raw).items()}
    if not provider_id or status not in {"pending", "waiting_for_capture", "succeeded", "canceled"}:
        raise PaymentValidationError("ЮKassa вернула некорректный объект платежа.")
    return provider_id, status, amount_minor, currency, metadata


def _validate_provider_payment(payment: Payment, payload: dict[str, Any]) -> str:
    provider_id, status, amount_minor, currency, metadata = _provider_fields(payload)
    if payment.provider_payment_id and payment.provider_payment_id != provider_id:
        raise PaymentValidationError("Идентификатор платежа не совпадает.")
    if amount_minor != payment.amount_minor or currency != payment.currency:
        raise PaymentValidationError("Сумма или валюта платежа не совпадает.")
    expected = {
        "local_payment_id": payment.id,
        "user_id": payment.user_id,
        "plan_id": str(payment.plan_id or ""),
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise PaymentValidationError("Metadata платежа не совпадает.")
    return status


def _safe_provider_details(payload: dict[str, Any]) -> dict[str, Any]:
    method = payload.get("payment_method") if isinstance(payload.get("payment_method"), dict) else {}
    cancellation = payload.get("cancellation_details")
    return {
        "test": bool(payload.get("test")),
        "paid": bool(payload.get("paid")),
        "payment_method_type": dict(method).get("type"),
        "payment_method_saved": bool(dict(method).get("saved")),
        "cancellation_details": cancellation if isinstance(cancellation, dict) else None,
    }


def _activate_subscription(db: Session, payment: Payment, payload: dict[str, Any]) -> None:
    now = utc_now()
    payment_method = payload.get("payment_method") if isinstance(payload.get("payment_method"), dict) else {}
    method_id = str(dict(payment_method).get("id") or "") or None
    method_saved = bool(dict(payment_method).get("saved"))

    subscription = db.get(Subscription, payment.subscription_id) if payment.subscription_id else None
    if subscription:
        subscription = db.scalar(
            select(Subscription)
            .where(Subscription.id == subscription.id)
            .with_for_update()
        )
        period_start = max(as_utc(subscription.current_period_end) or now, now)
    else:
        subscriptions = db.scalars(
            select(Subscription)
            .where(
                Subscription.user_id == payment.user_id,
                Subscription.status.in_(["active", "past_due"]),
            )
            .with_for_update()
        ).all()
        reusable = next(
            (item for item in subscriptions if item.plan_id == payment.plan_id and item.status == "past_due"),
            None,
        )
        for item in subscriptions:
            if item is not reusable:
                item.status = "canceled"
                item.canceled_at = now
        subscription = reusable or Subscription(
            id=str(uuid.uuid4()),
            user_id=payment.user_id,
            plan_id=str(payment.plan_id),
            provider="yookassa",
        )
        if reusable is None:
            db.add(subscription)
            db.flush()
        period_start = now

    subscription.status = "active"
    subscription.plan_id = str(payment.plan_id)
    subscription.current_period_start = period_start
    subscription.current_period_end = add_month(period_start)
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.grace_until = None
    if method_saved and method_id:
        subscription.payment_method_id = method_id
    payment.subscription_id = subscription.id
    payment.provider_payment_method_id = method_id
    grant_credits(
        db,
        payment.user_id,
        payment.credits,
        operation_type="subscription_credit",
        description=f"Тариф {payment.plan_id}: ежемесячные кредиты",
        idempotency_key=f"payment:{payment.provider_payment_id}",
        payment_id=payment.id,
    )


def apply_verified_payment(db: Session, payment: Payment, payload: dict[str, Any]) -> bool:
    status = _validate_provider_payment(payment, payload)
    provider_id = str(payload["id"])
    payment.provider_payment_id = provider_id
    payment.provider_created_at = parse_datetime(payload.get("created_at"))
    payment.details = _safe_provider_details(payload)
    payment_method = payload.get("payment_method") if isinstance(payload.get("payment_method"), dict) else {}
    payment.provider_payment_method_id = str(dict(payment_method).get("id") or "") or None

    if status == "succeeded":
        if payment.status == "succeeded":
            return False
        if not bool(payload.get("paid")):
            raise PaymentValidationError("Платёж имеет succeeded без признака paid.")
        payment.status = "succeeded"
        payment.paid_at = parse_datetime(payload.get("captured_at")) or utc_now()
        payment.failure_reason = None
        _activate_subscription(db, payment, payload)
        return True
    if status == "canceled":
        payment.status = "canceled"
        cancellation = dict(payment.details or {}).get("cancellation_details") or {}
        payment.failure_reason = str(dict(cancellation).get("reason") or "Платёж отменён")[:1000]
        if payment.subscription_id:
            subscription = db.get(Subscription, payment.subscription_id)
            if subscription and subscription.status != "canceled":
                subscription.status = "past_due"
                subscription.grace_until = subscription.grace_until or (utc_now() + timedelta(days=7))
        return True
    payment.status = status
    return True


def create_checkout_payment(
    session_factory: Callable[[], Session],
    provider: PaymentProvider,
    user_id: str,
    plan_id: str,
    *,
    legal_version: str,
) -> dict[str, object]:
    if not provider.configured:
        raise PaymentNotConfiguredError("ЮKassa пока не настроена администратором.")
    return_url_base = public_base_url()
    now = utc_now()
    with session_factory() as db, db.begin():
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        plan = db.get(Plan, plan_id)
        if user is None or plan is None or not plan.is_active or plan.price_minor <= 0:
            raise PaymentValidationError("Тариф недоступен для оплаты.")
        active = db.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.plan_id == plan_id,
                Subscription.status == "active",
                Subscription.current_period_end > now,
            )
        )
        if active:
            raise ActiveSubscriptionError("Этот тариф уже активен.")
        existing = db.scalar(
            select(Payment)
            .where(
                Payment.user_id == user_id,
                Payment.plan_id == plan_id,
                Payment.status.in_(["creating", "pending"]),
                Payment.created_at >= now - timedelta(minutes=30),
            )
            .order_by(Payment.created_at.desc())
        )
        if existing:
            if existing.status == "creating" and not existing.confirmation_url:
                raise PaymentInProgressError("Платёж уже создаётся. Повтори запрос через несколько секунд.")
            return payment_payload(existing)
        payment = Payment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            plan_id=plan.id,
            provider="yookassa",
            provider_payment_id=None,
            idempotency_key=f"checkout:{uuid.uuid4()}",
            status="creating",
            amount_minor=plan.price_minor,
            currency=plan.currency,
            credits=plan.monthly_credits,
            offer_accepted_at=now,
            recurring_consent_at=now,
            legal_version=legal_version[:40],
        )
        db.add(payment)
        db.flush()
        payment_id = payment.id
        amount_minor = payment.amount_minor
        currency = payment.currency
        idempotency_key = payment.idempotency_key
        email = user.email
        plan_name = plan.name

    metadata = {"local_payment_id": payment_id, "user_id": user_id, "plan_id": plan_id}
    try:
        provider_payload = provider.create_payment(
            amount_minor=amount_minor,
            currency=currency,
            description=f"YT Loader — тариф {plan_name}",
            idempotency_key=idempotency_key,
            metadata=metadata,
            return_url=f"{return_url_base}/?payment={payment_id}",
            customer_email=email,
            save_payment_method=True,
        )
    except Exception as exc:
        with session_factory() as db, db.begin():
            failed = db.get(Payment, payment_id)
            if failed:
                failed.status = "error"
                failed.failure_reason = str(exc)[:1000]
        raise

    with session_factory() as db, db.begin():
        stored = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
        _validate_provider_payment(stored, provider_payload)
        stored.provider_payment_id = str(provider_payload["id"])
        confirmation = provider_payload.get("confirmation")
        confirmation_url = (
            str(dict(confirmation).get("confirmation_url") or "")
            if isinstance(confirmation, dict)
            else None
        ) or None
        stored.confirmation_url = validate_confirmation_url(confirmation_url)
        apply_verified_payment(db, stored, provider_payload)
        result = payment_payload(stored)
    return result


def process_webhook(
    session_factory: Callable[[], Session],
    provider: PaymentProvider,
    notification: dict[str, Any],
    source_ip: str | None,
) -> str:
    event_type = str(notification.get("event") or "")
    object_body = notification.get("object") if isinstance(notification.get("object"), dict) else {}
    provider_payment_id = str(dict(object_body).get("id") or "")
    if notification.get("type") != "notification" or not event_type.startswith("payment."):
        raise PaymentValidationError("Некорректное событие webhook.")
    if not provider_payment_id or len(provider_payment_id) > 160:
        raise PaymentValidationError("Некорректный ID платежа в webhook.")

    verified = provider.get_payment(provider_payment_id)
    verified_id, verified_status, _amount, _currency, metadata = _provider_fields(verified)
    if verified_id != provider_payment_id:
        raise PaymentValidationError("ЮKassa подтвердила другой платёж.")
    event_key = f"yookassa:{provider_payment_id}:{verified_status}"
    redacted_payload = {
        "event": event_type,
        "verified_status": verified_status,
        "test": bool(verified.get("test")),
    }

    processing_error: Exception | None = None
    result = "processed"
    with session_factory() as db, db.begin():
        existing = db.scalar(select(WebhookEvent).where(WebhookEvent.event_key == event_key))
        if existing and existing.status == "processed":
            return "duplicate"
        event = existing or WebhookEvent(
            provider="yookassa",
            event_key=event_key,
            event_type=event_type,
            object_id=provider_payment_id,
            source_ip=source_ip,
            payload=redacted_payload,
            status="received",
        )
        if existing is None:
            db.add(event)
        payment = db.scalar(
            select(Payment)
            .where(Payment.provider_payment_id == provider_payment_id)
            .with_for_update()
        )
        if payment is None and metadata.get("local_payment_id"):
            candidate = db.scalar(
                select(Payment)
                .where(Payment.id == metadata["local_payment_id"])
                .with_for_update()
            )
            if candidate and candidate.provider_payment_id is None:
                candidate.provider_payment_id = provider_payment_id
                payment = candidate
        if payment is None:
            event.status = "ignored"
            event.error_message = "Платёж не найден в локальной базе"
            event.processed_at = utc_now()
            result = "ignored"
        else:
            try:
                apply_verified_payment(db, payment, verified)
            except Exception as exc:
                event.status = "error"
                event.error_message = str(exc)[:1000]
                event.processed_at = utc_now()
                processing_error = exc
            else:
                event.status = "processed"
                event.processed_at = utc_now()
    if processing_error:
        raise processing_error
    return result


class SubscriptionRenewalWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        provider_factory: Callable[[], PaymentProvider],
    ) -> None:
        self.session_factory = session_factory
        self.provider_factory = provider_factory
        self.interval = max(60, int(os.getenv("YT_LOADER_RENEWAL_INTERVAL_SECONDS", "3600")))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="subscription-renewal")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def healthy(self) -> bool:
        return self._thread is None or self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Ошибка worker автопродления подписок")
            self._stop.wait(self.interval)

    def run_once(self) -> int:
        provider = self.provider_factory()
        if not provider.configured:
            return 0
        now = utc_now()
        with self.session_factory() as db:
            due_ids = db.scalars(
                select(Subscription.id).where(
                    Subscription.status.in_(["active", "past_due"]),
                    Subscription.current_period_end.is_not(None),
                    Subscription.current_period_end <= now,
                )
            ).all()
        processed = 0
        for subscription_id in due_ids:
            if self._renew_one(provider, str(subscription_id)):
                processed += 1
        return processed

    def _renew_one(self, provider: PaymentProvider, subscription_id: str) -> bool:
        now = utc_now()
        existing_provider_id: str | None = None
        with self.session_factory() as db, db.begin():
            subscription = db.scalar(
                select(Subscription)
                .where(Subscription.id == subscription_id)
                .with_for_update()
            )
            if not subscription or not subscription.current_period_end:
                return False
            period_end = as_utc(subscription.current_period_end)
            if period_end and period_end > now:
                return False
            if subscription.cancel_at_period_end:
                subscription.status = "canceled"
                subscription.canceled_at = now
                return True
            if not subscription.payment_method_id:
                subscription.status = "past_due"
                subscription.grace_until = subscription.grace_until or (now + timedelta(days=7))
                return True
            plan = db.get(Plan, subscription.plan_id)
            user = db.get(User, subscription.user_id)
            if not plan or not plan.is_active or not user:
                subscription.status = "past_due"
                return True
            period_key = f"renewal:{subscription.id}:{period_end.isoformat()}"
            consent_payment = db.scalar(
                select(Payment)
                .where(
                    Payment.subscription_id == subscription.id,
                    Payment.recurring_consent_at.is_not(None),
                )
                .order_by(Payment.created_at)
                .limit(1)
            )
            payment = db.scalar(
                select(Payment).where(Payment.billing_period_key == period_key)
            )
            if payment and payment.provider_payment_id:
                payment_id = payment.id
                existing_provider_id = payment.provider_payment_id
            else:
                if payment and payment.status == "creating":
                    updated_at = as_utc(payment.updated_at) or now
                    if updated_at >= now - timedelta(minutes=5):
                        return False
                if payment is None:
                    payment = Payment(
                        id=str(uuid.uuid4()),
                        user_id=user.id,
                        plan_id=plan.id,
                        subscription_id=subscription.id,
                        provider="yookassa",
                        provider_payment_id=None,
                        idempotency_key=f"renewal:{uuid.uuid4()}",
                        billing_period_key=period_key,
                        status="creating",
                        amount_minor=plan.price_minor,
                        currency=plan.currency,
                        credits=plan.monthly_credits,
                        offer_accepted_at=(
                            consent_payment.offer_accepted_at if consent_payment else None
                        ),
                        recurring_consent_at=(
                            consent_payment.recurring_consent_at if consent_payment else None
                        ),
                        legal_version=(
                            consent_payment.legal_version if consent_payment else None
                        ),
                    )
                    db.add(payment)
                    db.flush()
                else:
                    payment.status = "creating"
                    payment.failure_reason = None
                payment_id = payment.id
                amount_minor = payment.amount_minor
                currency = payment.currency
                idempotency_key = payment.idempotency_key
                method_id = subscription.payment_method_id
                subscription.renewal_attempted_at = now
                email = user.email
                renewal_user_id = user.id
                renewal_plan_id = plan.id
                plan_name = plan.name

        if existing_provider_id:
            provider_payload = provider.get_payment(existing_provider_id)
            with self.session_factory() as db, db.begin():
                stored = db.scalar(
                    select(Payment).where(Payment.id == payment_id).with_for_update()
                )
                if stored is None:
                    return False
                apply_verified_payment(db, stored, provider_payload)
            return True

        metadata = {
            "local_payment_id": payment_id,
            "user_id": renewal_user_id,
            "plan_id": renewal_plan_id,
        }
        try:
            provider_payload = provider.create_payment(
                amount_minor=amount_minor,
                currency=currency,
                description=f"YT Loader — продление {plan_name}",
                idempotency_key=idempotency_key,
                metadata=metadata,
                payment_method_id=method_id,
                customer_email=email,
            )
        except Exception as exc:
            with self.session_factory() as db, db.begin():
                failed = db.get(Payment, payment_id)
                subscription = db.get(Subscription, subscription_id)
                if failed:
                    failed.status = "error"
                    failed.failure_reason = str(exc)[:1000]
                if subscription:
                    subscription.status = "past_due"
                    subscription.grace_until = subscription.grace_until or (utc_now() + timedelta(days=7))
            raise

        with self.session_factory() as db, db.begin():
            stored = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
            stored.provider_payment_id = str(provider_payload["id"])
            apply_verified_payment(db, stored, provider_payload)
        return True
