import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

import database
from auth_service import attempt_limiter
from payment_service import (
    ActiveSubscriptionError,
    apply_verified_payment,
    PaymentInProgressError,
    PaymentNotConfiguredError,
    PaymentValidationError,
    create_checkout_payment,
    payment_payload,
    process_webhook,
    public_base_url,
)
from saas_models import Payment
from legal_service import commercial_payments_ready, legal_config
from yookassa_client import (
    YooKassaAPIError,
    YooKassaClient,
    YooKassaConfigurationError,
    webhook_ip_allowed,
)


router = APIRouter(prefix="/api/payments", tags=["payments"])
MAX_WEBHOOK_BYTES = 128 * 1024


class CheckoutRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    recurring_consent: bool = False
    offer_accepted: bool = False


def get_provider() -> YooKassaClient:
    return YooKassaClient()


@router.get("/config")
def payment_config() -> dict[str, object]:
    provider = get_provider()
    ready = provider.configured
    if ready:
        try:
            public_base_url()
        except PaymentNotConfiguredError:
            ready = False
    legal = legal_config()
    ready = commercial_payments_ready(provider.configured, ready)
    return {
        "enabled": ready,
        "provider": "yookassa",
        "recurring": True,
        "legal_ready": legal.complete,
    }


@router.post("/checkout", status_code=201)
def create_checkout(payload: CheckoutRequest, request: Request) -> dict[str, object]:
    provider = get_provider()
    public_url_ready = True
    try:
        public_base_url()
    except PaymentNotConfiguredError:
        public_url_ready = False
    if not commercial_payments_ready(provider.configured, public_url_ready):
        raise HTTPException(503, "Приём платежей ещё не активирован владельцем сервиса.")
    if not payload.offer_accepted:
        raise HTTPException(400, "Подтвердите принятие публичной оферты.")
    if not payload.recurring_consent:
        raise HTTPException(
            400,
            "Подтвердите ежемесячное автопродление перед переходом к оплате.",
        )
    limiter_key = f"checkout:{request.state.user.id}"
    if not attempt_limiter.allow(limiter_key, limit=10, window_seconds=10 * 60):
        raise HTTPException(429, "Слишком много попыток оплаты. Повтори позже.")
    try:
        return create_checkout_payment(
            lambda: database.SessionLocal(),
            provider,
            str(request.state.user.id),
            payload.plan_id,
        )
    except PaymentNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (PaymentValidationError, ActiveSubscriptionError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except PaymentInProgressError as exc:
        raise HTTPException(425, str(exc)) from exc
    except (YooKassaAPIError, YooKassaConfigurationError) as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("")
def list_payments(request: Request, limit: int = 50) -> list[dict[str, object]]:
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit должен быть от 1 до 200")
    user = request.state.user
    with database.SessionLocal() as db:
        statement = select(Payment)
        if not user.is_admin:
            statement = statement.where(Payment.user_id == str(user.id))
        records = db.scalars(
            statement.order_by(Payment.created_at.desc()).limit(limit)
        ).all()
        return [payment_payload(payment) for payment in records]


@router.post("/{payment_id}/sync")
def sync_payment(payment_id: str, request: Request) -> dict[str, object]:
    user = request.state.user
    provider = get_provider()
    if not provider.configured:
        raise HTTPException(503, "ЮKassa не настроена")
    with database.SessionLocal() as db:
        payment = db.get(Payment, payment_id)
        if payment is None or (payment.user_id != str(user.id) and not user.is_admin):
            raise HTTPException(404, "Платёж не найден")
        provider_payment_id = payment.provider_payment_id
    if not provider_payment_id:
        raise HTTPException(409, "Платёж ещё создаётся")
    try:
        verified = provider.get_payment(provider_payment_id)
        with database.SessionLocal() as db, db.begin():
            payment = db.scalar(
                select(Payment).where(Payment.id == payment_id).with_for_update()
            )
            apply_verified_payment(db, payment, verified)
            result = payment_payload(payment)
        return result
    except YooKassaAPIError as exc:
        raise HTTPException(502, str(exc)) from exc
    except PaymentValidationError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{payment_id}")
def get_payment(payment_id: str, request: Request) -> dict[str, object]:
    if len(payment_id) > 36:
        raise HTTPException(404, "Платёж не найден")
    user = request.state.user
    with database.SessionLocal() as db:
        payment = db.get(Payment, payment_id)
        if payment is None or (payment.user_id != str(user.id) and not user.is_admin):
            raise HTTPException(404, "Платёж не найден")
        return payment_payload(payment)


@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request) -> dict[str, str]:
    source_ip = request.client.host if request.client else None
    if not webhook_ip_allowed(source_ip):
        raise HTTPException(403, "Недоверенный источник webhook")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "Webhook слишком большой")
        except ValueError as exc:
            raise HTTPException(400, "Некорректный Content-Length") from exc
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "Webhook слишком большой")
    try:
        notification = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Некорректный JSON") from exc
    if not isinstance(notification, dict):
        raise HTTPException(400, "Webhook должен быть JSON-объектом")
    provider = get_provider()
    if not provider.configured:
        raise HTTPException(503, "ЮKassa не настроена")
    try:
        result = process_webhook(
            lambda: database.SessionLocal(), provider, notification, source_ip
        )
    except YooKassaAPIError as exc:
        # Non-200 asks YooKassa to retry delivery later.
        raise HTTPException(503, str(exc)) from exc
    except PaymentValidationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": result}
