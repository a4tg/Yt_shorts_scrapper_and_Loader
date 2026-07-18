from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from approval_service import add_approval_event
from asset_review_routes import _review_summary
from content_routes import _attachment_access, _attachment_payload, _require_editor
from database import get_db
from realtime_service import project_events
from saas_models import (
    ApprovalEvent,
    ApprovalRequest,
    ApprovalStage,
    ApprovalWorkflow,
    AssetApproval,
    ContentAttachment,
    ContentItem,
    Project,
    User,
    WorkspaceMember,
)
from workspace_service import project_membership


router = APIRouter(prefix="/api", tags=["approval-queue"])
Visibility = Literal["team", "client"]


class ApprovalRequestCreate(BaseModel):
    assignee_user_id: str | None = Field(default=None, max_length=36)
    stage_id: str | None = Field(default=None, max_length=36)
    due_at: datetime | None = None
    visibility: Visibility = "team"
    note: str | None = Field(default=None, max_length=10_000)


class ApprovalRequestUpdate(BaseModel):
    assignee_user_id: str | None = Field(default=None, max_length=36)
    due_at: datetime | None = None
    visibility: Visibility | None = None
    note: str | None = Field(default=None, max_length=10_000)
    status: Literal["pending", "cancelled"] | None = None


def _user_payload(user: User | None) -> dict[str, str] | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.display_name or user.email, "email": user.email}


def _event(project_id: str, event_type: str, user_id: str, **payload: object) -> None:
    project_events.publish(project_id, {
        "type": event_type,
        "project_id": project_id,
        "actor_user_id": user_id,
        **payload,
    })


def _is_overdue(value: datetime | None, status: str) -> bool:
    if value is None or status != "pending":
        return False
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        now = now.replace(tzinfo=None)
    return value < now


def _validate_assignee(
    db: Session,
    project: Project,
    user_id: str | None,
    visibility: str,
) -> User | None:
    if not user_id:
        return None
    member = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == project.workspace_id,
        WorkspaceMember.user_id == user_id,
    ))
    if member is None or member.role not in {"owner", "admin", "editor", "client"}:
        raise HTTPException(400, "Ответственный должен иметь право согласовывать материалы.")
    if visibility == "team" and member.role == "client":
        raise HTTPException(400, "Клиента нельзя назначить на скрытое согласование.")
    return db.get(User, user_id)


def _validate_stage(
    db: Session,
    project_id: str,
    stage_id: str | None,
) -> ApprovalStage | None:
    if not stage_id:
        return None
    stage = db.scalar(
        select(ApprovalStage)
        .join(ApprovalWorkflow, ApprovalWorkflow.id == ApprovalStage.workflow_id)
        .where(ApprovalStage.id == stage_id, ApprovalWorkflow.project_id == project_id)
    )
    if stage is None:
        raise HTTPException(400, "Этап не принадлежит процессу проекта.")
    return stage


def _request_payload(
    db: Session,
    approval_request: ApprovalRequest,
    member: WorkspaceMember,
    viewer_id: str,
) -> dict[str, object]:
    attachment = db.get(ContentAttachment, approval_request.attachment_id)
    content = db.get(ContentItem, attachment.content_item_id) if attachment and attachment.content_item_id else None
    stage = db.get(ApprovalStage, approval_request.stage_id) if approval_request.stage_id else None
    requester = db.get(User, approval_request.requested_by_user_id)
    assignee = db.get(User, approval_request.assignee_user_id) if approval_request.assignee_user_id else None
    summary = _review_summary(db, attachment, member, viewer_id)
    return {
        "id": approval_request.id,
        "project_id": approval_request.project_id,
        "status": approval_request.status,
        "visibility": approval_request.visibility,
        "note": approval_request.note,
        "due_at": approval_request.due_at.isoformat() if approval_request.due_at else None,
        "overdue": _is_overdue(approval_request.due_at, approval_request.status),
        "requested_by": _user_payload(requester),
        "assignee": _user_payload(assignee),
        "stage": {
            "id": stage.id,
            "name": stage.name,
            "color": stage.color,
            "required_role": stage.required_role,
        } if stage else None,
        "content": {
            "id": content.id,
            "title": content.title,
            "item_type": content.item_type,
        } if content else None,
        "attachment": _attachment_payload(attachment),
        "created_at": approval_request.created_at.isoformat(),
        "updated_at": approval_request.updated_at.isoformat(),
        **summary,
    }


@router.get("/projects/{project_id}/approval-queue")
def list_approval_queue(
    project_id: str,
    request: Request,
    status: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    access = project_membership(db, project_id, request.state.user.id)
    if access is None:
        raise HTTPException(404, "Проект не найден.")
    _, member = access
    statement = select(ApprovalRequest).where(ApprovalRequest.project_id == project_id)
    if member.role == "client":
        statement = statement.where(ApprovalRequest.visibility == "client")
    if status and status != "all":
        if status == "overdue":
            statement = statement.where(
                ApprovalRequest.status == "pending",
                ApprovalRequest.due_at < datetime.now(timezone.utc),
            )
        elif status in {"pending", "approved", "changes_requested", "cancelled"}:
            statement = statement.where(ApprovalRequest.status == status)
    requests = db.scalars(
        statement.order_by(ApprovalRequest.due_at.asc().nullslast(), ApprovalRequest.created_at.desc())
    ).all()
    all_visible = db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.project_id == project_id,
            *((ApprovalRequest.visibility == "client",) if member.role == "client" else ()),
        )
    ).all()
    summary = {
        "total": len(all_visible),
        "pending": sum(item.status == "pending" for item in all_visible),
        "approved": sum(item.status == "approved" for item in all_visible),
        "changes_requested": sum(item.status == "changes_requested" for item in all_visible),
        "overdue": sum(_is_overdue(item.due_at, item.status) for item in all_visible),
    }
    return {
        "requests": [_request_payload(db, item, member, request.state.user.id) for item in requests],
        "summary": summary,
    }


@router.post("/content-attachments/{attachment_id}/approval-request", status_code=201)
def create_approval_request(
    attachment_id: str,
    payload: ApprovalRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    attachment, project, member = _attachment_access(db, attachment_id, request.state.user.id)
    _require_editor(member)
    if not attachment.is_current:
        raise HTTPException(409, "На согласование можно отправить только актуальную версию файла.")
    _validate_assignee(db, project, payload.assignee_user_id, payload.visibility)
    stage = _validate_stage(db, project.id, payload.stage_id)
    if stage is None and attachment.content_item_id:
        content = db.get(ContentItem, attachment.content_item_id)
        stage = db.get(ApprovalStage, content.stage_id) if content and content.stage_id else None
    approval_request = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.attachment_id == attachment.id)
    )
    event_type = "requested"
    if approval_request is None:
        approval_request = ApprovalRequest(
            project_id=project.id,
            attachment_id=attachment.id,
            requested_by_user_id=request.state.user.id,
        )
        db.add(approval_request)
    elif approval_request.status in {"approved", "changes_requested"}:
        raise HTTPException(409, "Этот файл уже получил решение. Загрузите новую версию для следующего круга.")
    elif approval_request.status == "cancelled":
        db.execute(delete(AssetApproval).where(AssetApproval.attachment_id == attachment.id))
        approval_request.status = "pending"
        event_type = "reopened"
    else:
        event_type = "updated"
    approval_request.assignee_user_id = payload.assignee_user_id
    approval_request.stage_id = stage.id if stage else None
    approval_request.due_at = payload.due_at
    approval_request.visibility = payload.visibility
    approval_request.note = (payload.note or "").strip() or None
    db.flush()
    add_approval_event(
        db,
        approval_request,
        event_type,
        request.state.user.id,
        assignee_user_id=payload.assignee_user_id,
        due_at=payload.due_at.isoformat() if payload.due_at else None,
        visibility=payload.visibility,
    )
    db.commit()
    _event(project.id, "approval.request.updated", request.state.user.id, approval_request_id=approval_request.id)
    return _request_payload(db, approval_request, member, request.state.user.id)


@router.patch("/approval-requests/{request_id}")
def update_approval_request(
    request_id: str,
    payload: ApprovalRequestUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    approval_request = db.get(ApprovalRequest, request_id)
    if approval_request is None:
        raise HTTPException(404, "Согласование не найдено.")
    access = project_membership(db, approval_request.project_id, request.state.user.id)
    if access is None:
        raise HTTPException(404, "Согласование не найдено.")
    project, member = access
    _require_editor(member)
    values = payload.model_dump(exclude_unset=True)
    visibility = values.get("visibility", approval_request.visibility)
    assignee_id = values.get("assignee_user_id", approval_request.assignee_user_id)
    _validate_assignee(db, project, assignee_id, visibility)
    previous = approval_request.status
    for field in ("assignee_user_id", "due_at", "visibility"):
        if field in values:
            setattr(approval_request, field, values[field])
    if "note" in values:
        approval_request.note = (values["note"] or "").strip() or None
    if values.get("status"):
        approval_request.status = values["status"]
    add_approval_event(
        db,
        approval_request,
        "cancelled" if approval_request.status == "cancelled" else "updated",
        request.state.user.id,
        previous_status=previous,
        status=approval_request.status,
    )
    db.commit()
    _event(project.id, "approval.request.updated", request.state.user.id, approval_request_id=approval_request.id)
    return _request_payload(db, approval_request, member, request.state.user.id)


@router.get("/approval-requests/{request_id}/history")
def approval_request_history(
    request_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    approval_request = db.get(ApprovalRequest, request_id)
    if approval_request is None:
        raise HTTPException(404, "Согласование не найдено.")
    access = project_membership(db, approval_request.project_id, request.state.user.id)
    if access is None or (access[1].role == "client" and approval_request.visibility != "client"):
        raise HTTPException(404, "Согласование не найдено.")
    rows = db.execute(
        select(ApprovalEvent, User)
        .join(User, User.id == ApprovalEvent.actor_user_id)
        .where(ApprovalEvent.approval_request_id == approval_request.id)
        .order_by(ApprovalEvent.created_at.desc(), ApprovalEvent.id.desc())
    ).all()
    return [{
        "id": event.id,
        "event_type": event.event_type,
        "actor": _user_payload(user),
        "details": event.details or {},
        "created_at": event.created_at.isoformat(),
    } for event, user in rows]
