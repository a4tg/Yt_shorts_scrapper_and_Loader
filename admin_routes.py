from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_db
from saas_models import (
    ContentAttachment,
    FeedbackTicket,
    Job,
    Payment,
    Plan,
    ProductEvent,
    Subscription,
    User,
    Workspace,
)


router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(request: Request) -> None:
    if not bool(request.state.user.is_admin):
        raise HTTPException(404, "Раздел не найден")


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
    return [{
        "id": payment.id, "email": user.email, "plan_id": payment.plan_id,
        "status": payment.status, "amount_minor": payment.amount_minor,
        "currency": payment.currency, "created_at": payment.created_at.isoformat(),
    } for payment, user in rows]
