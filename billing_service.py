import os
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from saas_models import CreditLedger, Job, User


class InsufficientCreditsError(RuntimeError):
    def __init__(self, required: int, available: int) -> None:
        self.required = required
        self.available = available
        super().__init__(
            f"Недостаточно кредитов: требуется {required}, доступно {available}."
        )


@dataclass(frozen=True)
class CreditSnapshot:
    balance: int
    reserved: int

    @property
    def available(self) -> int:
        return self.balance - self.reserved


def signup_credits() -> int:
    try:
        return max(0, min(int(os.getenv("YT_LOADER_SIGNUP_CREDITS", "5")), 10000))
    except ValueError:
        return 5


def job_credit_cost(kind: str, args: dict[str, object]) -> int:
    if kind == "import":
        try:
            requested = int(args.get("limit") or 0)
        except (TypeError, ValueError):
            requested = 0
        # "All Shorts" is bounded by the API maximum of 1000 for reservation.
        return 10 if requested <= 0 else max(1, (requested + 99) // 100)
    if kind == "download":
        # One credit per generated video variant. A download without overlays
        # still produces one output and therefore costs one credit.
        return max(1, len(list(args.get("overlays") or [])))
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
