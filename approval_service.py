from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_models import ApprovalEvent, ApprovalRequest, AssetApproval


def add_approval_event(
    db: Session,
    request: ApprovalRequest,
    event_type: str,
    actor_user_id: str,
    **details: object,
) -> ApprovalEvent:
    event = ApprovalEvent(
        approval_request_id=request.id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        details={key: value for key, value in details.items() if value is not None} or None,
    )
    db.add(event)
    return event


def sync_approval_request_after_decision(
    db: Session,
    attachment_id: str,
    actor_user_id: str,
    *,
    event_type: str,
    decision: str | None = None,
    comment: str | None = None,
) -> ApprovalRequest | None:
    request = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.attachment_id == attachment_id)
    )
    if request is None or request.status == "cancelled":
        return request
    approvals = db.scalars(
        select(AssetApproval).where(AssetApproval.attachment_id == attachment_id)
    ).all()
    previous = request.status
    if any(item.decision == "changes_requested" for item in approvals):
        request.status = "changes_requested"
    elif request.assignee_user_id:
        assignee_decision = next(
            (item.decision for item in approvals if item.user_id == request.assignee_user_id),
            None,
        )
        request.status = "approved" if assignee_decision == "approved" else "pending"
    else:
        request.status = "approved" if approvals else "pending"
    add_approval_event(
        db,
        request,
        event_type,
        actor_user_id,
        decision=decision,
        comment=(comment or "").strip() or None,
        previous_status=previous,
        status=request.status,
    )
    return request
