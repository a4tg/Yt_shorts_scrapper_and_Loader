import os
import logging
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth_service import (
    attempt_limiter,
    clear_auth_cookies,
    consume_account_token,
    create_account_token,
    create_user_session,
    hash_password,
    needs_password_rehash,
    normalize_email,
    request_client_key,
    revoke_all_sessions,
    revoke_current_session,
    utc_now,
    verify_password,
)
from billing_service import credit_snapshot, grant_credits, signup_credits, trial_days
from database import get_db
from email_service import (
    email_features_configured,
    email_verification_required,
    send_password_reset_email,
    send_verification_email,
)
from saas_models import User
from workspace_service import create_personal_workspace


router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class ResetPasswordRequest(TokenRequest):
    password: SecretStr


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr
    new_password: SecretStr


def registration_enabled() -> bool:
    return os.getenv("YT_LOADER_ALLOW_REGISTRATION", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def user_payload(db: Session, user: User) -> dict[str, object]:
    credits = credit_snapshot(db, user.id)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "credit_balance": credits.available,
        "credit_total": credits.balance,
        "credits_reserved": credits.reserved,
        "email_verified": user.email_verified_at is not None,
    }


@router.get("/config")
def auth_config() -> dict[str, bool]:
    return {
        "registration_enabled": registration_enabled(),
        "email_verification_required": email_verification_required(),
        "email_delivery_enabled": email_features_configured(),
        "password_reset_enabled": email_features_configured(),
    }


def _send_safely(callback, email: str, token: str) -> None:
    try:
        callback(email, token)
    except Exception:
        # Tokens and SMTP credentials must never be included in logs.
        logger.exception("Не удалось отправить служебное письмо пользователю")


@router.post("/register", status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not registration_enabled():
        raise HTTPException(403, "Регистрация временно закрыта.")
    verification_required = email_verification_required()
    if verification_required and not email_features_configured():
        raise HTTPException(503, "Регистрация временно недоступна: почтовая доставка не настроена.")
    limiter_key = request_client_key(request, "register")
    if not attempt_limiter.allow(limiter_key, limit=5, window_seconds=60 * 60):
        raise HTTPException(429, "Слишком много попыток регистрации. Попробуй позже.")
    try:
        email = normalize_email(payload.email)
        password_hash = hash_password(payload.password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    display_name = (payload.display_name or "").strip()[:120] or None
    user = User(
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        email_verified_at=None if verification_required else utc_now(),
        trial_expires_at=utc_now() + timedelta(days=trial_days()),
    )
    db.add(user)
    verification_token = None
    try:
        db.flush()
        create_personal_workspace(db, user)
        initial_credits = signup_credits()
        if initial_credits:
            grant_credits(
                db,
                user.id,
                initial_credits,
                operation_type="signup_grant",
                description="Стартовые кредиты",
                idempotency_key=f"signup:{user.id}",
            )
        if verification_required:
            verification_token = create_account_token(
                db, user.id, "verify_email", timedelta(hours=24)
            )
        create_user_session(db, user, request, response)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Аккаунт с таким email уже существует.") from exc
    if verification_token:
        background_tasks.add_task(_send_safely, send_verification_email, email, verification_token)
    return user_payload(db, user)


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    limiter_key = request_client_key(request, "login")
    if not attempt_limiter.allow(limiter_key, limit=10, window_seconds=15 * 60):
        raise HTTPException(429, "Слишком много попыток входа. Попробуй позже.")
    try:
        email = normalize_email(payload.email)
    except ValueError:
        email = "invalid@example.invalid"
    user = db.scalar(select(User).where(User.email == email))
    password = payload.password.get_secret_value()
    if not verify_password(user.password_hash if user else None, password) or not user:
        raise HTTPException(401, "Неверный email или пароль.")
    if user.status != "active":
        raise HTTPException(403, "Аккаунт недоступен.")
    if user.password_hash and needs_password_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    create_user_session(db, user, request, response)
    attempt_limiter.clear(limiter_key)
    return user_payload(db, user)


@router.get("/me")
def current_user(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    return user_payload(db, request.state.user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    revoke_current_session(db, request)
    clear_auth_cookies(response)
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    return {"status": "ok"}


@router.post("/verification/request", status_code=202)
def request_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user = request.state.user
    if user.email_verified_at is not None:
        return {"status": "already_verified"}
    if not email_features_configured():
        raise HTTPException(503, "Почтовая доставка не настроена.")
    limiter_key = f"verify-email:{user.id}"
    if not attempt_limiter.allow(limiter_key, limit=3, window_seconds=60 * 60):
        raise HTTPException(429, "Новое письмо можно будет запросить позже.")
    token = create_account_token(db, user.id, "verify_email", timedelta(hours=24))
    db.commit()
    background_tasks.add_task(_send_safely, send_verification_email, user.email, token)
    return {"status": "sent"}


@router.post("/verification/confirm")
def confirm_verification(payload: TokenRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    token = consume_account_token(db, payload.token, "verify_email")
    if token is None:
        raise HTTPException(400, "Ссылка недействительна или уже использована.")
    user = db.get(User, token.user_id)
    if user is None or user.status != "active":
        raise HTTPException(400, "Ссылка недействительна или уже использована.")
    user.email_verified_at = utc_now()
    db.commit()
    return {"status": "verified"}


@router.post("/password/forgot", status_code=202)
def forgot_password(
    payload: EmailRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if not email_features_configured():
        raise HTTPException(503, "Восстановление пароля временно недоступно.")
    limiter_key = request_client_key(request, "forgot-password")
    if not attempt_limiter.allow(limiter_key, limit=5, window_seconds=60 * 60):
        raise HTTPException(429, "Слишком много запросов. Повтори позже.")
    try:
        email = normalize_email(payload.email)
    except ValueError:
        email = "invalid@example.invalid"
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if user is not None:
        token = create_account_token(db, user.id, "reset_password", timedelta(hours=1))
        db.commit()
        background_tasks.add_task(_send_safely, send_password_reset_email, user.email, token)
    return {"status": "accepted"}


@router.post("/password/reset")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        password_hash = hash_password(payload.password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    token = consume_account_token(db, payload.token, "reset_password")
    if token is None:
        raise HTTPException(400, "Ссылка недействительна или уже использована.")
    user = db.get(User, token.user_id)
    if user is None or user.status != "active":
        raise HTTPException(400, "Ссылка недействительна или уже использована.")
    user.password_hash = password_hash
    user.email_verified_at = user.email_verified_at or utc_now()
    revoke_all_sessions(db, user.id)
    db.commit()
    return {"status": "changed"}


@router.post("/password/change")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user = db.get(User, str(request.state.user.id))
    if user is None or not verify_password(user.password_hash, payload.current_password.get_secret_value()):
        raise HTTPException(400, "Текущий пароль указан неверно.")
    try:
        user.password_hash = hash_password(payload.new_password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    revoke_all_sessions(db, user.id)
    create_user_session(db, user, request, response)
    return {"status": "changed"}
