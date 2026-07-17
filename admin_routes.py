import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import database
from billing_service import InsufficientCreditsError, credit_snapshot, grant_credits
from database import get_db
from refund_service import (
    RefundNotConfiguredError,
    RefundValidationError,
    request_full_refund,
    sync_refund,
)
from saas_models import (
    AdminAuditLog,
    ContentAttachment,
    FeedbackTicket,
    Job,
    Payment,
    PaymentRefund,
    Plan,
    ProductEvent,
    Subscription,
    User,
    Workspace,
)
from yookassa_client import YooKassaAPIError, YooKassaClient


router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreditGrantRequest(BaseModel):
    amount: int = Field(ge=1, le=100_000)
    reason: str = Field(min_length=10, max_length=500)


class FeedbackUpdateRequest(BaseModel):
    status: str = Field(pattern=r"^(open|in_progress|resolved|closed)$")
    resolution_note: str | None = Field(default=None, max_length=2000)


class RefundRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


def _require_admin(request: Request) -> None:
    if not bool(request.state.user.is_admin):
        raise HTTPException(404, "Раздел не найден")


def _audit(
    db: Session,
    request: Request,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            actor_user_id=request.state.user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or None,
        )
    )


def get_refund_provider() -> YooKassaClient:
    return YooKassaClient()


@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    _require_admin(request)
    now = datetime.now(timezone.utc)
    active_subscriptions = db.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == "active", Subscription.current_period_end > now
        )
    ) or 0
    mrr_minor = db.scalar(
        select(func.coalesce(func.sum(Plan.price_minor), 0))
        .select_from(Subscription).join(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.status == "active", Subscription.current_period_end > now)
    ) or 0
    job_rows = db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    active_users_7d = db.scalar(
        select(func.count(func.distinct(ProductEvent.user_id))).where(
            ProductEvent.created_at >= now - timedelta(days=7)
        )
    ) or 0
    return {
        "users": int(db.scalar(select(func.count(User.id))) or 0),
        "verified_users": int(db.scalar(select(func.count(User.id)).where(User.email_verified_at.is_not(None))) or 0),
        "workspaces": int(db.scalar(select(func.count(Workspace.id)).where(Workspace.status == "active")) or 0),
        "active_subscriptions": int(active_subscriptions),
        "mrr_minor": int(mrr_minor),
        "storage_bytes": int(db.scalar(select(func.coalesce(func.sum(ContentAttachment.size_bytes), 0))) or 0),
        "active_users_7d": int(active_users_7d),
        "completed_onboarding": int(db.scalar(
            select(func.count(func.distinct(ProductEvent.user_id))).where(
                ProductEvent.event_name == "onboarding_completed"
            )
        ) or 0),
        "open_feedback": int(db.scalar(
            select(func.count(FeedbackTicket.id)).where(
                FeedbackTicket.status.in_(["open", "in_progress"])
            )
        ) or 0),
        "jobs": {str(status): int(count) for status, count in job_rows},
    }


@router.get("/users")
def users(request: Request, limit: int = 100, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    _require_admin(request)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit должен быть от 1 до 500")
    records = db.scalars(select(User).order_by(User.created_at.desc()).limit(limit)).all()
    result = []
    for user in records:
        subscription = db.scalar(
            select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.created_at.desc())
        )
        result.append({
            "id": user.id, "email": user.email, "display_name": user.display_name,
            "status": user.status, "is_admin": user.is_admin,
            "email_verified": user.email_verified_at is not None,
            "credits": user.credit_balance - user.reserved_credits,
            "trial_expires_at": user.trial_expires_at.isoformat() if user.trial_expires_at else None,
            "plan_id": subscription.plan_id if subscription else "free",
            "subscription_status": subscription.status if subscription else "trial",
            "created_at": user.created_at.isoformat(),
        })
    return result


@router.get("/payments")
def payments(request: Request, limit: int = 100, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    _require_admin(request)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit должен быть от 1 до 500")
    rows = db.execute(
        select(Payment, User).join(User, User.id == Payment.user_id)
        .order_by(Payment.created_at.desc()).limit(limit)
    ).all()
    result = []
    for payment, user in rows:
        refund = db.scalar(
            select(PaymentRefund).where(PaymentRefund.payment_id == payment.id)
        )
        result.append({
            "id": payment.id, "email": user.email, "plan_id": payment.plan_id,
            "status": payment.status, "amount_minor": payment.amount_minor,
            "currency": payment.currency, "created_at": payment.created_at.isoformat(),
            "refunded_at": payment.refunded_at.isoformat() if payment.refunded_at else None,
            "refund": (
                {
                    "id": refund.id,
                    "status": refund.status,
                    "failure_reason": refund.failure_reason,
                }
                if refund else None
            ),
        })
    return result


@router.post("/users/{user_id}/credits")
def grant_user_credits(
    user_id: str,
    payload: CreditGrantRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, int]:
    _require_admin(request)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден.")
    grant_credits(
        db,
        user.id,
        payload.amount,
        operation_type="admin_grant",
        description=payload.reason.strip(),
        idempotency_key=f"admin-grant:{request.state.user.id}:{uuid.uuid4()}",
    )
    _audit(
        db,
        request,
        "credits.grant",
        "user",
        user.id,
        {"amount": payload.amount, "reason": payload.reason.strip()},
    )
    db.commit()
    return {"available": credit_snapshot(db, user.id).available}


@router.get("/jobs")
def jobs(
    request: Request,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    _require_admin(request)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit должен быть от 1 до 500")
    statement = (
        select(Job, User)
        .outerjoin(User, User.id == Job.user_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    if status:
        if status not in {"queued", "running", "done", "error", "deleted"}:
            raise HTTPException(400, "Некорректный статус задания.")
        statement = statement.where(Job.status == status)
    rows = db.execute(statement).all()
    return [
        {
            "id": job.id,
            "email": user.email if user else None,
            "kind": job.kind,
            "status": job.status,
            "message": job.message,
            "error": job.error_message,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "credits_reserved": job.credits_reserved,
            "credits_spent": job.credits_spent,
            "created_at": job.created_at.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }
        for job, user in rows
    ]


@router.get("/feedback")
def feedback(
    request: Request,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    _require_admin(request)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit должен быть от 1 до 500")
    statement = (
        select(FeedbackTicket, User)
        .join(User, User.id == FeedbackTicket.user_id)
        .order_by(FeedbackTicket.created_at.desc())
        .limit(limit)
    )
    if status:
        if status not in {"open", "in_progress", "resolved", "closed"}:
            raise HTTPException(400, "Некорректный статус обращения.")
        statement = statement.where(FeedbackTicket.status == status)
    rows = db.execute(statement).all()
    return [
        {
            "id": ticket.id,
            "email": user.email,
            "category": ticket.category,
            "page": ticket.page,
            "message": ticket.message,
            "status": ticket.status,
            "resolution_note": ticket.resolution_note,
            "created_at": ticket.created_at.isoformat(),
            "updated_at": ticket.updated_at.isoformat(),
        }
        for ticket, user in rows
    ]


@router.patch("/feedback/{ticket_id}")
def update_feedback(
    ticket_id: str,
    payload: FeedbackUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_admin(request)
    ticket = db.get(FeedbackTicket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "Обращение не найдено.")
    note = (payload.resolution_note or "").strip() or None
    if payload.status in {"resolved", "closed"} and not note:
        raise HTTPException(400, "Для завершения укажите результат.")
    ticket.status = payload.status
    ticket.resolution_note = note
    ticket.resolved_at = (
        datetime.now(timezone.utc)
        if payload.status in {"resolved", "closed"}
        else None
    )
    _audit(
        db,
        request,
        "feedback.update",
        "feedback",
        ticket.id,
        {"status": ticket.status},
    )
    db.commit()
    return {
        "id": ticket.id,
        "status": ticket.status,
        "resolution_note": ticket.resolution_note,
    }


@router.get("/refunds")
def refunds(
    request: Request,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    _require_admin(request)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit должен быть от 1 до 500")
    rows = db.execute(
        select(PaymentRefund, User)
        .join(User, User.id == PaymentRefund.user_id)
        .order_by(PaymentRefund.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": refund.id,
            "payment_id": refund.payment_id,
            "email": user.email,
            "status": refund.status,
            "amount_minor": refund.amount_minor,
            "currency": refund.currency,
            "credits_reversed": refund.credits_reversed,
            "reason": refund.reason,
            "failure_reason": refund.failure_reason,
            "created_at": refund.created_at.isoformat(),
        }
        for refund, user in rows
    ]


@router.post("/payments/{payment_id}/refund", status_code=202)
def refund_payment(
    payment_id: str,
    payload: RefundRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_admin(request)
    try:
        result = request_full_refund(
            lambda: database.SessionLocal(),
            get_refund_provider(),
            payment_id,
            str(request.state.user.id),
            payload.reason,
        )
    except InsufficientCreditsError as exc:
        _audit(
            db,
            request,
            "payment.refund_failed",
            "payment",
            payment_id,
            {"reason": "insufficient_credits", "required": exc.required, "available": exc.available},
        )
        db.commit()
        raise HTTPException(
            409,
            f"У пользователя недостаточно неиспользованных кредитов для возврата: "
            f"нужно {exc.required}, доступно {exc.available}.",
        ) from exc
    except (RefundValidationError, RefundNotConfiguredError) as exc:
        _audit(
            db,
            request,
            "payment.refund_failed",
            "payment",
            payment_id,
            {"reason": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(409, str(exc)) from exc
    except YooKassaAPIError as exc:
        _audit(
            db,
            request,
            "payment.refund_failed",
            "payment",
            payment_id,
            {"reason": "provider_error"},
        )
        db.commit()
        raise HTTPException(502, str(exc)) from exc
    _audit(
        db,
        request,
        "payment.refund",
        "payment",
        payment_id,
        {"refund_id": result["id"], "reason": payload.reason.strip()},
    )
    db.commit()
    return result


@router.post("/refunds/{refund_id}/sync")
def sync_payment_refund(
    refund_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_admin(request)
    try:
        result = sync_refund(
            lambda: database.SessionLocal(),
            get_refund_provider(),
            refund_id,
        )
    except (RefundValidationError, RefundNotConfiguredError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except YooKassaAPIError as exc:
        raise HTTPException(502, str(exc)) from exc
    _audit(
        db,
        request,
        "refund.sync",
        "refund",
        refund_id,
        {"status": result["status"]},
    )
    db.commit()
    return result


@router.get("/audit")
def audit_log(
    request: Request,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    _require_admin(request)
    if not 1 <= limit <= 500:
        raise HTTPException(400, "limit должен быть от 1 до 500")
    rows = db.execute(
        select(AdminAuditLog, User)
        .join(User, User.id == AdminAuditLog.actor_user_id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": entry.id,
            "actor": user.email,
            "action": entry.action,
            "target_type": entry.target_type,
            "target_id": entry.target_id,
            "details": entry.details or {},
            "created_at": entry.created_at.isoformat(),
        }
        for entry, user in rows
    ]
