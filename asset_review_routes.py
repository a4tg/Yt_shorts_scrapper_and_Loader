from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from content_routes import _attachment_access, _attachment_payload, _require_editor, _store_upload
from database import get_db
from realtime_service import project_events
from saas_models import AssetApproval, AssetReview, ContentAttachment, User, WorkspaceMember
from workspace_service import has_role


router = APIRouter(prefix="/api", tags=["asset-reviews"])
AnnotationType = Literal["general", "point", "region", "timestamp", "page", "drawing"]
ReviewStatus = Literal["open", "in_progress", "resolved", "wont_fix"]
Visibility = Literal["team", "client"]


class ReviewCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    annotation_type: AnnotationType = "general"
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    width: float | None = Field(default=None, gt=0, le=1)
    height: float | None = Field(default=None, gt=0, le=1)
    time_seconds: float | None = Field(default=None, ge=0, le=86_400)
    page_number: int | None = Field(default=None, ge=1, le=100_000)
    annotation_data: dict[str, object] | None = None
    assignee_user_id: str | None = Field(default=None, max_length=36)
    visibility: Visibility = "team"
    parent_review_id: str | None = Field(default=None, max_length=36)


class ReviewUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=10_000)
    status: ReviewStatus | None = None
    assignee_user_id: str | None = Field(default=None, max_length=36)
    visibility: Visibility | None = None


class ApprovalCreate(BaseModel):
    decision: Literal["approved", "changes_requested"]
    comment: str | None = Field(default=None, max_length=10_000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_payload(user: User | None) -> dict[str, str] | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.display_name or user.email, "email": user.email}


def _event(project_id: str, event_type: str, user_id: str, **payload: object) -> None:
    project_events.publish(project_id, {
        "type": event_type, "project_id": project_id, "actor_user_id": user_id, **payload,
    })


def _validate_assignee(
    db: Session,
    workspace_id: str,
    user_id: str | None,
    *,
    visibility: Visibility,
) -> None:
    if not user_id:
        return
    assignee = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id,
    ))
    if assignee is None:
        raise HTTPException(400, "Ответственный должен состоять в рабочем пространстве.")
    if visibility == "team" and assignee.role == "client":
        raise HTTPException(400, "Клиента нельзя назначить на скрытое командное замечание.")


def _require_decider(member: WorkspaceMember) -> None:
    if member.role not in {"owner", "admin", "editor", "client"}:
        raise HTTPException(403, "Эта роль не может принимать решение по материалу.")


def _require_visible_review(review: AssetReview, member: WorkspaceMember) -> None:
    if member.role == "client" and review.visibility != "client":
        raise HTTPException(404, "Замечание не найдено.")


def _validate_annotation(payload: ReviewCreate) -> None:
    if payload.annotation_data is not None and len(json.dumps(payload.annotation_data)) > 100_000:
        raise HTTPException(413, "Аннотация слишком большая.")
    required = {
        "point": (payload.x, payload.y), "region": (payload.x, payload.y, payload.width, payload.height),
        "timestamp": (payload.time_seconds,), "page": (payload.page_number,),
        "drawing": (payload.annotation_data,),
    }.get(payload.annotation_type, ())
    if any(value is None for value in required):
        raise HTTPException(400, "Для выбранного типа замечания не хватает координат, страницы или таймкода.")
    if payload.x is not None and payload.width is not None and payload.x + payload.width > 1:
        raise HTTPException(400, "Область выходит за правую границу файла.")
    if payload.y is not None and payload.height is not None and payload.y + payload.height > 1:
        raise HTTPException(400, "Область выходит за нижнюю границу файла.")


def _review_payload(db: Session, review: AssetReview, viewer_id: str) -> dict[str, object]:
    author = db.get(User, review.author_user_id)
    assignee = db.get(User, review.assignee_user_id) if review.assignee_user_id else None
    resolver = db.get(User, review.resolved_by_user_id) if review.resolved_by_user_id else None
    return {
        "id": review.id, "attachment_id": review.attachment_id, "parent_review_id": review.parent_review_id,
        "author": _user_payload(author), "assignee": _user_payload(assignee), "body": review.body,
        "annotation_type": review.annotation_type, "x": review.x, "y": review.y,
        "width": review.width, "height": review.height, "time_seconds": review.time_seconds,
        "page_number": review.page_number, "annotation_data": review.annotation_data,
        "status": review.status, "visibility": review.visibility, "resolved_by": _user_payload(resolver),
        "resolved_at": review.resolved_at.isoformat() if review.resolved_at else None,
        "created_at": review.created_at.isoformat(), "updated_at": review.updated_at.isoformat(),
        "is_own": review.author_user_id == viewer_id,
    }


def _visible_reviews(db: Session, attachment: ContentAttachment, member: WorkspaceMember):
    statement = select(AssetReview).where(AssetReview.attachment_id == attachment.id)
    if member.role == "client":
        statement = statement.where(AssetReview.visibility == "client")
    return db.scalars(statement.order_by(AssetReview.created_at, AssetReview.id)).all()


def _approval_payload(db: Session, approval: AssetApproval, viewer_id: str) -> dict[str, object]:
    return {
        "id": approval.id, "decision": approval.decision, "comment": approval.comment,
        "user": _user_payload(db.get(User, approval.user_id)), "decided_at": approval.decided_at.isoformat(),
        "is_own": approval.user_id == viewer_id,
    }


def _review_summary(db: Session, attachment: ContentAttachment, member: WorkspaceMember, viewer_id: str) -> dict[str, object]:
    reviews = _visible_reviews(db, attachment, member)
    approvals = db.scalars(select(AssetApproval).where(
        AssetApproval.attachment_id == attachment.id
    ).order_by(AssetApproval.decided_at.desc())).all()
    counts = {status: 0 for status in ("open", "in_progress", "resolved", "wont_fix")}
    for review in reviews:
        counts[review.status] += 1
    decisions = [_approval_payload(db, approval, viewer_id) for approval in approvals]
    return {
        "review_counts": counts, "open_count": counts["open"] + counts["in_progress"],
        "approvals": decisions,
        "approval_state": (
            "changes_requested" if any(item["decision"] == "changes_requested" for item in decisions)
            else "approved" if decisions and all(item["decision"] == "approved" for item in decisions)
            else "pending"
        ),
    }


@router.get("/content-attachments/{attachment_id}/versions")
def list_asset_versions(attachment_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    versions = db.scalars(select(ContentAttachment).where(
        ContentAttachment.asset_key == attachment.asset_key
    ).order_by(ContentAttachment.version_number.desc())).all()
    return {
        "asset_key": attachment.asset_key,
        "current_attachment_id": next((item.id for item in versions if item.is_current), versions[0].id),
        "versions": [{**_attachment_payload(item), **_review_summary(db, item, member, request.state.user.id)} for item in versions],
    }


@router.post("/content-attachments/{attachment_id}/versions", status_code=201)
async def upload_asset_version(
    attachment_id: str, request: Request, file: UploadFile,
    label: str | None = Form(default=None, max_length=120),
    notes: str | None = Form(default=None, max_length=10_000),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    _require_editor(member)
    current = db.scalar(select(ContentAttachment).where(
        ContentAttachment.asset_key == attachment.asset_key, ContentAttachment.is_current.is_(True),
    )) or attachment
    number = int(db.scalar(select(func.max(ContentAttachment.version_number)).where(
        ContentAttachment.asset_key == attachment.asset_key
    )) or 0) + 1
    current.is_current = False
    created = await _store_upload(
        file, project_id=current.project_id, user_id=request.state.user.id, db=db,
        content_item_id=current.content_item_id, folder_id=current.folder_id, ensure_unique_name=False,
        asset_key=current.asset_key, version_number=number, version_label=(label or "").strip() or None,
        version_notes=(notes or "").strip() or None, supersedes_attachment_id=current.id,
    )
    _event(current.project_id, "asset.version.created", request.state.user.id,
           attachment_id=created.id, asset_key=created.asset_key, version_number=created.version_number)
    return _attachment_payload(created)


@router.get("/content-attachments/{attachment_id}/reviews")
def list_asset_reviews(attachment_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    reviews = _visible_reviews(db, attachment, member)
    return {"reviews": [_review_payload(db, review, request.state.user.id) for review in reviews],
            **_review_summary(db, attachment, member, request.state.user.id)}


@router.post("/content-attachments/{attachment_id}/reviews", status_code=201)
def create_asset_review(
    attachment_id: str, payload: ReviewCreate, request: Request, db: Session = Depends(get_db),
) -> dict[str, object]:
    attachment, project, member = _attachment_access(db, attachment_id, request.state.user.id)
    _validate_annotation(payload)
    if member.role == "client" and payload.visibility != "client":
        raise HTTPException(403, "Клиентские замечания должны быть видимы заказчику.")
    visibility = payload.visibility
    if payload.parent_review_id:
        parent = db.get(AssetReview, payload.parent_review_id)
        if parent is None or parent.attachment_id != attachment.id:
            raise HTTPException(400, "Ответ относится к другому файлу.")
        _require_visible_review(parent, member)
        visibility = parent.visibility
    _validate_assignee(
        db, project.workspace_id, payload.assignee_user_id, visibility=visibility,
    )
    review = AssetReview(
        attachment_id=attachment.id, parent_review_id=payload.parent_review_id,
        author_user_id=request.state.user.id, assignee_user_id=payload.assignee_user_id,
        body=payload.body.strip(), annotation_type=payload.annotation_type,
        x=payload.x, y=payload.y, width=payload.width, height=payload.height,
        time_seconds=payload.time_seconds, page_number=payload.page_number,
        annotation_data=payload.annotation_data, visibility=visibility, status="open",
    )
    db.add(review); db.commit()
    _event(attachment.project_id, "asset.review.created", request.state.user.id,
           attachment_id=attachment.id, review_id=review.id)
    return _review_payload(db, review, request.state.user.id)


@router.patch("/asset-reviews/{review_id}")
def update_asset_review(
    review_id: str, payload: ReviewUpdate, request: Request, db: Session = Depends(get_db),
) -> dict[str, object]:
    review = db.get(AssetReview, review_id)
    if review is None:
        raise HTTPException(404, "Замечание не найдено.")
    attachment, project, member = _attachment_access(db, review.attachment_id, request.state.user.id)
    _require_visible_review(review, member)
    is_assignee = review.assignee_user_id == request.state.user.id
    is_author = review.author_user_id == request.state.user.id
    if not (has_role(member, "editor") or is_author or is_assignee):
        raise HTTPException(403, "Недостаточно прав для изменения замечания.")
    values = payload.model_dump(exclude_unset=True)
    if "body" in values:
        if not (is_author or has_role(member, "admin")):
            raise HTTPException(403, "Текст может изменить автор или администратор.")
        review.body = values["body"].strip()
    if "visibility" in values:
        if member.role == "client" or not has_role(member, "editor"):
            raise HTTPException(403, "Видимость замечания меняет редактор.")
        review.visibility = values["visibility"]
    if "assignee_user_id" in values:
        if not has_role(member, "editor"):
            raise HTTPException(403, "Ответственного назначает редактор.")
        _validate_assignee(
            db, project.workspace_id, values["assignee_user_id"],
            visibility=review.visibility,
        )
        review.assignee_user_id = values["assignee_user_id"]
    if "status" in values:
        review.status = values["status"]
        if review.status in {"resolved", "wont_fix"}:
            review.resolved_at = _now(); review.resolved_by_user_id = request.state.user.id
        else:
            review.resolved_at = None; review.resolved_by_user_id = None
    db.commit()
    _event(attachment.project_id, "asset.review.updated", request.state.user.id,
           attachment_id=attachment.id, review_id=review.id, status=review.status)
    return _review_payload(db, review, request.state.user.id)


@router.delete("/asset-reviews/{review_id}", status_code=204)
def delete_asset_review(review_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    review = db.get(AssetReview, review_id)
    if review is None:
        raise HTTPException(404, "Замечание не найдено.")
    attachment, _, member = _attachment_access(db, review.attachment_id, request.state.user.id)
    _require_visible_review(review, member)
    if review.author_user_id != request.state.user.id and not has_role(member, "admin"):
        raise HTTPException(403, "Удалить замечание может автор или администратор.")
    db.delete(review); db.commit()
    _event(attachment.project_id, "asset.review.deleted", request.state.user.id,
           attachment_id=attachment.id, review_id=review_id)


@router.put("/content-attachments/{attachment_id}/approval")
def decide_asset(
    attachment_id: str, payload: ApprovalCreate, request: Request, db: Session = Depends(get_db),
) -> dict[str, object]:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    _require_decider(member)
    approval = db.scalar(select(AssetApproval).where(
        AssetApproval.attachment_id == attachment.id, AssetApproval.user_id == request.state.user.id,
    ))
    if approval is None:
        approval = AssetApproval(attachment_id=attachment.id, user_id=request.state.user.id,
                                 decision=payload.decision)
        db.add(approval)
    approval.decision = payload.decision; approval.comment = (payload.comment or "").strip() or None
    approval.decided_at = _now(); db.commit()
    _event(attachment.project_id, "asset.approval.updated", request.state.user.id,
           attachment_id=attachment.id, decision=approval.decision)
    return {**_approval_payload(db, approval, request.state.user.id),
            **_review_summary(db, attachment, member, request.state.user.id)}


@router.delete("/content-attachments/{attachment_id}/approval", status_code=204)
def clear_asset_decision(attachment_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    _require_decider(member)
    db.execute(delete(AssetApproval).where(
        AssetApproval.attachment_id == attachment.id, AssetApproval.user_id == request.state.user.id,
    )); db.commit()
    _event(attachment.project_id, "asset.approval.cleared", request.state.user.id,
           attachment_id=attachment.id)
