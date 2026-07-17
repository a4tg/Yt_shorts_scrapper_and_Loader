from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from billing_service import require_plan_capacity
from content_routes import CONTENT_DIR
from database import get_db
from saas_models import (
    ApprovalStage,
    ApprovalWorkflow,
    AssetReview,
    ContentAttachment,
    ContentItem,
    ContentRevision,
    Conversation,
    ConversationParticipant,
    FeedbackTicket,
    Message,
    ProductEvent,
    Project,
    User,
    Workspace,
    WorkspaceMember,
)
from workspace_service import create_project, has_role, membership_for, project_payload


router = APIRouter(prefix="/api", tags=["beta"])
ONBOARDING_PAGES = {
    "dashboard", "content", "documents", "library", "video", "approvals",
    "messages", "attention", "graph", "ai", "billing", "support",
}
PRODUCT_EVENTS = {
    "page_view",
    "onboarding_step_opened",
    "onboarding_completed",
    "onboarding_dismissed",
    "demo_project_created",
    "support_ticket_created",
}


class OnboardingUpdate(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    dismissed: bool


class DemoProjectCreate(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)


class FeedbackCreate(BaseModel):
    workspace_id: str | None = Field(default=None, max_length=36)
    project_id: str | None = Field(default=None, max_length=36)
    category: Literal["bug", "idea", "question", "billing"]
    page: str | None = Field(default=None, max_length=40)
    message: str = Field(min_length=10, max_length=4000)


class ProductEventCreate(BaseModel):
    event_name: Literal[
        "page_view",
        "onboarding_step_opened",
        "onboarding_completed",
    ]
    workspace_id: str | None = Field(default=None, max_length=36)
    project_id: str | None = Field(default=None, max_length=36)
    page: str | None = Field(default=None, max_length=40)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _workspace_access(
    db: Session, workspace_id: str, user_id: str
) -> tuple[Workspace, WorkspaceMember]:
    workspace = db.get(Workspace, workspace_id)
    member = membership_for(db, workspace_id, user_id)
    if workspace is None or workspace.status != "active" or member is None:
        raise HTTPException(404, "Рабочее пространство не найдено.")
    return workspace, member


def _project_in_workspace(
    db: Session,
    project_id: str | None,
    workspace_id: str,
) -> Project | None:
    if not project_id:
        return None
    project = db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
    )
    if project is None:
        raise HTTPException(404, "Проект не найден.")
    return project


def _safe_page(page: str | None) -> str | None:
    if not page:
        return None
    normalized = page.strip().lower()
    if normalized not in ONBOARDING_PAGES:
        raise HTTPException(400, "Неизвестная страница приложения.")
    return normalized


def _record_event(
    db: Session,
    user_id: str,
    event_name: str,
    *,
    workspace_id: str | None = None,
    project_id: str | None = None,
    page: str | None = None,
    properties: dict[str, int | bool | str] | None = None,
) -> None:
    if event_name not in PRODUCT_EVENTS:
        raise ValueError(event_name)
    db.add(
        ProductEvent(
            user_id=user_id,
            workspace_id=workspace_id,
            project_id=project_id,
            event_name=event_name,
            page=page,
            properties=properties or None,
        )
    )


def _onboarding_dismissed(workspace: Workspace, user_id: str) -> bool:
    settings = dict(workspace.settings or {})
    dismissed = list(settings.get("onboarding_dismissed_user_ids") or [])
    return user_id in dismissed


@router.get("/onboarding")
def onboarding(
    workspace_id: str,
    request: Request,
    project_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    workspace, _ = _workspace_access(db, workspace_id, request.state.user.id)
    project = _project_in_workspace(db, project_id, workspace.id)
    projects = int(db.scalar(
        select(func.count(Project.id)).where(
            Project.workspace_id == workspace.id,
            Project.status == "active",
        )
    ) or 0)
    members = int(db.scalar(
        select(func.count(WorkspaceMember.id)).where(
            WorkspaceMember.workspace_id == workspace.id
        )
    ) or 0)
    content = 0
    files = 0
    if project is not None:
        content = int(db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.project_id == project.id,
                ContentItem.status == "active",
            )
        ) or 0)
        files = int(db.scalar(
            select(func.count(ContentAttachment.id)).where(
                ContentAttachment.project_id == project.id,
                ContentAttachment.is_current.is_(True),
            )
        ) or 0)
    settings = dict(workspace.settings or {})
    demo_project_id = settings.get("demo_project_id")
    demo_exists = bool(
        demo_project_id
        and db.scalar(select(Project.id).where(Project.id == demo_project_id))
    )
    steps = [
        {
            "key": "project",
            "done": projects > 0,
            "page": "dashboard",
            "title": "Создайте проект",
            "detail": "Разделите работу по брендам или направлениям.",
        },
        {
            "key": "content",
            "done": content > 0,
            "page": "content",
            "title": "Добавьте материал",
            "detail": "Запланируйте первый пост, ролик или баннер.",
        },
        {
            "key": "library",
            "done": files > 0,
            "page": "library",
            "title": "Соберите медиатеку",
            "detail": "Прикрепите исходник к карточке контента.",
        },
        {
            "key": "team",
            "done": members > 1,
            "page": "dashboard",
            "title": "Пригласите команду",
            "detail": "Назначьте редактора, клиента или наблюдателя.",
        },
    ]
    return {
        "dismissed": _onboarding_dismissed(workspace, request.state.user.id),
        "completed": all(bool(step["done"]) for step in steps),
        "steps": steps,
        "demo_project_id": demo_project_id if demo_exists else None,
    }


@router.patch("/onboarding")
def update_onboarding(
    payload: OnboardingUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    workspace, _ = _workspace_access(db, payload.workspace_id, request.state.user.id)
    settings = dict(workspace.settings or {})
    dismissed = set(settings.get("onboarding_dismissed_user_ids") or [])
    if payload.dismissed:
        dismissed.add(request.state.user.id)
    else:
        dismissed.discard(request.state.user.id)
    settings["onboarding_dismissed_user_ids"] = sorted(dismissed)
    workspace.settings = settings
    _record_event(
        db,
        request.state.user.id,
        "onboarding_dismissed",
        workspace_id=workspace.id,
        properties={"dismissed": payload.dismissed},
    )
    db.commit()
    return {"dismissed": payload.dismissed}


@router.post("/onboarding/demo", status_code=201)
def create_demo_project(
    payload: DemoProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    workspace, member = _workspace_access(db, payload.workspace_id, request.state.user.id)
    if not has_role(member, "editor"):
        raise HTTPException(403, "Недостаточно прав для создания демо-проекта.")
    settings = dict(workspace.settings or {})
    existing_id = str(settings.get("demo_project_id") or "")
    existing = db.scalar(
        select(Project).where(
            Project.id == existing_id,
            Project.workspace_id == workspace.id,
        )
    ) if existing_id else None
    if existing is not None:
        result = project_payload(existing)
        result["created"] = False
        return result

    active_projects = int(db.scalar(
        select(func.count(Project.id)).where(
            Project.workspace_id == workspace.id,
            Project.status == "active",
        )
    ) or 0)
    require_plan_capacity(
        db,
        request.state.user.id,
        "projects",
        active_projects,
    )
    user = db.get(User, request.state.user.id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден.")
    project = create_project(
        db,
        workspace,
        user,
        "Демо · Запуск продукта",
        "Пример полного контент-процесса: план, материалы, обсуждение и согласование.",
        "#7c6cff",
    )
    db.flush()
    workflow = db.scalar(
        select(ApprovalWorkflow).where(ApprovalWorkflow.project_id == project.id)
    )
    stages = {
        stage.stage_key: stage
        for stage in db.scalars(
            select(ApprovalStage).where(ApprovalStage.workflow_id == workflow.id)
        ).all()
    }
    now = _now()
    items = [
        ContentItem(
            project_id=project.id,
            title="История создания продукта",
            item_type="post",
            body="Черновик истории для Telegram: проблема, путь команды и результат.",
            stage_id=stages["draft"].id,
            channel="Telegram",
            tags=["история", "продукт"],
            priority="normal",
            planned_at=now + timedelta(days=1),
            created_by_user_id=user.id,
        ),
        ContentItem(
            project_id=project.id,
            title="Главный промо-ролик",
            item_type="video",
            body="Вертикальный ролик о едином рабочем процессе команды.",
            stage_id=stages["review"].id,
            channel="VK",
            tags=["видео", "запуск"],
            priority="high",
            planned_at=now + timedelta(days=2),
            created_by_user_id=user.id,
        ),
        ContentItem(
            project_id=project.id,
            title="Серия карточек о команде",
            item_type="banner",
            body="Четыре карточки: роли, подход, скорость и прозрачность.",
            stage_id=stages["idea"].id,
            channel="Telegram",
            tags=["команда", "баннер"],
            priority="normal",
            planned_at=now + timedelta(days=3),
            created_by_user_id=user.id,
        ),
        ContentItem(
            project_id=project.id,
            title="Анонс новой функции",
            item_type="post",
            body="Готовый анонс с выгодой для контент-команд.",
            stage_id=stages["published"].id,
            channel="Telegram",
            tags=["релиз"],
            priority="normal",
            planned_at=now - timedelta(days=1),
            created_by_user_id=user.id,
        ),
    ]
    db.add_all(items)
    db.flush()
    db.add_all([
        ContentRevision(
            content_item_id=item.id,
            version_number=1,
            title=item.title,
            body=item.body,
            changed_by_user_id=user.id,
        )
        for item in items
    ])

    demo_directory = CONTENT_DIR / project.id / "files"
    demo_directory.mkdir(parents=True, exist_ok=True)
    demo_path = demo_directory / f"{uuid.uuid4().hex}_demo-brief.md"
    demo_text = (
        "# Демо-бриф запуска\n\n"
        "Цель: показать единый процесс от идеи до публикации.\n\n"
        "Каналы: Telegram и VK. Срок: одна неделя.\n"
    )
    try:
        demo_path.write_text(demo_text, encoding="utf-8")
        attachment = ContentAttachment(
            project_id=project.id,
            content_item_id=items[1].id,
            uploaded_by_user_id=user.id,
            original_name="Демо-бриф запуска.md",
            storage_path=str(demo_path.resolve()),
            mime_type="text/markdown",
            source_type="demo",
            size_bytes=demo_path.stat().st_size,
            asset_key=str(uuid.uuid4()),
            version_number=1,
            version_label="Демо-версия",
            is_current=True,
        )
        db.add(attachment)
        db.flush()
        db.add(
            AssetReview(
                attachment_id=attachment.id,
                author_user_id=user.id,
                body="Проверьте формулировку цели и подтвердите каналы публикации.",
                annotation_type="general",
                status="open",
                visibility="team",
            )
        )
        conversation = Conversation(
            project_id=project.id,
            kind="group",
            conversation_key="project-wide",
            name="Общий чат",
            is_project_wide=True,
            created_by_user_id=user.id,
        )
        db.add(conversation)
        db.flush()
        db.add(
            ConversationParticipant(
                conversation_id=conversation.id,
                user_id=user.id,
            )
        )
        db.add(
            Message(
                conversation_id=conversation.id,
                author_user_id=user.id,
                body=(
                    "Это демо-проект. Откройте контент-план, бриф и согласование, "
                    "чтобы пройти полный рабочий сценарий."
                ),
            )
        )
        settings["demo_project_id"] = project.id
        workspace.settings = settings
        _record_event(
            db,
            user.id,
            "demo_project_created",
            workspace_id=workspace.id,
            project_id=project.id,
        )
        db.commit()
    except Exception:
        db.rollback()
        demo_path.unlink(missing_ok=True)
        try:
            demo_directory.rmdir()
        except OSError:
            pass
        raise
    result = project_payload(project)
    result["created"] = True
    return result


@router.get("/feedback")
def list_feedback(
    request: Request,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    if not 1 <= limit <= 100:
        raise HTTPException(400, "limit должен быть от 1 до 100.")
    tickets = db.scalars(
        select(FeedbackTicket)
        .where(FeedbackTicket.user_id == request.state.user.id)
        .order_by(FeedbackTicket.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": ticket.id,
            "category": ticket.category,
            "page": ticket.page,
            "message": ticket.message,
            "status": ticket.status,
            "resolution_note": ticket.resolution_note,
            "created_at": ticket.created_at.isoformat(),
            "updated_at": ticket.updated_at.isoformat(),
        }
        for ticket in tickets
    ]


@router.post("/feedback", status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    workspace_id = payload.workspace_id
    project_id = payload.project_id
    if workspace_id:
        workspace, _ = _workspace_access(db, workspace_id, request.state.user.id)
        _project_in_workspace(db, project_id, workspace.id)
    elif project_id:
        raise HTTPException(400, "Для проекта нужно указать рабочее пространство.")
    recent = int(db.scalar(
        select(func.count(FeedbackTicket.id)).where(
            FeedbackTicket.user_id == request.state.user.id,
            FeedbackTicket.created_at >= _now() - timedelta(hours=1),
        )
    ) or 0)
    if recent >= 5:
        raise HTTPException(429, "Можно отправить не более пяти обращений в час.")
    message = payload.message.strip()
    if len(message) < 10:
        raise HTTPException(400, "Опишите вопрос хотя бы в десяти символах.")
    ticket = FeedbackTicket(
        user_id=request.state.user.id,
        workspace_id=workspace_id,
        project_id=project_id,
        category=payload.category,
        page=_safe_page(payload.page),
        message=message,
        status="open",
    )
    db.add(ticket)
    _record_event(
        db,
        request.state.user.id,
        "support_ticket_created",
        workspace_id=workspace_id,
        project_id=project_id,
        page=ticket.page,
        properties={"category": ticket.category},
    )
    db.commit()
    return {
        "id": ticket.id,
        "status": ticket.status,
        "created_at": ticket.created_at.isoformat(),
    }


@router.post("/product-events", status_code=202)
def create_product_event(
    payload: ProductEventCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    workspace_id = payload.workspace_id
    project_id = payload.project_id
    if workspace_id:
        workspace, _ = _workspace_access(db, workspace_id, request.state.user.id)
        _project_in_workspace(db, project_id, workspace.id)
    elif project_id:
        raise HTTPException(400, "Для проекта нужно указать рабочее пространство.")
    recent = int(db.scalar(
        select(func.count(ProductEvent.id)).where(
            ProductEvent.user_id == request.state.user.id,
            ProductEvent.created_at >= _now() - timedelta(days=1),
        )
    ) or 0)
    if recent >= 500:
        raise HTTPException(429, "Дневной лимит продуктовых событий исчерпан.")
    if payload.event_name == "onboarding_completed":
        existing = db.scalar(
            select(ProductEvent.id).where(
                ProductEvent.user_id == request.state.user.id,
                ProductEvent.workspace_id == workspace_id,
                ProductEvent.event_name == "onboarding_completed",
            )
        )
        if existing:
            return {"status": "accepted"}
    _record_event(
        db,
        request.state.user.id,
        payload.event_name,
        workspace_id=workspace_id,
        project_id=project_id,
        page=_safe_page(payload.page),
    )
    db.commit()
    return {"status": "accepted"}
