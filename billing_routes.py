from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from billing_service import credit_snapshot, entitlement_snapshot
from database import get_db
from saas_models import ContentAttachment, CreditLedger, Job, Plan, Project, Subscription, Workspace, WorkspaceMember


router = APIRouter(prefix="/api/billing", tags=["billing"])


def plan_payload(plan: Plan) -> dict[str, object]:
    return {
        "id": plan.id,
        "name": plan.name,
        "description": plan.description,
        "monthly_credits": plan.monthly_credits,
        "price_minor": plan.price_minor,
        "currency": plan.currency,
        "limits": dict(plan.feature_limits or {}),
    }


@router.get("/plans")
def plans(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    records = db.scalars(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.id)
    ).all()
    return [plan_payload(plan) for plan in records]


@router.get("/summary")
def summary(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    user_id = str(request.state.user.id)
    credits = credit_snapshot(db, user_id)
    entitlement = entitlement_snapshot(db, user_id)
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_(["active", "past_due"]),
        ).order_by(Subscription.created_at.desc())
    )
    plan = entitlement.plan
    workspace_ids = select(Workspace.id).where(
        Workspace.owner_user_id == user_id, Workspace.status == "active"
    )
    usage = {
        "workspaces": int(db.scalar(select(func.count(Workspace.id)).where(Workspace.owner_user_id == user_id, Workspace.status == "active")) or 0),
        "projects": int(db.scalar(select(func.count(Project.id)).where(Project.workspace_id.in_(workspace_ids), Project.status == "active")) or 0),
        "members": int(db.scalar(select(func.count(WorkspaceMember.id)).where(WorkspaceMember.workspace_id.in_(workspace_ids))) or 0),
        "storage_mb": round(int(db.scalar(select(func.coalesce(func.sum(ContentAttachment.size_bytes), 0)).where(ContentAttachment.uploaded_by_user_id == user_id)) or 0) / 1024 / 1024, 1),
        "active_jobs": int(db.scalar(select(func.count(Job.id)).where(Job.user_id == user_id, Job.status.in_(["queued", "running"]))) or 0),
    }
    return {
        "balance": credits.balance,
        "reserved": credits.reserved,
        "available": credits.available,
        "plan": plan_payload(plan) if plan else None,
        "subscription_status": entitlement.status,
        "trial_expires_at": entitlement.trial_expires_at.isoformat() if entitlement.trial_expires_at else None,
        "limits": entitlement.limits,
        "usage": usage,
        "current_period_end": (
            subscription.current_period_end.isoformat()
            if subscription and subscription.current_period_end
            else None
        ),
        "cancel_at_period_end": bool(subscription.cancel_at_period_end) if subscription else False,
        "auto_renew": bool(subscription.payment_method_id) if subscription else False,
        "grace_until": (
            subscription.grace_until.isoformat()
            if subscription and subscription.grace_until
            else None
        ),
    }


@router.post("/subscription/cancel")
def cancel_subscription(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == str(request.state.user.id),
            Subscription.status.in_(["active", "past_due"]),
        )
        .order_by(Subscription.created_at.desc())
        .with_for_update()
    )
    if subscription is None:
        raise HTTPException(404, "Активная подписка не найдена")
    subscription.cancel_at_period_end = True
    db.commit()
    return {
        "status": subscription.status,
        "cancel_at_period_end": True,
        "current_period_end": (
            subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else None
        ),
    }


@router.post("/subscription/resume")
def resume_subscription(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    subscription = db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == str(request.state.user.id),
            Subscription.status == "active",
            Subscription.current_period_end > now,
        )
        .order_by(Subscription.created_at.desc())
        .with_for_update()
    )
    if subscription is None:
        raise HTTPException(409, "Подписку уже нельзя возобновить без новой оплаты")
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    db.commit()
    return {"status": "active", "cancel_at_period_end": False}


@router.get("/ledger")
def ledger(
    request: Request,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit должен быть от 1 до 200")
    records = db.scalars(
        select(CreditLedger)
        .where(CreditLedger.user_id == str(request.state.user.id))
        .order_by(CreditLedger.created_at.desc(), CreditLedger.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": record.id,
            "job_id": record.job_id,
            "amount": record.amount,
            "operation_type": record.operation_type,
            "description": record.description,
            "created_at": record.created_at.isoformat(),
        }
        for record in records
    ]
