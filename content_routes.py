import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database import get_db
from billing_service import require_entitlement
from saas_models import (
    ApprovalStage,
    ApprovalWorkflow,
    ContentAttachment,
    ContentItem,
    ContentRevision,
    User,
    WorkspaceMember,
)
from workspace_service import has_role, project_membership
from server_core import normalize_source_video_url


router = APIRouter(prefix="/api", tags=["content"])
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("YT_LOADER_DATA_DIR", BASE_DIR / "server_data")).resolve()
CONTENT_DIR = Path(
    os.getenv("YT_LOADER_CONTENT_DIR", DATA_DIR / "content")
).resolve()
MAX_CONTENT_FILE_BYTES = max(1, int(os.getenv("YT_LOADER_MAX_CONTENT_FILE_MB", "250"))) * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
ContentType = Literal["post", "video", "banner", "document", "campaign", "note"]
Priority = Literal["low", "normal", "high", "urgent"]


class ContentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    item_type: ContentType = "post"
    body: str | None = Field(default=None, max_length=2_000_000)
    stage_id: str | None = Field(default=None, max_length=36)
    channel: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=30)
    priority: Priority = "normal"
    planned_at: datetime | None = None
    due_at: datetime | None = None
    assignee_user_id: str | None = Field(default=None, max_length=36)
    source_platform: Literal["youtube", "vk", "rutube"] | None = None
    source_id: str | None = Field(default=None, max_length=160)
    source_url: str | None = Field(default=None, max_length=1000)


class ContentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    item_type: ContentType | None = None
    body: str | None = Field(default=None, max_length=2_000_000)
    stage_id: str | None = Field(default=None, max_length=36)
    channel: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=30)
    priority: Priority | None = None
    planned_at: datetime | None = None
    due_at: datetime | None = None
    assignee_user_id: str | None = Field(default=None, max_length=36)
    status: Literal["active", "archived"] | None = None


def _project_access(db: Session, project_id: str, user_id: str):
    access = project_membership(db, project_id, user_id)
    if access is None:
        raise HTTPException(404, "Проект не найден")
    return access


def _item_access(db: Session, item_id: str, user_id: str):
    item = db.get(ContentItem, item_id)
    if item is None:
        raise HTTPException(404, "Материал не найден")
    access = project_membership(db, item.project_id, user_id)
    if access is None:
        raise HTTPException(404, "Материал не найден")
    return item, access[1]


def _require_editor(member: WorkspaceMember) -> None:
    if not has_role(member, "editor"):
        raise HTTPException(403, "Для изменения контента нужна роль редактора")


def _normalize_tags(tags: list[str] | None) -> list[str]:
    result: list[str] = []
    for raw in tags or []:
        tag = raw.strip().lstrip("#")[:60]
        if tag and tag.casefold() not in {value.casefold() for value in result}:
            result.append(tag)
    return result[:30]


def _stage_for_project(db: Session, project_id: str, stage_id: str | None) -> ApprovalStage | None:
    if not stage_id:
        workflow = db.scalar(select(ApprovalWorkflow).where(ApprovalWorkflow.project_id == project_id))
        if workflow is None:
            return None
        return db.scalar(
            select(ApprovalStage)
            .where(ApprovalStage.workflow_id == workflow.id)
            .order_by(ApprovalStage.position)
        )
    return db.scalar(
        select(ApprovalStage)
        .join(ApprovalWorkflow, ApprovalWorkflow.id == ApprovalStage.workflow_id)
        .where(ApprovalStage.id == stage_id, ApprovalWorkflow.project_id == project_id)
    )


def _validate_assignee(db: Session, workspace_id: str, user_id: str | None) -> None:
    if not user_id:
        return
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member is None:
        raise HTTPException(400, "Ответственный должен состоять в рабочем пространстве")


def _check_stage_permission(member: WorkspaceMember, stage: ApprovalStage | None) -> None:
    if stage is None or not stage.required_role or member.role in {"owner", "admin"}:
        return
    if member.role != stage.required_role:
        raise HTTPException(403, f"Перевод на этот этап доступен роли: {stage.required_role}")


def _attachment_payload(attachment: ContentAttachment) -> dict[str, object]:
    return {
        "id": attachment.id,
        "content_item_id": attachment.content_item_id,
        "name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "size_bytes": attachment.size_bytes,
        "created_at": attachment.created_at.isoformat(),
        "download_url": f"/api/content-attachments/{attachment.id}/download",
    }


def _item_payload(db: Session, item: ContentItem, *, include_body: bool = True) -> dict[str, object]:
    stage = db.get(ApprovalStage, item.stage_id) if item.stage_id else None
    assignee = db.get(User, item.assignee_user_id) if item.assignee_user_id else None
    attachment_count = db.scalar(
        select(func.count(ContentAttachment.id)).where(ContentAttachment.content_item_id == item.id)
    ) or 0
    payload: dict[str, object] = {
        "id": item.id,
        "project_id": item.project_id,
        "title": item.title,
        "item_type": item.item_type,
        "stage": ({
            "id": stage.id, "key": stage.stage_key, "name": stage.name,
            "color": stage.color, "position": stage.position,
        } if stage else None),
        "channel": item.channel,
        "source_platform": item.source_platform,
        "source_id": item.source_id,
        "source_url": item.source_url,
        "tags": list(item.tags or []),
        "priority": item.priority,
        "status": item.status,
        "planned_at": item.planned_at.isoformat() if item.planned_at else None,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "assignee": ({
            "id": assignee.id,
            "name": assignee.display_name or assignee.email,
            "email": assignee.email,
        } if assignee else None),
        "attachment_count": attachment_count,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if include_body:
        payload["body"] = item.body or ""
    return payload


def _add_revision(db: Session, item: ContentItem, user_id: str) -> None:
    version = db.scalar(
        select(func.max(ContentRevision.version_number)).where(
            ContentRevision.content_item_id == item.id
        )
    ) or 0
    db.add(ContentRevision(
        content_item_id=item.id,
        version_number=version + 1,
        title=item.title,
        body=item.body,
        changed_by_user_id=user_id,
    ))


@router.get("/projects/{project_id}/content")
def list_content(
    project_id: str,
    request: Request,
    q: str | None = None,
    item_type: ContentType | None = None,
    stage_id: str | None = None,
    status: Literal["active", "archived", "all"] = "active",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    _project_access(db, project_id, request.state.user.id)
    statement = select(ContentItem).where(ContentItem.project_id == project_id)
    if status != "all":
        statement = statement.where(ContentItem.status == status)
    if item_type:
        statement = statement.where(ContentItem.item_type == item_type)
    if stage_id:
        statement = statement.where(ContentItem.stage_id == stage_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.where(or_(ContentItem.title.ilike(pattern), ContentItem.body.ilike(pattern)))
    items = db.scalars(
        statement.order_by(ContentItem.planned_at.is_(None), ContentItem.planned_at, ContentItem.updated_at.desc())
    ).all()
    return [_item_payload(db, item, include_body=False) for item in items]


@router.post("/projects/{project_id}/content", status_code=201)
def create_content(
    project_id: str,
    payload: ContentCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project, member = _project_access(db, project_id, request.state.user.id)
    _require_editor(member)
    require_entitlement(db, request.state.user.id)
    stage = _stage_for_project(db, project_id, payload.stage_id)
    if payload.stage_id and stage is None:
        raise HTTPException(400, "Этап не принадлежит проекту")
    _check_stage_permission(member, stage)
    _validate_assignee(db, project.workspace_id, payload.assignee_user_id)
    source_fields = (payload.source_platform, payload.source_id, payload.source_url)
    if any(source_fields) and not all(source_fields):
        raise HTTPException(400, "Для источника нужны platform, id и url")
    normalized_source_url = None
    if all(source_fields):
        try:
            normalized_source_url, detected = normalize_source_video_url(str(payload.source_url))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if detected != payload.source_platform:
            raise HTTPException(400, "Платформа не соответствует ссылке источника")
        duplicate = db.scalar(
            select(ContentItem.id).where(
                ContentItem.project_id == project_id,
                ContentItem.source_platform == payload.source_platform,
                ContentItem.source_id == payload.source_id,
            )
        )
        if duplicate:
            raise HTTPException(409, "Этот ролик уже добавлен в контент-план")
    item = ContentItem(
        project_id=project_id,
        title=payload.title.strip(),
        item_type=payload.item_type,
        body=(payload.body or "").strip() or None,
        stage_id=stage.id if stage else None,
        channel=(payload.channel or "").strip() or None,
        source_platform=payload.source_platform,
        source_id=payload.source_id,
        source_url=normalized_source_url,
        tags=_normalize_tags(payload.tags),
        priority=payload.priority,
        planned_at=payload.planned_at,
        due_at=payload.due_at,
        assignee_user_id=payload.assignee_user_id,
        created_by_user_id=request.state.user.id,
    )
    db.add(item)
    db.flush()
    _add_revision(db, item, request.state.user.id)
    db.commit()
    return _item_payload(db, item)


@router.get("/content/{item_id}")
def get_content(item_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    item, _ = _item_access(db, item_id, request.state.user.id)
    payload = _item_payload(db, item)
    attachments = db.scalars(
        select(ContentAttachment)
        .where(ContentAttachment.content_item_id == item.id)
        .order_by(ContentAttachment.created_at.desc())
    ).all()
    payload["attachments"] = [_attachment_payload(attachment) for attachment in attachments]
    return payload


@router.patch("/content/{item_id}")
def update_content(
    item_id: str,
    payload: ContentUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    item, member = _item_access(db, item_id, request.state.user.id)
    _require_editor(member)
    require_entitlement(db, request.state.user.id)
    project, _ = _project_access(db, item.project_id, request.state.user.id)
    values = payload.model_dump(exclude_unset=True)
    if "stage_id" in values:
        stage = _stage_for_project(db, item.project_id, values["stage_id"])
        if values["stage_id"] and stage is None:
            raise HTTPException(400, "Этап не принадлежит проекту")
        _check_stage_permission(member, stage)
        item.stage_id = stage.id if stage else None
    if "assignee_user_id" in values:
        _validate_assignee(db, project.workspace_id, values["assignee_user_id"])
    for field in (
        "item_type", "priority", "planned_at", "due_at", "assignee_user_id", "status",
    ):
        if field in values:
            setattr(item, field, values[field])
    if "title" in values:
        item.title = values["title"].strip()
    if "body" in values:
        item.body = (values["body"] or "").strip() or None
    if "channel" in values:
        item.channel = (values["channel"] or "").strip() or None
    if "tags" in values:
        item.tags = _normalize_tags(values["tags"])
    _add_revision(db, item, request.state.user.id)
    db.commit()
    return _item_payload(db, item)


@router.delete("/content/{item_id}", status_code=204)
def archive_content(item_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    item, member = _item_access(db, item_id, request.state.user.id)
    _require_editor(member)
    require_entitlement(db, request.state.user.id)
    item.status = "archived"
    db.commit()


@router.get("/content/{item_id}/revisions")
def list_revisions(
    item_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    item, _ = _item_access(db, item_id, request.state.user.id)
    rows = db.execute(
        select(ContentRevision, User)
        .join(User, User.id == ContentRevision.changed_by_user_id)
        .where(ContentRevision.content_item_id == item.id)
        .order_by(ContentRevision.version_number.desc())
    ).all()
    return [{
        "id": revision.id,
        "version": revision.version_number,
        "title": revision.title,
        "body": revision.body or "",
        "author": user.display_name or user.email,
        "created_at": revision.created_at.isoformat(),
    } for revision, user in rows]


@router.post("/content/{item_id}/attachments", status_code=201)
async def upload_attachment(
    item_id: str,
    request: Request,
    file: UploadFile,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    item, member = _item_access(db, item_id, request.state.user.id)
    _require_editor(member)
    entitlement = require_entitlement(db, request.state.user.id)
    storage_limit = entitlement.limits.get("storage_mb", 0) * 1024 * 1024
    used_storage = db.scalar(
        select(func.coalesce(func.sum(ContentAttachment.size_bytes), 0)).where(
            ContentAttachment.uploaded_by_user_id == request.state.user.id
        )
    ) or 0
    original_name = Path(file.filename or "file").name[:255]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._") or "file"
    directory = CONTENT_DIR / item.project_id / item.id
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    target = directory / f"{token}_{safe_name}"
    temporary = target.with_suffix(target.suffix + ".part")
    size = 0
    try:
        with temporary.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_CONTENT_FILE_BYTES:
                    raise HTTPException(
                        413, f"Файл больше {MAX_CONTENT_FILE_BYTES // 1024 // 1024} МБ"
                    )
                if storage_limit and int(used_storage) + size > storage_limit:
                    raise HTTPException(413, "Достигнут лимит хранилища текущего тарифа.")
                output.write(chunk)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    attachment = ContentAttachment(
        content_item_id=item.id,
        uploaded_by_user_id=request.state.user.id,
        original_name=original_name,
        storage_path=str(target),
        mime_type=(file.content_type or "application/octet-stream")[:160],
        size_bytes=size,
    )
    db.add(attachment)
    db.commit()
    return _attachment_payload(attachment)


def _attachment_access(db: Session, attachment_id: str, user_id: str):
    attachment = db.get(ContentAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Файл не найден")
    item, member = _item_access(db, attachment.content_item_id, user_id)
    return attachment, item, member


@router.get("/content-attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
) -> FileResponse:
    attachment, _, _ = _attachment_access(db, attachment_id, request.state.user.id)
    path = Path(attachment.storage_path).resolve()
    if not path.is_file() or not path.is_relative_to(CONTENT_DIR.resolve()):
        raise HTTPException(404, "Файл отсутствует в хранилище")
    return FileResponse(path, filename=attachment.original_name, media_type=attachment.mime_type)


@router.delete("/content-attachments/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
) -> None:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    _require_editor(member)
    path = Path(attachment.storage_path)
    db.delete(attachment)
    db.commit()
    path.unlink(missing_ok=True)


@router.get("/projects/{project_id}/library")
def project_library(
    project_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    _project_access(db, project_id, request.state.user.id)
    rows = db.execute(
        select(ContentAttachment, ContentItem)
        .join(ContentItem, ContentItem.id == ContentAttachment.content_item_id)
        .where(ContentItem.project_id == project_id, ContentItem.status == "active")
        .order_by(ContentAttachment.created_at.desc())
    ).all()
    result = []
    for attachment, item in rows:
        payload = _attachment_payload(attachment)
        payload.update({"content_title": item.title, "content_type": item.item_type})
        result.append(payload)
    return result
