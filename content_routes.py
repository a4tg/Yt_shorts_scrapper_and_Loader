import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database import get_db
from billing_service import require_entitlement
from file_validation import FileValidationError, validate_file
from asset_preview import PreviewError, build_preview_data, preview_capabilities
from saas_models import (
    ApprovalStage,
    ApprovalWorkflow,
    ContentAttachment,
    ContentItem,
    ContentRevision,
    Job,
    JobFile,
    ProjectFolder,
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


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = Field(default=None, max_length=36)


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: str | None = Field(default=None, max_length=36)


class ProjectFileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    folder_id: str | None = Field(default=None, max_length=36)


class SaveAIResult(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    folder_id: str | None = Field(default=None, max_length=36)


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


def _safe_display_name(value: str, *, max_length: int = 255) -> str:
    name = "".join(character for character in value.strip() if ord(character) >= 32)[:max_length]
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise HTTPException(400, "Некорректное имя файла или папки.")
    return name


def _folder_for_project(db: Session, project_id: str, folder_id: str | None) -> ProjectFolder | None:
    if not folder_id:
        return None
    folder = db.get(ProjectFolder, folder_id)
    if folder is None or folder.project_id != project_id:
        raise HTTPException(400, "Папка не принадлежит выбранному проекту.")
    return folder


def _ensure_unique_folder_name(
    db: Session, project_id: str, parent_id: str | None, name: str, *, exclude_id: str | None = None
) -> None:
    statement = select(ProjectFolder.id).where(
        ProjectFolder.project_id == project_id,
        ProjectFolder.parent_id.is_(None) if parent_id is None else ProjectFolder.parent_id == parent_id,
        func.lower(ProjectFolder.name) == name.casefold(),
    )
    if exclude_id:
        statement = statement.where(ProjectFolder.id != exclude_id)
    if db.scalar(statement):
        raise HTTPException(409, "Папка с таким названием уже существует здесь.")


def _ensure_unique_file_name(
    db: Session, project_id: str, folder_id: str | None, name: str, *, exclude_id: str | None = None
) -> None:
    statement = select(ContentAttachment.id).where(
        ContentAttachment.project_id == project_id,
        ContentAttachment.is_current.is_(True),
        ContentAttachment.folder_id.is_(None) if folder_id is None else ContentAttachment.folder_id == folder_id,
        func.lower(ContentAttachment.original_name) == name.casefold(),
    )
    if exclude_id:
        statement = statement.where(ContentAttachment.id != exclude_id)
    if db.scalar(statement):
        raise HTTPException(409, "Файл с таким названием уже существует в этой папке.")


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
        "project_id": attachment.project_id,
        "folder_id": attachment.folder_id,
        "content_item_id": attachment.content_item_id,
        "name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "source_type": attachment.source_type,
        "size_bytes": attachment.size_bytes,
        "created_at": attachment.created_at.isoformat(),
        "download_url": f"/api/content-attachments/{attachment.id}/download",
        "preview_url": f"/api/content-attachments/{attachment.id}/preview",
        "preview_data_url": f"/api/content-attachments/{attachment.id}/preview-data",
        "preview": preview_capabilities(attachment.original_name),
        "asset_key": attachment.asset_key,
        "version_number": attachment.version_number,
        "version_label": attachment.version_label,
        "version_notes": attachment.version_notes,
        "supersedes_attachment_id": attachment.supersedes_attachment_id,
        "is_current": attachment.is_current,
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
        .where(ContentAttachment.content_item_id == item.id, ContentAttachment.is_current.is_(True))
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


async def _store_upload(
    file: UploadFile,
    *,
    project_id: str,
    user_id: str,
    db: Session,
    content_item_id: str | None = None,
    folder_id: str | None = None,
    ensure_unique_name: bool = True,
    asset_key: str | None = None,
    version_number: int = 1,
    version_label: str | None = None,
    version_notes: str | None = None,
    supersedes_attachment_id: str | None = None,
    source_type: str = "upload",
) -> ContentAttachment:
    entitlement = require_entitlement(db, user_id)
    storage_limit = entitlement.limits.get("storage_mb", 0) * 1024 * 1024
    used_storage = db.scalar(
        select(func.coalesce(func.sum(ContentAttachment.size_bytes), 0)).where(
            ContentAttachment.uploaded_by_user_id == user_id
        )
    ) or 0
    original_name = _safe_display_name(Path(file.filename or "file").name)
    if ensure_unique_name:
        _ensure_unique_file_name(db, project_id, folder_id, original_name)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._") or "file"
    directory = CONTENT_DIR / project_id / "files"
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    target = directory / f"{token}_{safe_name}"
    temporary = target.with_suffix(target.suffix + ".part")
    size = 0
    validated = None
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
        if size == 0:
            raise HTTPException(400, "Пустые файлы не поддерживаются.")
        try:
            validated = validate_file(temporary, original_name)
        except FileValidationError as exc:
            raise HTTPException(415, str(exc)) from exc
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    attachment = ContentAttachment(
        project_id=project_id,
        folder_id=folder_id,
        content_item_id=content_item_id,
        uploaded_by_user_id=user_id,
        original_name=original_name,
        storage_path=str(target),
        mime_type=validated.mime_type,
        source_type=source_type,
        size_bytes=size,
        asset_key=asset_key or str(uuid.uuid4()),
        version_number=version_number,
        version_label=version_label,
        version_notes=version_notes,
        supersedes_attachment_id=supersedes_attachment_id,
        is_current=True,
    )
    db.add(attachment)
    db.commit()
    return attachment


@router.post("/content/{item_id}/attachments", status_code=201)
async def upload_attachment(
    item_id: str,
    request: Request,
    file: UploadFile,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    item, member = _item_access(db, item_id, request.state.user.id)
    _require_editor(member)
    attachment = await _store_upload(
        file, project_id=item.project_id, user_id=request.state.user.id,
        db=db, content_item_id=item.id,
    )
    return _attachment_payload(attachment)


@router.post("/projects/{project_id}/files", status_code=201)
async def upload_project_file(
    project_id: str,
    request: Request,
    file: UploadFile,
    folder_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _, member = _project_access(db, project_id, request.state.user.id)
    _require_editor(member)
    folder = _folder_for_project(db, project_id, folder_id)
    attachment = await _store_upload(
        file, project_id=project_id, user_id=request.state.user.id,
        db=db, folder_id=folder.id if folder else None,
    )
    return _attachment_payload(attachment)


def _attachment_access(db: Session, attachment_id: str, user_id: str):
    attachment = db.get(ContentAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Файл не найден")
    access = project_membership(db, attachment.project_id, user_id)
    if access is None:
        raise HTTPException(404, "Файл не найден")
    return attachment, access[0], access[1]


@router.get("/content-attachments/{attachment_id}")
def attachment_detail(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    attachment, _, _ = _attachment_access(db, attachment_id, request.state.user.id)
    return _attachment_payload(attachment)


@router.get("/content-attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
) -> FileResponse:
    attachment, _, _ = _attachment_access(db, attachment_id, request.state.user.id)
    path = Path(attachment.storage_path).resolve()
    if not path.is_file() or not path.is_relative_to(CONTENT_DIR.resolve()):
        raise HTTPException(404, "Файл отсутствует в хранилище")
    return FileResponse(path, filename=attachment.original_name, media_type=attachment.mime_type)


def _attachment_path(attachment: ContentAttachment) -> Path:
    path = Path(attachment.storage_path).resolve()
    if not path.is_file() or not path.is_relative_to(CONTENT_DIR.resolve()):
        raise HTTPException(404, "Файл отсутствует в хранилище")
    return path


@router.get("/content-attachments/{attachment_id}/preview")
def preview_attachment(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
) -> FileResponse:
    attachment, _, _ = _attachment_access(db, attachment_id, request.state.user.id)
    capability = preview_capabilities(attachment.original_name)
    if not capability["inline_url"]:
        raise HTTPException(415, "Для этого формата используется структурированный просмотр.")
    path = _attachment_path(attachment)
    return FileResponse(
        path, media_type=attachment.mime_type,
        headers={"Content-Disposition": "inline", "Accept-Ranges": "bytes"},
    )


@router.get("/content-attachments/{attachment_id}/preview-data")
def preview_attachment_data(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    attachment, _, _ = _attachment_access(db, attachment_id, request.state.user.id)
    path = _attachment_path(attachment)
    try:
        payload = build_preview_data(path, attachment.original_name)
    except PreviewError as exc:
        raise HTTPException(415, str(exc)) from exc
    return {"id": attachment.id, "name": attachment.original_name, **payload}


@router.delete("/content-attachments/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: str, request: Request, db: Session = Depends(get_db)
) -> None:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    _require_editor(member)
    path = Path(attachment.storage_path)
    if attachment.is_current:
        previous = db.scalar(
            select(ContentAttachment)
            .where(
                ContentAttachment.asset_key == attachment.asset_key,
                ContentAttachment.id != attachment.id,
            )
            .order_by(ContentAttachment.version_number.desc())
        )
        if previous is not None:
            previous.is_current = True
    db.delete(attachment)
    db.commit()
    path.unlink(missing_ok=True)


@router.patch("/project-files/{attachment_id}")
def update_project_file(
    attachment_id: str,
    payload: ProjectFileUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    attachment, _, member = _attachment_access(db, attachment_id, request.state.user.id)
    _require_editor(member)
    values = payload.model_dump(exclude_unset=True)
    target_folder_id = attachment.folder_id
    if "folder_id" in values:
        folder = _folder_for_project(db, attachment.project_id, values["folder_id"])
        target_folder_id = folder.id if folder else None
    target_name = attachment.original_name
    if "name" in values:
        target_name = _safe_display_name(values["name"])
        if Path(target_name).suffix.lower() != Path(attachment.original_name).suffix.lower():
            raise HTTPException(400, "При переименовании нельзя менять формат файла.")
    _ensure_unique_file_name(
        db, attachment.project_id, target_folder_id, target_name, exclude_id=attachment.id
    )
    attachment.original_name = target_name
    attachment.folder_id = target_folder_id
    db.commit()
    return _attachment_payload(attachment)


@router.get("/projects/{project_id}/folders")
def list_project_folders(
    project_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    _project_access(db, project_id, request.state.user.id)
    folders = db.scalars(
        select(ProjectFolder).where(ProjectFolder.project_id == project_id).order_by(ProjectFolder.name)
    ).all()
    return [{
        "id": folder.id, "project_id": folder.project_id, "parent_id": folder.parent_id,
        "name": folder.name, "created_at": folder.created_at.isoformat(),
    } for folder in folders]


@router.post("/projects/{project_id}/folders", status_code=201)
def create_project_folder(
    project_id: str,
    payload: FolderCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _, member = _project_access(db, project_id, request.state.user.id)
    _require_editor(member)
    parent = _folder_for_project(db, project_id, payload.parent_id)
    name = _safe_display_name(payload.name, max_length=120)
    _ensure_unique_folder_name(db, project_id, parent.id if parent else None, name)
    folder = ProjectFolder(
        project_id=project_id, parent_id=parent.id if parent else None,
        name=name, created_by_user_id=request.state.user.id,
    )
    db.add(folder)
    db.commit()
    return {"id": folder.id, "project_id": project_id, "parent_id": folder.parent_id, "name": folder.name}


@router.patch("/project-folders/{folder_id}")
def update_project_folder(
    folder_id: str,
    payload: FolderUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    folder = db.get(ProjectFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "Папка не найдена")
    _, member = _project_access(db, folder.project_id, request.state.user.id)
    _require_editor(member)
    values = payload.model_dump(exclude_unset=True)
    parent_id = folder.parent_id
    if "parent_id" in values:
        parent = _folder_for_project(db, folder.project_id, values["parent_id"])
        parent_id = parent.id if parent else None
        cursor = parent
        while cursor is not None:
            if cursor.id == folder.id:
                raise HTTPException(400, "Нельзя переместить папку внутрь самой себя.")
            cursor = db.get(ProjectFolder, cursor.parent_id) if cursor.parent_id else None
    name = _safe_display_name(values.get("name", folder.name), max_length=120)
    _ensure_unique_folder_name(db, folder.project_id, parent_id, name, exclude_id=folder.id)
    folder.name = name
    folder.parent_id = parent_id
    db.commit()
    return {"id": folder.id, "project_id": folder.project_id, "parent_id": folder.parent_id, "name": folder.name}


@router.delete("/project-folders/{folder_id}", status_code=204)
def delete_project_folder(
    folder_id: str, request: Request, db: Session = Depends(get_db)
) -> None:
    folder = db.get(ProjectFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "Папка не найдена")
    _, member = _project_access(db, folder.project_id, request.state.user.id)
    _require_editor(member)
    has_children = db.scalar(select(ProjectFolder.id).where(ProjectFolder.parent_id == folder.id))
    has_files = db.scalar(select(ContentAttachment.id).where(ContentAttachment.folder_id == folder.id))
    if has_children or has_files:
        raise HTTPException(409, "Сначала переместите или удалите содержимое папки.")
    db.delete(folder)
    db.commit()


@router.get("/projects/{project_id}/library")
def project_library(
    project_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    _project_access(db, project_id, request.state.user.id)
    rows = db.execute(
        select(ContentAttachment, ContentItem)
        .outerjoin(ContentItem, ContentItem.id == ContentAttachment.content_item_id)
        .where(
            ContentAttachment.project_id == project_id,
            ContentAttachment.is_current.is_(True),
            or_(ContentItem.id.is_(None), ContentItem.status == "active"),
        )
        .order_by(ContentAttachment.created_at.desc())
    ).all()
    result = []
    for attachment, item in rows:
        payload = _attachment_payload(attachment)
        payload.update({
            "content_title": item.title if item else None,
            "content_type": item.item_type if item else None,
        })
        result.append(payload)
    return result


@router.post("/jobs/{job_id}/save-to-project", status_code=201)
def save_ai_result_to_project(
    job_id: str,
    payload: SaveAIResult,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    job = db.get(Job, job_id)
    if job is None or job.user_id != request.state.user.id or job.status != "done":
        raise HTTPException(404, "Готовый AI-результат не найден.")
    if job.kind not in {"ai_text", "ai_image", "ai_clips"} or not job.project_id:
        raise HTTPException(400, "Этот результат нельзя сохранить в проект.")
    _, member = _project_access(db, job.project_id, request.state.user.id)
    _require_editor(member)
    folder = _folder_for_project(db, job.project_id, payload.folder_id)
    name = _safe_display_name(payload.name)
    expected_extension = {"ai_text": {".md", ".txt"}, "ai_image": {".png"}, "ai_clips": {".zip"}}[job.kind]
    if Path(name).suffix.lower() not in expected_extension:
        allowed = " или ".join(sorted(expected_extension))
        raise HTTPException(400, f"Для этого AI-результата требуется расширение {allowed}.")
    target_folder_id = folder.id if folder else None
    _ensure_unique_file_name(db, job.project_id, target_folder_id, name)
    directory = CONTENT_DIR / job.project_id / "files"
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "ai-result"
    target = directory / f"{uuid.uuid4().hex}_{safe_name}"
    if job.kind == "ai_text":
        text = str((job.result_payload or {}).get("text") or "")
        target.write_text(text, encoding="utf-8")
        mime_type = "text/markdown" if target.suffix.lower() == ".md" else "text/plain"
    else:
        job_file = db.scalar(
            select(JobFile).where(JobFile.job_id == job.id, JobFile.deleted_at.is_(None)).order_by(JobFile.created_at)
        )
        source = Path(job_file.storage_path).resolve() if job_file else None
        if source is None or not source.is_file():
            raise HTTPException(404, "Файл AI-результата уже недоступен.")
        shutil.copy2(source, target)
        mime_type = job_file.mime_type or ("image/png" if job.kind == "ai_image" else "application/zip")
    entitlement = require_entitlement(db, request.state.user.id)
    storage_limit = entitlement.limits.get("storage_mb", 0) * 1024 * 1024
    used_storage = db.scalar(
        select(func.coalesce(func.sum(ContentAttachment.size_bytes), 0)).where(
            ContentAttachment.uploaded_by_user_id == request.state.user.id
        )
    ) or 0
    if storage_limit and int(used_storage) + target.stat().st_size > storage_limit:
        target.unlink(missing_ok=True)
        raise HTTPException(413, "AI-результат не помещается в лимит хранилища текущего тарифа.")
    attachment = ContentAttachment(
        project_id=job.project_id, folder_id=target_folder_id, content_item_id=None,
        uploaded_by_user_id=request.state.user.id, original_name=name,
        storage_path=str(target), mime_type=mime_type, source_type="ai",
        size_bytes=target.stat().st_size,
    )
    db.add(attachment)
    db.commit()
    return _attachment_payload(attachment)
