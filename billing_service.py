import os
import uuid
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from saas_models import CreditLedger, Job, Plan, Subscription, User


class InsufficientCreditsError(RuntimeError):
    def __init__(self, required: int, available: int) -> None:
        self.required = required
        self.available = available
        super().__init__(
            f"Недостаточно кредитов: требуется {required}, доступно {available}."
        )


class SubscriptionRequiredError(RuntimeError):
    pass


class PlanLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreditSnapshot:
    balance: int
    reserved: int

    @property
    def available(self) -> int:
        return self.balance - self.reserved


@dataclass(frozen=True)
class EntitlementSnapshot:
    status: str
    plan: Plan
    trial_expires_at: datetime | None
    period_end: datetime | None

    @property
    def limits(self) -> dict[str, int]:
        return {str(key): int(value) for key, value in dict(self.plan.feature_limits or {}).items()}


def trial_days() -> int:
    try:
        return max(1, min(int(os.getenv("YT_LOADER_TRIAL_DAYS", "7")), 90))
    except ValueError:
        return 7


def entitlement_snapshot(db: Session, user_id: str) -> EntitlementSnapshot:
    now = datetime.now(timezone.utc)
    user = db.get(User, user_id)
    if user is None:
        raise KeyError(user_id)
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_(["active", "past_due"]),
        ).order_by(Subscription.created_at.desc())
    )
    subscription_status: str | None = None
    if subscription:
        period_end = subscription.current_period_end
        normalized_end = (
            period_end.replace(tzinfo=timezone.utc)
            if period_end and period_end.tzinfo is None
            else period_end
        )
        grace_until = subscription.grace_until
        normalized_grace = (
            grace_until.replace(tzinfo=timezone.utc)
            if grace_until and grace_until.tzinfo is None
            else grace_until
        )
        try:
            renewal_grace_hours = max(
                1, min(int(os.getenv("YT_LOADER_RENEWAL_GRACE_HOURS", "24")), 168)
            )
        except ValueError:
            renewal_grace_hours = 24
        if subscription.status == "active" and (
            normalized_end is None or normalized_end > now
        ):
            subscription_status = "active"
        elif (
            subscription.status == "active"
            and normalized_end is not None
            and normalized_end + timedelta(hours=renewal_grace_hours) > now
        ):
            subscription_status = "grace"
        elif (
            subscription.status == "past_due"
            and normalized_grace is not None
            and normalized_grace > now
        ):
            subscription_status = "grace"
    plan = db.get(Plan, subscription.plan_id if subscription_status else "free")
    if plan is None:
        raise RuntimeError("Тариф пользователя не найден.")
    expires = user.trial_expires_at
    normalized = expires.replace(tzinfo=timezone.utc) if expires and expires.tzinfo is None else expires
    status = subscription_status or (
        "trial" if normalized is None or normalized > now else "expired"
    )
    return EntitlementSnapshot(
        status,
        plan,
        normalized,
        subscription.current_period_end if subscription_status else None,
    )


def require_entitlement(db: Session, user_id: str) -> EntitlementSnapshot:
    entitlement = entitlement_snapshot(db, user_id)
    if entitlement.status == "expired":
        raise SubscriptionRequiredError("Пробный период завершён. Выберите тариф, чтобы продолжить работу.")
    return entitlement


def require_plan_capacity(db: Session, user_id: str, key: str, current: int, *, increment: int = 1) -> None:
    entitlement = require_entitlement(db, user_id)
    if (
        os.getenv("YT_LOADER_TEST_DISABLE_PLAN_CAPACITY", "false").lower()
        in {"1", "true", "yes", "on"}
        and "PYTEST_CURRENT_TEST" in os.environ
    ):
        return
    maximum = entitlement.limits.get(key, 0)
    if maximum and current + increment > maximum:
        raise PlanLimitError(f"Достигнут лимит тарифа: {key} — {maximum}.")


def signup_credits() -> int:
    try:
        return max(0, min(int(os.getenv("YT_LOADER_SIGNUP_CREDITS", "20")), 10000))
    except ValueError:
        return 20


CREDIT_COST_CEILING_RUB = 0.75
IMAGE_CREDIT_COSTS = {"low": 3, "medium": 15, "high": 45, "auto": 45}


def credit_rate_catalog() -> dict[str, object]:
    """Public, versioned explanation of how metered operations consume credits."""
    return {
        "version": "2026-07-22",
        "cost_ceiling_rub": CREDIT_COST_CEILING_RUB,
        "rates": {
            "import": {"credits": 1, "unit": "100 imported items"},
            "download_original": {"credits": 1, "unit": "video"},
            "overlay_variant": {"credits": 2, "unit": "rendered variant"},
            "ai_text": {"credits": 1, "unit": "standard request"},
            "ai_image_low": {"credits": 3, "unit": "image"},
            "ai_image_medium": {"credits": 15, "unit": "image"},
            "ai_image_high": {"credits": 45, "unit": "image"},
            "ai_transcription": {"credits": 1, "unit": "started 5 minutes"},
            "ai_clip": {"credits": 2, "unit": "requested output clip"},
            "ai_clips_start": {"credits": 1, "unit": "job"},
        },
    }


def _image_quality(args: dict[str, object]) -> str:
    configured = (
        os.getenv("AAP_AI_IMAGE_QUALITY")
        or os.getenv("OPENAI_IMAGE_QUALITY")
        or "low"
    )
    quality = str(args.get("quality") or configured).strip().lower()
    return quality if quality in IMAGE_CREDIT_COSTS else "high"


def job_credit_cost(kind: str, args: dict[str, object]) -> int:
    if kind == "import":
        try:
            requested = int(args.get("limit") or 0)
        except (TypeError, ValueError):
            requested = 0
        # "All Shorts" is bounded by the API maximum of 1000 for reservation.
        return 10 if requested <= 0 else max(1, (requested + 99) // 100)
    if kind == "download":
        # The original without an overlay is cheap. Each rendered overlay variant
        # uses FFmpeg CPU and costs two credits.
        overlay_count = len(list(args.get("overlays") or []))
        return 1 if overlay_count == 0 else overlay_count * 2
    if kind == "ai_text":
        return 1
    if kind == "ai_image":
        return IMAGE_CREDIT_COSTS[_image_quality(args)]
    if kind == "ai_clips":
        try:
            count = int(args.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        try:
            duration_seconds = max(0.0, float(args.get("source_duration_seconds") or 0))
        except (TypeError, ValueError):
            duration_seconds = 0.0
        transcription_units = max(1, math.ceil(duration_seconds / 300))
        return 1 + transcription_units + max(1, count) * 2
    return 0


def actual_job_credit_cost(
    kind: str,
    request_payload: dict[str, object],
    result_payload: dict[str, object],
) -> int:
    if kind == "import":
        try:
            count = max(0, int(result_payload.get("count") or 0))
        except (TypeError, ValueError):
            count = 0
        return max(1, (count + 99) // 100)
    return job_credit_cost(kind, request_payload)


def credit_snapshot(db: Session, user_id: str) -> CreditSnapshot:
    row = db.execute(
        select(User.credit_balance, User.reserved_credits).where(User.id == user_id)
    ).one_or_none()
    if row is None:
        raise KeyError(user_id)
    return CreditSnapshot(balance=int(row[0]), reserved=int(row[1]))


def grant_credits(
    db: Session,
    user_id: str,
    amount: int,
    *,
    operation_type: str,
    description: str,
    idempotency_key: str,
    payment_id: str | None = None,
) -> bool:
    if amount <= 0:
        raise ValueError("Количество начисляемых кредитов должно быть положительным.")
    existing = db.scalar(
        select(CreditLedger.id).where(CreditLedger.idempotency_key == idempotency_key)
    )
    if existing:
        return False
    changed = db.execute(
        update(User)
        .where(User.id == user_id)
        .values(credit_balance=User.credit_balance + amount)
        .returning(User.id)
    ).scalar_one_or_none()
    if changed is None:
        raise KeyError(user_id)
    db.add(
        CreditLedger(
            id=str(uuid.uuid4()),
            user_id=user_id,
            payment_id=payment_id,
            amount=amount,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            description=description[:500],
        )
    )
    # SessionLocal disables autoflush; flush so a repeated key in the same
    # transaction is visible to the next idempotency check.
    db.flush()
    return True


def revoke_credits(
    db: Session,
    user_id: str,
    amount: int,
    *,
    operation_type: str,
    description: str,
    idempotency_key: str,
    payment_id: str | None = None,
) -> bool:
    """Atomically remove available credits and append a negative audit entry."""
    if amount <= 0:
        raise ValueError("Количество списываемых кредитов должно быть положительным.")
    existing = db.scalar(
        select(CreditLedger.id).where(CreditLedger.idempotency_key == idempotency_key)
    )
    if existing:
        return False
    snapshot = credit_snapshot(db, user_id)
    changed = db.execute(
        update(User)
        .where(
            User.id == user_id,
            User.credit_balance - User.reserved_credits >= amount,
        )
        .values(credit_balance=User.credit_balance - amount)
        .returning(User.id)
    ).scalar_one_or_none()
    if changed is None:
        raise InsufficientCreditsError(amount, snapshot.available)
    db.add(
        CreditLedger(
            id=str(uuid.uuid4()),
            user_id=user_id,
            payment_id=payment_id,
            amount=-amount,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            description=description[:500],
        )
    )
    db.flush()
    return True


def reserve_credits(db: Session, user_id: str, cost: int) -> CreditSnapshot:
    if cost <= 0:
        return credit_snapshot(db, user_id)
    row = db.execute(
        update(User)
        .where(
            User.id == user_id,
            User.credit_balance - User.reserved_credits >= cost,
        )
        .values(reserved_credits=User.reserved_credits + cost)
        .returning(User.credit_balance, User.reserved_credits)
    ).one_or_none()
    if row is None:
        try:
            current = credit_snapshot(db, user_id)
        except KeyError:
            raise
        raise InsufficientCreditsError(cost, current.available)
    return CreditSnapshot(balance=int(row[0]), reserved=int(row[1]))


def release_job_reservation(db: Session, job: Job) -> int:
    amount = int(job.credits_reserved or 0)
    if amount <= 0 or not job.user_id:
        job.credits_reserved = 0
        return 0
    changed = db.execute(
        update(User)
        .where(User.id == job.user_id, User.reserved_credits >= amount)
        .values(reserved_credits=User.reserved_credits - amount)
        .returning(User.id)
    ).scalar_one_or_none()
    if changed is None:
        raise RuntimeError("Нарушен баланс зарезервированных кредитов.")
    job.credits_reserved = 0
    return amount


def charge_job_credits(db: Session, job: Job, actual_cost: int | None = None) -> int:
    reserved = int(job.credits_reserved or 0)
    amount = reserved if actual_cost is None else max(0, min(int(actual_cost), reserved))
    if reserved <= 0 or not job.user_id:
        job.credits_reserved = 0
        return 0
    changed = db.execute(
        update(User)
        .where(
            User.id == job.user_id,
            User.reserved_credits >= reserved,
            User.credit_balance >= amount,
        )
        .values(
            reserved_credits=User.reserved_credits - reserved,
            credit_balance=User.credit_balance - amount,
        )
        .returning(User.id)
    ).scalar_one_or_none()
    if changed is None:
        raise RuntimeError("Не удалось списать зарезервированные кредиты.")
    if amount:
        db.add(
            CreditLedger(
                id=str(uuid.uuid4()),
                user_id=job.user_id,
                job_id=job.id,
                amount=-amount,
                operation_type="job_charge",
                idempotency_key=f"job-charge:{job.id}",
                description=f"Обработка задания {job.kind}",
            )
        )
    job.credits_reserved = 0
    job.credits_spent += amount
    return amount
