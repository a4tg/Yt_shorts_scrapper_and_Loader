from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ai_service import AIServiceError, ai_enabled, generate_text
from database import get_db
from decision_intelligence import (
    aware_datetime, classify_text, deterministic_briefing, extract_due_at, fingerprint, impact_score,
    insight_title, parse_ai_intelligence, priority_for,
)
from graph_routes import _validate_entity
from realtime_service import project_events
from saas_models import (
    AssetApproval, AssetReview, ContentAttachment, ContentItem, Conversation,
    ConversationParticipant, EntityLink, InsightLink, Message, Project,
    ProjectBriefing, ProjectInsight, User, WorkspaceMember,
)
from workspace_service import has_role, project_membership


router = APIRouter(prefix="/api", tags=["decision-intelligence"])
InsightKind = Literal["decision", "commitment", "action", "risk", "question"]
InsightStatus = Literal["open", "in_progress", "done", "dismissed"]
Priority = Literal["low", "normal", "high", "urgent"]
Visibility = Literal["team", "client"]


class InsightLinkInput(BaseModel):
    entity_type: Literal["project", "content", "asset", "conversation", "review", "user", "diagram"]
    entity_id: str = Field(max_length=36)
    relation_type: Literal["derived_from", "impacts", "depends_on", "resolves"] = "impacts"
    weight: float = Field(default=1, gt=0, le=100)


class InsightCreate(BaseModel):
    kind: InsightKind
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=20_000)
    priority: Priority = "normal"
    visibility: Visibility = "team"
    assignee_user_id: str | None = Field(default=None, max_length=36)
    due_at: datetime | None = None
    links: list[InsightLinkInput] = Field(default_factory=list, max_length=30)


class InsightUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=20_000)
    status: InsightStatus | None = None
    priority: Priority | None = None
    visibility: Visibility | None = None
    assignee_user_id: str | None = Field(default=None, max_length=36)
    due_at: datetime | None = None


class ExtractRequest(BaseModel):
    use_ai: bool = False
    limit: int = Field(default=500, ge=10, le=1000)


class BriefingRequest(BaseModel):
    use_ai: bool = True
    visibility: Visibility = "team"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_confidence(value: object, default: float = .7) -> float:
    try:
        return max(0, min(1, float(value)))
    except (TypeError, ValueError):
        return default


def _briefing_entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        clean: dict[str, object] = {}
        for key in ("id", "title", "detail", "priority", "impact_score"):
            current = item.get(key)
            if isinstance(current, str):
                clean[key] = current[:1000]
            elif isinstance(current, (int, float)):
                clean[key] = current
        if clean:
            result.append(clean)
    return result


def _project_access(db: Session, project_id: str, user_id: str):
    access = project_membership(db, project_id, user_id)
    if access is None:
        raise HTTPException(404, "Проект не найден.")
    return access


def _require_editor(member: WorkspaceMember) -> None:
    if not has_role(member, "editor"):
        raise HTTPException(403, "Для запуска анализа нужна роль редактора.")


def _event(project_id: str, event_type: str, user_id: str, **payload: object) -> None:
    project_events.publish(project_id, {
        "type": event_type, "project_id": project_id, "actor_user_id": user_id, **payload,
    })


def _validate_assignee(db: Session, project: Project, user_id: str | None) -> None:
    if user_id and not db.scalar(select(WorkspaceMember.id).where(
        WorkspaceMember.workspace_id == project.workspace_id, WorkspaceMember.user_id == user_id,
    )):
        raise HTTPException(400, "Ответственный должен состоять в рабочем пространстве.")


def _user_payload(user: User | None) -> dict[str, str] | None:
    if user is None: return None
    return {"id": user.id, "name": user.display_name or user.email, "email": user.email}


def _insight_links(db: Session, insight_id: str) -> list[dict[str, object]]:
    return [{"id": item.id, "entity_type": item.entity_type, "entity_id": item.entity_id,
             "relation_type": item.relation_type, "weight": item.weight}
            for item in db.scalars(select(InsightLink).where(InsightLink.insight_id == insight_id)).all()]


def _insight_payload(db: Session, insight: ProjectInsight, viewer_id: str) -> dict[str, object]:
    assignee = db.get(User, insight.assignee_user_id) if insight.assignee_user_id else None
    creator = db.get(User, insight.created_by_user_id) if insight.created_by_user_id else None
    return {
        "id": insight.id, "project_id": insight.project_id, "kind": insight.kind,
        "title": insight.title, "description": insight.description, "status": insight.status,
        "priority": insight.priority, "visibility": insight.visibility,
        "source_type": insight.source_type, "source_id": insight.source_id,
        "source_excerpt": insight.source_excerpt, "assignee": _user_payload(assignee),
        "due_at": insight.due_at.isoformat() if insight.due_at else None,
        "confidence": insight.confidence, "impact_score": insight.impact_score,
        "extra": insight.extra or {}, "created_by": _user_payload(creator),
        "completed_at": insight.completed_at.isoformat() if insight.completed_at else None,
        "created_at": insight.created_at.isoformat(), "updated_at": insight.updated_at.isoformat(),
        "links": _insight_links(db, insight.id),
        "is_own": insight.created_by_user_id == viewer_id,
    }


def _visible_insights(db: Session, project_id: str, member: WorkspaceMember, *, include_closed: bool = True):
    statement = select(ProjectInsight).where(ProjectInsight.project_id == project_id)
    if member.role == "client": statement = statement.where(ProjectInsight.visibility == "client")
    if not include_closed: statement = statement.where(ProjectInsight.status.in_(["open", "in_progress"]))
    return db.scalars(statement.order_by(ProjectInsight.impact_score.desc(), ProjectInsight.due_at, ProjectInsight.updated_at.desc())).all()


def _link_weight(db: Session, entity_type: str | None, entity_id: str | None) -> float:
    if not entity_type or not entity_id: return 0
    outgoing = db.scalar(select(func.coalesce(func.sum(EntityLink.weight), 0)).where(
        EntityLink.source_type == entity_type, EntityLink.source_id == entity_id,
    )) or 0
    incoming = db.scalar(select(func.coalesce(func.sum(EntityLink.weight), 0)).where(
        EntityLink.target_type == entity_type, EntityLink.target_id == entity_id,
    )) or 0
    return float(outgoing) + float(incoming)


def _upsert_insight(
    db: Session, project: Project, *, kind: str, text: str, source_type: str,
    source_id: str | None, visibility: str, assignee_user_id: str | None = None,
    due_at: datetime | None = None, confidence: float = 1, title: str | None = None,
    priority: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
    created_by_user_id: str | None = None, extra: dict | None = None,
) -> tuple[ProjectInsight, bool]:
    title = (title or insight_title(text, kind)).strip()[:240]
    key = fingerprint(project.id, kind, source_type, source_id, title)
    existing = db.scalar(select(ProjectInsight).where(
        ProjectInsight.project_id == project.id, ProjectInsight.fingerprint == key,
    ))
    priority = priority or priority_for(kind, text, due_at)
    score = impact_score(kind, priority, due_at, _link_weight(db, entity_type, entity_id))
    if existing:
        if existing.status not in {"done", "dismissed"}:
            existing.description = text[:20_000]; existing.source_excerpt = text[:1000]
            existing.assignee_user_id = assignee_user_id or existing.assignee_user_id
            existing.due_at = due_at or existing.due_at; existing.priority = priority
            existing.impact_score = score; existing.confidence = max(existing.confidence, confidence)
        return existing, False
    item = ProjectInsight(
        project_id=project.id, kind=kind, title=title, description=text[:20_000], status="open",
        priority=priority, visibility=visibility, source_type=source_type, source_id=source_id,
        source_excerpt=text[:1000], assignee_user_id=assignee_user_id, due_at=due_at,
        confidence=max(0, min(1, confidence)), impact_score=score, fingerprint=key,
        extra=extra, created_by_user_id=created_by_user_id,
    )
    db.add(item); db.flush()
    if entity_type and entity_id:
        db.add(InsightLink(insight_id=item.id, entity_type=entity_type, entity_id=entity_id,
                           relation_type="derived_from", weight=1))
    return item, True


def _extract_rules(db: Session, project: Project, user_id: str, limit: int) -> dict[str, int]:
    inserted = updated = scanned = 0
    rows = db.execute(select(Message, Conversation).join(
        Conversation, Conversation.id == Message.conversation_id
    ).where(
        Conversation.project_id == project.id, Conversation.is_project_wide.is_(True),
        Message.deleted_at.is_(None), Message.body.is_not(None),
    ).order_by(Message.created_at.desc()).limit(limit)).all()
    for message, conversation in rows:
        scanned += 1; text = message.body or ""
        for kind, confidence in classify_text(text):
            due = extract_due_at(text, message.created_at)
            assignee = (message.mentioned_user_ids or [None])[0]
            if kind == "commitment" and not assignee: assignee = message.author_user_id
            _, created = _upsert_insight(
                db, project, kind=kind, text=text, source_type="message", source_id=message.id,
                visibility="client", assignee_user_id=assignee, due_at=due, confidence=confidence,
                entity_type="conversation", entity_id=conversation.id,
            )
            inserted += int(created); updated += int(not created)

    reviews = db.scalars(select(AssetReview).join(
        ContentAttachment, ContentAttachment.id == AssetReview.attachment_id
    ).where(ContentAttachment.project_id == project.id, AssetReview.status.in_(["open", "in_progress"])).limit(limit)).all()
    for review in reviews:
        scanned += 1
        _, created = _upsert_insight(
            db, project, kind="action", text=review.body, source_type="review", source_id=review.id,
            visibility=review.visibility, assignee_user_id=review.assignee_user_id, confidence=1,
            priority="high" if review.status == "open" else "normal",
            entity_type="asset", entity_id=review.attachment_id,
        )
        inserted += int(created); updated += int(not created)

    horizon = _now() + timedelta(days=7)
    contents = db.scalars(select(ContentItem).where(
        ContentItem.project_id == project.id, ContentItem.status == "active",
        ContentItem.due_at.is_not(None), ContentItem.due_at <= horizon,
    ).limit(limit)).all()
    for content in contents:
        scanned += 1; overdue = aware_datetime(content.due_at) < _now()
        text = f"{'Просрочен' if overdue else 'Скоро срок'} материал «{content.title}» — {content.due_at.isoformat()}"
        _, created = _upsert_insight(
            db, project, kind="risk" if overdue else "action", text=text,
            source_type="content", source_id=content.id, visibility="client",
            assignee_user_id=content.assignee_user_id, due_at=content.due_at,
            confidence=1, priority="urgent" if overdue else "high", entity_type="content", entity_id=content.id,
        )
        inserted += int(created); updated += int(not created)

    approvals = db.execute(select(AssetApproval, ContentAttachment).join(
        ContentAttachment, ContentAttachment.id == AssetApproval.attachment_id
    ).where(ContentAttachment.project_id == project.id, ContentAttachment.is_current.is_(True),
            AssetApproval.decision == "changes_requested").limit(limit)).all()
    for approval, attachment in approvals:
        scanned += 1; text = approval.comment or f"Запрошены изменения файла «{attachment.original_name}»"
        _, created = _upsert_insight(
            db, project, kind="action", text=text, source_type="approval", source_id=approval.id,
            visibility="client", confidence=1, priority="high", entity_type="asset", entity_id=attachment.id,
        )
        inserted += int(created); updated += int(not created)
    return {"scanned": scanned, "inserted": inserted, "updated": updated}


def _extract_ai(db: Session, project: Project, user_id: str, visibility: str) -> dict[str, object]:
    messages = db.execute(select(Message, Conversation).join(
        Conversation, Conversation.id == Message.conversation_id
    ).where(Conversation.project_id == project.id, Conversation.is_project_wide.is_(True),
            Message.deleted_at.is_(None), Message.body.is_not(None)).order_by(Message.created_at.desc()).limit(150)).all()
    sources = [{"source_type": "message", "source_id": message.id, "text": message.body,
                "author_user_id": message.author_user_id, "conversation_id": conversation.id}
               for message, conversation in messages]
    prompt = json.dumps({"project": project.name, "sources": sources}, ensure_ascii=False)
    result = generate_text(
        prompt,
        "Проанализируй русскоязычную командную переписку. Верни только JSON-объект с массивом insights. "
        "Каждый элемент: kind decision|commitment|action|risk|question, title, description, source_id из входа, "
        "priority low|normal|high|urgent, confidence 0..1. Не додумывай факты и не создавай элементы без явного свидетельства.",
        max_output_tokens=3000,
    )
    parsed = parse_ai_intelligence(result["text"]); allowed = {item["source_id"]: item for item in sources}
    inserted = 0
    for candidate in parsed.get("insights", []) if isinstance(parsed.get("insights"), list) else []:
        if not isinstance(candidate, dict) or candidate.get("source_id") not in allowed or candidate.get("kind") not in {"decision", "commitment", "action", "risk", "question"}: continue
        source = allowed[candidate["source_id"]]; text = str(candidate.get("description") or source["text"])
        _, created = _upsert_insight(
            db, project, kind=candidate["kind"], text=text, source_type="message_ai",
            source_id=candidate["source_id"], visibility=visibility,
            assignee_user_id=source["author_user_id"] if candidate["kind"] == "commitment" else None,
            due_at=extract_due_at(text), confidence=_safe_confidence(candidate.get("confidence")),
            title=str(candidate.get("title") or "")[:240] or None,
            priority=candidate.get("priority") if candidate.get("priority") in {"low", "normal", "high", "urgent"} else None,
            entity_type="conversation", entity_id=source["conversation_id"], extra={"model": result.get("model")},
        )
        inserted += int(created)
    return {"inserted": inserted, "model": result.get("model")}


def _attention_data(db: Session, project: Project, member: WorkspaceMember, viewer_id: str) -> dict[str, object]:
    now = _now(); insights = _visible_insights(db, project.id, member, include_closed=False)
    review_statement = select(AssetReview, ContentAttachment).join(
        ContentAttachment, ContentAttachment.id == AssetReview.attachment_id
    ).where(ContentAttachment.project_id == project.id, AssetReview.status.in_(["open", "in_progress"]))
    if member.role == "client": review_statement = review_statement.where(AssetReview.visibility == "client")
    reviews = db.execute(review_statement.order_by(AssetReview.updated_at.desc()).limit(100)).all()
    overdue = db.scalars(select(ContentItem).where(
        ContentItem.project_id == project.id, ContentItem.status == "active",
        ContentItem.due_at.is_not(None), ContentItem.due_at < now,
    ).order_by(ContentItem.due_at).limit(100)).all()
    changes = db.execute(select(AssetApproval, ContentAttachment).join(
        ContentAttachment, ContentAttachment.id == AssetApproval.attachment_id
    ).where(ContentAttachment.project_id == project.id, ContentAttachment.is_current.is_(True),
            AssetApproval.decision == "changes_requested").limit(100)).all()
    unread = 0
    conversations = db.scalars(select(Conversation).where(
        Conversation.project_id == project.id, Conversation.is_project_wide.is_(True)
    )).all()
    for conversation in conversations:
        participant = db.scalar(select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation.id,
            ConversationParticipant.user_id == viewer_id,
        ))
        statement = select(func.count(Message.id)).where(
            Message.conversation_id == conversation.id, Message.deleted_at.is_(None),
            Message.author_user_id != viewer_id,
        )
        if participant and participant.last_read_at: statement = statement.where(Message.created_at > participant.last_read_at)
        unread += int(db.scalar(statement) or 0)
    stats = {
        "open_insights": len(insights), "urgent": sum(item.priority == "urgent" for item in insights),
        "open_reviews": len(reviews), "overdue": len(overdue), "changes_requested": len(changes),
        "unread_messages": unread,
    }
    items = []
    for insight in insights[:50]: items.append({"type": "insight", "id": insight.id, "title": insight.title, "detail": insight.description, "priority": insight.priority, "impact_score": insight.impact_score, "due_at": insight.due_at.isoformat() if insight.due_at else None})
    for review, attachment in reviews[:20]: items.append({"type": "review", "id": review.id, "title": review.body[:240], "detail": attachment.original_name, "priority": "high" if review.status == "open" else "normal", "impact_score": 6, "attachment_id": attachment.id})
    for content in overdue[:20]: items.append({"type": "overdue", "id": content.id, "title": content.title, "detail": "Просрочен материал", "priority": "urgent", "impact_score": 10, "due_at": content.due_at.isoformat()})
    items.sort(key=lambda item: (-float(item.get("impact_score") or 0), item.get("due_at") or "9999"))
    briefing_statement = select(ProjectBriefing).where(ProjectBriefing.project_id == project.id)
    if member.role == "client": briefing_statement = briefing_statement.where(ProjectBriefing.visibility == "client")
    latest = db.scalar(briefing_statement.order_by(ProjectBriefing.generated_at.desc()).limit(1))
    score = min(100, stats["urgent"] * 15 + stats["overdue"] * 12 + stats["changes_requested"] * 8 + stats["open_reviews"] * 3 + min(15, stats["unread_messages"]))
    return {"project_id": project.id, "score": score, "stats": stats, "items": items,
            "insights": [_insight_payload(db, item, viewer_id) for item in insights],
            "latest_briefing": _briefing_payload(latest) if latest else None}


def _briefing_payload(item: ProjectBriefing) -> dict[str, object]:
    return {"id": item.id, "project_id": item.project_id, "summary": item.summary,
            "highlights": item.highlights or [], "risks": item.risks or [], "next_actions": item.next_actions or [],
            "visibility": item.visibility, "provider": item.provider, "model": item.model,
            "source_stats": item.source_stats or {}, "generated_at": item.generated_at.isoformat()}


@router.get("/projects/{project_id}/insights")
def list_insights(project_id: str, request: Request, status: str | None = None, kind: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    _, member = _project_access(db, project_id, request.state.user.id)
    items = _visible_insights(db, project_id, member)
    if status: items = [item for item in items if item.status == status]
    if kind: items = [item for item in items if item.kind == kind]
    return [_insight_payload(db, item, request.state.user.id) for item in items]


@router.post("/projects/{project_id}/insights", status_code=201)
def create_insight(project_id: str, payload: InsightCreate, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    project, member = _project_access(db, project_id, request.state.user.id)
    if member.role == "client" and payload.visibility != "client": raise HTTPException(403, "Клиентская запись должна быть видима заказчику.")
    _validate_assignee(db, project, payload.assignee_user_id)
    item, _ = _upsert_insight(
        db, project, kind=payload.kind, text=payload.description or payload.title,
        source_type="manual", source_id=str(uuid.uuid4()), visibility=payload.visibility,
        assignee_user_id=payload.assignee_user_id, due_at=payload.due_at, confidence=1,
        title=payload.title, priority=payload.priority, created_by_user_id=request.state.user.id,
    )
    for link in payload.links:
        _validate_entity(db, project, link.entity_type, link.entity_id, request.state.user.id)
        if member.role == "client" and link.entity_type == "review":
            review = db.get(AssetReview, link.entity_id)
            if review is None or review.visibility != "client":
                raise HTTPException(404, "Связанное замечание не найдено.")
        db.add(InsightLink(insight_id=item.id, **link.model_dump()))
    item.impact_score = impact_score(item.kind, item.priority, item.due_at, sum(link.weight for link in payload.links))
    db.commit(); _event(project.id, "insight.created", request.state.user.id, insight_id=item.id)
    return _insight_payload(db, item, request.state.user.id)


@router.patch("/insights/{insight_id}")
def update_insight(insight_id: str, payload: InsightUpdate, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    item = db.get(ProjectInsight, insight_id)
    if item is None: raise HTTPException(404, "Сигнал не найден.")
    project, member = _project_access(db, item.project_id, request.state.user.id)
    if member.role == "client" and item.visibility != "client":
        raise HTTPException(404, "Сигнал не найден.")
    if not (has_role(member, "editor") or item.created_by_user_id == request.state.user.id or item.assignee_user_id == request.state.user.id): raise HTTPException(403, "Недостаточно прав для изменения сигнала.")
    values = payload.model_dump(exclude_unset=True)
    if member.role == "client" and values.get("visibility") not in {None, "client"}: raise HTTPException(403, "Клиент не может создать внутреннюю запись.")
    if "assignee_user_id" in values: _validate_assignee(db, project, values["assignee_user_id"])
    for field in ("title", "description", "priority", "visibility", "assignee_user_id", "due_at"):
        if field in values: setattr(item, field, values[field].strip() if isinstance(values[field], str) else values[field])
    if "status" in values:
        item.status = values["status"]
        if item.status in {"done", "dismissed"}: item.completed_at = _now(); item.completed_by_user_id = request.state.user.id
        else: item.completed_at = None; item.completed_by_user_id = None
    item.impact_score = impact_score(item.kind, item.priority, item.due_at, sum(link["weight"] for link in _insight_links(db, item.id)))
    db.commit(); _event(project.id, "insight.updated", request.state.user.id, insight_id=item.id, status=item.status)
    return _insight_payload(db, item, request.state.user.id)


@router.delete("/insights/{insight_id}", status_code=204)
def dismiss_insight(insight_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    item = db.get(ProjectInsight, insight_id)
    if item is None: raise HTTPException(404, "Сигнал не найден.")
    _, member = _project_access(db, item.project_id, request.state.user.id)
    if member.role == "client" and item.visibility != "client":
        raise HTTPException(404, "Сигнал не найден.")
    if not (has_role(member, "editor") or item.created_by_user_id == request.state.user.id): raise HTTPException(403, "Недостаточно прав.")
    item.status = "dismissed"; item.completed_at = _now(); item.completed_by_user_id = request.state.user.id; db.commit()
    _event(item.project_id, "insight.dismissed", request.state.user.id, insight_id=item.id)


@router.post("/projects/{project_id}/insights/extract")
def extract_insights(project_id: str, payload: ExtractRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    project, member = _project_access(db, project_id, request.state.user.id); _require_editor(member)
    stats = _extract_rules(db, project, request.state.user.id, payload.limit); ai_result = None; ai_error = None
    if payload.use_ai and ai_enabled():
        try: ai_result = _extract_ai(db, project, request.state.user.id, "team")
        except (AIServiceError, ValueError, json.JSONDecodeError) as exc: ai_error = str(exc)
    db.commit(); _event(project.id, "insights.extracted", request.state.user.id, inserted=stats["inserted"] + int((ai_result or {}).get("inserted", 0)))
    return {**stats, "ai_used": ai_result is not None, "ai": ai_result, "ai_error": ai_error,
            "provider_configured": ai_enabled()}


@router.get("/projects/{project_id}/attention")
def project_attention(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    project, member = _project_access(db, project_id, request.state.user.id)
    return _attention_data(db, project, member, request.state.user.id)


@router.post("/projects/{project_id}/briefings", status_code=201)
def generate_briefing(project_id: str, payload: BriefingRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    project, member = _project_access(db, project_id, request.state.user.id)
    visibility = "client" if member.role == "client" else payload.visibility
    if member.role not in {"client"} and not has_role(member, "editor"): raise HTTPException(403, "Для создания сводки нужна роль редактора.")
    _extract_rules(db, project, request.state.user.id, 500); db.flush()
    attention = _attention_data(db, project, member, request.state.user.id)
    visible = [item for item in attention["insights"] if visibility == "team" or item["visibility"] == "client"]
    generated = deterministic_briefing(project.name, visible, attention["stats"]); provider = "rules"; model = None
    if payload.use_ai and ai_enabled():
        try:
            response = generate_text(
                json.dumps({"project": project.name, "stats": attention["stats"], "insights": visible[:100]}, ensure_ascii=False),
                "Составь короткую управленческую сводку проекта на русском. Верни только JSON: summary строка, "
                "highlights массив объектов title/detail, risks массив объектов title/detail, next_actions массив объектов title/detail. "
                "Не придумывай факты и сохрани конкретику сроков и ответственных.", max_output_tokens=2200,
            )
            parsed = parse_ai_intelligence(response["text"])
            if isinstance(parsed.get("summary"), str):
                generated = {"summary": parsed["summary"][:10_000],
                             "highlights": _briefing_entries(parsed.get("highlights")),
                             "risks": _briefing_entries(parsed.get("risks")),
                             "next_actions": _briefing_entries(parsed.get("next_actions"))}
                provider = "openai"; model = response.get("model")
        except (AIServiceError, ValueError, json.JSONDecodeError):
            pass
    briefing = ProjectBriefing(project_id=project.id, **generated, visibility=visibility, provider=provider,
                                model=model, source_stats=attention["stats"], generated_by_user_id=request.state.user.id)
    db.add(briefing); db.commit(); _event(project.id, "briefing.generated", request.state.user.id, briefing_id=briefing.id)
    return _briefing_payload(briefing)


@router.get("/projects/{project_id}/briefings")
def list_briefings(project_id: str, request: Request, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    _, member = _project_access(db, project_id, request.state.user.id)
    statement = select(ProjectBriefing).where(ProjectBriefing.project_id == project_id)
    if member.role == "client": statement = statement.where(ProjectBriefing.visibility == "client")
    return [_briefing_payload(item) for item in db.scalars(statement.order_by(ProjectBriefing.generated_at.desc()).limit(30)).all()]
