import hashlib
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from email_validator import EmailNotValidError, validate_email
from fastapi import Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from saas_models import AccountToken, User, UserSession


SESSION_COOKIE = "yt_loader_session"
CSRF_COOKIE = "yt_loader_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/config",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/verification/confirm",
    "/api/auth/password/forgot",
    "/api/auth/password/reset",
    "/api/payments/yookassa/webhook",
}
password_hasher = PasswordHasher()
DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-user-password")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: str) -> str:
    try:
        normalized = validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError("Укажи корректный email.") from exc
    return normalized.casefold()


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise ValueError("Пароль должен содержать не менее 10 символов.")
    if len(password) > 128:
        raise ValueError("Пароль не должен превышать 128 символов.")


def hash_password(password: str) -> str:
    validate_password(password)
    return password_hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    candidate_hash = password_hash or DUMMY_PASSWORD_HASH
    try:
        return password_hasher.verify(candidate_hash, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_password_rehash(password_hash: str) -> bool:
    try:
        return password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secure_cookies() -> bool:
    return os.getenv("YT_LOADER_SECURE_COOKIES", "false").strip().lower() in {"1", "true", "yes", "on"}


def session_lifetime() -> timedelta:
    raw_days = os.getenv("YT_LOADER_SESSION_DAYS", "14")
    try:
        days = max(1, min(int(raw_days), 90))
    except ValueError:
        days = 14
    return timedelta(days=days)


def set_auth_cookies(response: Response, raw_token: str, csrf_token: str, max_age: int) -> None:
    cookie_options = {
        "max_age": max_age,
        "path": "/",
        "secure": secure_cookies(),
        "samesite": "lax",
    }
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, **cookie_options)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **cookie_options)
    response.headers["Cache-Control"] = "no-store"


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=secure_cookies(), httponly=True, samesite="lax"
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", secure=secure_cookies(), httponly=False, samesite="lax"
    )
    response.headers["Cache-Control"] = "no-store"


def create_user_session(db: Session, user: User, request: Request, response: Response) -> None:
    raw_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    lifetime = session_lifetime()
    now = utc_now()
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.expires_at <= now,
        )
    )
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=token_hash(raw_token),
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            expires_at=now + lifetime,
        )
    )
    db.commit()
    set_auth_cookies(response, raw_token, csrf_token, int(lifetime.total_seconds()))


def authenticate_request(db: Session, request: Request) -> User | None:
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    if not raw_token or len(raw_token) > 256:
        return None
    now = utc_now()
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == token_hash(raw_token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            User.status == "active",
        )
    ).first()
    if not row:
        return None
    user_session, user = row
    cutoff = now - timedelta(minutes=5)
    db.execute(
        update(UserSession)
        .where(UserSession.id == user_session.id, UserSession.last_seen_at < cutoff)
        .values(last_seen_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return user


def revoke_current_session(db: Session, request: Request) -> None:
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    if raw_token:
        db.execute(
            update(UserSession)
            .where(UserSession.token_hash == token_hash(raw_token), UserSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        db.commit()


def revoke_all_sessions(db: Session, user_id: str) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )


def create_account_token(
    db: Session,
    user_id: str,
    purpose: str,
    lifetime: timedelta,
) -> str:
    if purpose not in {"verify_email", "reset_password"}:
        raise ValueError("Unsupported account token purpose")
    now = utc_now()
    db.execute(
        update(AccountToken)
        .where(
            AccountToken.user_id == user_id,
            AccountToken.purpose == purpose,
            AccountToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    raw_token = secrets.token_urlsafe(48)
    db.add(
        AccountToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash(raw_token),
            expires_at=now + lifetime,
        )
    )
    return raw_token


def consume_account_token(db: Session, raw_token: str, purpose: str) -> AccountToken | None:
    if not raw_token or len(raw_token) > 256:
        return None
    record = db.scalar(
        select(AccountToken)
        .where(
            AccountToken.token_hash == token_hash(raw_token),
            AccountToken.purpose == purpose,
            AccountToken.used_at.is_(None),
            AccountToken.expires_at > utc_now(),
        )
        .with_for_update()
    )
    if record is not None:
        record.used_at = utc_now()
    return record


def csrf_is_valid(request: Request) -> bool:
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("X-CSRF-Token", "")
    return bool(cookie_token and header_token) and secrets.compare_digest(cookie_token, header_token)


def origin_is_allowed(request: Request) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    parsed = urlparse(origin)
    supplied = parsed.netloc.casefold()
    current = (request.headers.get("host") or "").casefold()
    configured = {
        urlparse(item.strip()).netloc.casefold()
        for item in os.getenv("YT_LOADER_TRUSTED_ORIGINS", "").split(",")
        if item.strip()
    }
    return bool(supplied) and (supplied == current or supplied in configured)


class AttemptLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= now - window_seconds:
                attempts.popleft()
            if len(attempts) >= limit:
                return False
            attempts.append(now)
            return True

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


attempt_limiter = AttemptLimiter()


def request_client_key(request: Request, action: str) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    client_ip = forwarded or (request.client.host if request.client else "unknown")
    return f"{action}:{client_ip}"
