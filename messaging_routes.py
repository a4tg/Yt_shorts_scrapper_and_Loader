from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from database import get_db
from realtime_service import project_events
from saas_models import (
    ContentAttachment,
    ContentItem,
    Conversation,
    ConversationParticipant,
    Message,
    MessageReaction,
    Project,
    User,
    WorkspaceMember,
)
from asset_preview import preview_capabilities
from workspace_service import project_membership


router = APIRouter(prefix="/api", tags=["messages"])


class ConversationCreate(BaseModel):
    kind: Literal["group", "direct"]
    name: str | None = Field(default=None, max_length=120)
    participant_user_ids: list[str] = Field(default_factory=list, max_length=100)
    is_project_wide: bool = False


class ConversationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    participant_user_ids: list[str] | None = Field(default=None, max_length=100)


class MessageCreate(BaseModel):
    body: str | None = Field(default=None, max_length=10_000)
    reply_to_message_id: str | None = Field(default=None, max_length=36)
    attachment_id: str | None = Field(default=None, max_length=36)
    mentioned_user_ids: list[str] = Field(default_factory=list, max_length=50)


class MessageUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    mentioned_user_ids: list[str] | None = Field(default=None, max_length=50)


class ReactionCreate(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)


SUPPORTED_REACTIONS = {"👍", "❤️", "🔥", "🎉", "👀", "✅"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _project_access(db: Session, project_id: str, user_id: str) -> tuple[Project, WorkspaceMember]:
    access = project_membership(db, project_id, user_id)
    if access is None:
        raise HTTPException(404, "Проект не найден")
    return access


def _participant(
    db: Session, conversation_id: str, user_id: str, *, create: bool = False
) -> ConversationParticipant | None:
    participant = db.scalar(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
        )
    )
    if participant is None and create:
        participant = ConversationParticipant(conversation_id=conversation_id, user_id=user_id)
        db.add(participant)
        db.flush()
    return participant


def _conversation_access(
    db: Session, conversation_id: str, user_id: str, *, create_project_wide_participant: bool = True
) -> tuple[Conversation, Project, WorkspaceMember, ConversationParticipant]:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Диалог не найден")
    project, member = _project_access(db, conversation.project_id, user_id)
    participant = _participant(
        db, conversation.id, user_id,
        create=conversation.is_project_wide and create_project_wide_participant,
    )
    if participant is None:
        raise HTTPException(404, "Диалог не найден")
    return conversation, project, member, participant


def _workspace_users(db: Session, workspace_id: str, user_ids: list[str]) -> list[str]:
    unique_ids = list(dict.fromkeys(user_ids))
    if not unique_ids:
        return []
    found = set(db.scalars(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id.in_(unique_ids),
        )
    ).all())
    if found != set(unique_ids):
        raise HTTPException(400, "Все участники диалога должны состоять в рабочем пространстве.")
    return unique_ids


def _attachment_payload(attachment: ContentAttachment | None) -> dict[str, object] | None:
    if attachment is None:
        return None
    return {
        "id": attachment.id,
        "project_id": attachment.project_id,
        "name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "size_bytes": attachment.size_bytes,
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


def _mention_ids(
    db: Session, conversation: Conversation, requested: list[str]
) -> list[str]:
    project = db.get(Project, conversation.project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    user_ids = _workspace_users(db, project.workspace_id, requested)
    if not conversation.is_project_wide and user_ids:
        participants = set(db.scalars(select(ConversationParticipant.user_id).where(
            ConversationParticipant.conversation_id == conversation.id
        )).all())
        if not set(user_ids).issubset(participants):
            raise HTTPException(400, "Упоминать можно только участников диалога.")
    return user_ids


def _publish_message_event(
    conversation: Conversation,
    event_type: str,
    actor_user_id: str,
    message_id: str | None = None,
) -> None:
    project_events.publish(conversation.project_id, {
        "type": event_type,
        "project_id": conversation.project_id,
        "conversation_id": conversation.id,
        "message_id": message_id,
        "actor_user_id": actor_user_id,
        "created_at": _now().isoformat(),
    })


def _message_payload(db: Session, message: Message, current_user_id: str) -> dict[str, object]:
    author = db.get(User, message.author_user_id)
    attachment = db.get(ContentAttachment, message.attachment_id) if message.attachment_id else None
    reply = db.get(Message, message.reply_to_message_id) if message.reply_to_message_id else None
    reply_author = db.get(User, reply.author_user_id) if reply else None
    deleted = message.deleted_at is not None
    reaction_rows = db.execute(
        select(MessageReaction, User)
        .join(User, User.id == MessageReaction.user_id)
        .where(MessageReaction.message_id == message.id)
        .order_by(MessageReaction.created_at, MessageReaction.id)
    ).all() if not deleted else []
    grouped_reactions: dict[str, dict[str, object]] = {}
    for reaction, user in reaction_rows:
        group = grouped_reactions.setdefault(reaction.emoji, {
            "emoji": reaction.emoji, "count": 0, "reacted_by_me": False, "users": [],
        })
        group["count"] = int(group["count"]) + 1
        group["reacted_by_me"] = bool(group["reacted_by_me"] or user.id == current_user_id)
        group["users"].append({"id": user.id, "name": user.display_name or user.email})
    mentioned_users = []
    for user_id in message.mentioned_user_ids or []:
        user = db.get(User, user_id)
        if user is not None:
            mentioned_users.append({"id": user.id, "name": user.display_name or user.email})
    pinned_by = db.get(User, message.pinned_by_user_id) if message.pinned_by_user_id else None
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "body": None if deleted else (message.body or ""),
        "author": {
            "id": author.id,
            "name": author.display_name or author.email,
            "email": author.email,
        },
        "reply_to": ({
            "id": reply.id,
            "body": None if reply.deleted_at else (reply.body or ""),
            "author_name": reply_author.display_name or reply_author.email,
            "deleted": reply.deleted_at is not None,
        } if reply and reply_author else None),
        "attachment": None if deleted else _attachment_payload(attachment),
        "attachment_name": None if deleted else message.attachment_name,
        "mentions": [] if deleted else mentioned_users,
        "reactions": list(grouped_reactions.values()),
        "is_pinned": not deleted and message.pinned_at is not None,
        "pinned_at": message.pinned_at.isoformat() if not deleted and message.pinned_at else None,
        "pinned_by": ({
            "id": pinned_by.id,
            "name": pinned_by.display_name or pinned_by.email,
        } if not deleted and pinned_by else None),
        "created_at": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "deleted_at": message.deleted_at.isoformat() if message.deleted_at else None,
        "is_own": message.author_user_id == current_user_id,
    }


@router.get("/projects/{project_id}/message-events")
async def message_events(
    project_id: str, request: Request, db: Session = Depends(get_db)
) -> StreamingResponse:
    _project_access(db, project_id, request.state.user.id)
    user_id = request.state.user.id

    async def stream():
        yield f"event: ready\ndata: {json.dumps({'project_id': project_id})}\n\n"
        async with project_events.subscribe(project_id) as queue:
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                # The payload contains identifiers only; authorization remains endpoint-based.
                event = {**event, "viewer_user_id": user_id}
                yield f"event: project-message\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _conversation_payload(
    db: Session, conversation: Conversation, current_user_id: str
) -> dict[str, object]:
    participant = _participant(db, conversation.id, current_user_id, create=conversation.is_project_wide)
    rows = db.execute(
        select(ConversationParticipant, User)
        .join(User, User.id == ConversationParticipant.user_id)
        .where(ConversationParticipant.conversation_id == conversation.id)
        .order_by(User.display_name, User.email)
    ).all()
    last_message = db.scalar(
        select(Message).where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(1)
    )
    unread_filters = [
        Message.conversation_id == conversation.id,
        Message.author_user_id != current_user_id,
        Message.deleted_at.is_(None),
    ]
    if participant and participant.last_read_at:
        unread_filters.append(Message.created_at > participant.last_read_at)
    unread_count = db.scalar(select(func.count(Message.id)).where(*unread_filters)) or 0
    content_item = db.get(ContentItem, conversation.content_item_id) if conversation.content_item_id else None
    display_name = conversation.name or "Диалог"
    if conversation.kind == "direct":
        other = next((user for _, user in rows if user.id != current_user_id), None)
        if other:
            display_name = other.display_name or other.email
    return {
        "id": conversation.id,
        "project_id": conversation.project_id,
        "content_item_id": conversation.content_item_id,
        "content_title": content_item.title if content_item else None,
        "kind": conversation.kind,
        "name": display_name,
        "is_project_wide": conversation.is_project_wide,
        "created_by_user_id": conversation.created_by_user_id,
        "participants": [{
            "id": user.id,
            "name": user.display_name or user.email,
            "email": user.email,
        } for _, user in rows],
        "unread_count": int(unread_count),
        "last_message": (_message_payload(db, last_message, current_user_id) if last_message else None),
        "updated_at": (last_message.created_at if last_message else conversation.updated_at).isoformat(),
    }


def _ensure_general_chat(db: Session, project: Project, user_id: str) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.project_id == project.id,
            Conversation.conversation_key == "general",
        )
    )
    if conversation is None:
        conversation = Conversation(
            project_id=project.id,
            kind="group",
            conversation_key="general",
            name="Общий чат",
            is_project_wide=True,
            created_by_user_id=project.created_by_user_id,
        )
        db.add(conversation)
        db.flush()
    _participant(db, conversation.id, user_id, create=True)
    return conversation


@router.get("/projects/{project_id}/conversations")
def list_conversations(
    project_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    project, _ = _project_access(db, project_id, request.state.user.id)
    _ensure_general_chat(db, project, request.state.user.id)
    participating = select(ConversationParticipant.conversation_id).where(
        ConversationParticipant.user_id == request.state.user.id
    )
    conversations = db.scalars(
        select(Conversation).where(
            Conversation.project_id == project_id,
            or_(Conversation.is_project_wide.is_(True), Conversation.id.in_(participating)),
        )
    ).all()
    for conversation in conversations:
        if conversation.is_project_wide:
            _participant(db, conversation.id, request.state.user.id, create=True)
    db.commit()
    payload = [_conversation_payload(db, conversation, request.state.user.id) for conversation in conversations]
    return sorted(payload, key=lambda item: str(item["updated_at"]), reverse=True)


@router.post("/projects/{project_id}/conversations", status_code=201)
def create_conversation(
    project_id: str,
    payload: ConversationCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project, _ = _project_access(db, project_id, request.state.user.id)
    requested = [user_id for user_id in payload.participant_user_ids if user_id != request.state.user.id]
    participants = _workspace_users(db, project.workspace_id, requested)
    conversation_key = None
    name = (payload.name or "").strip() or None
    if payload.kind == "direct":
        if len(participants) != 1:
            raise HTTPException(400, "Для личного диалога выберите одного собеседника.")
        conversation_key = "direct:" + ":".join(sorted([request.state.user.id, participants[0]]))
        existing = db.scalar(select(Conversation).where(
            Conversation.project_id == project_id,
            Conversation.conversation_key == conversation_key,
        ))
        if existing:
            return _conversation_payload(db, existing, request.state.user.id)
        name = None
    elif not name:
        raise HTTPException(400, "Укажите название группового чата.")
    conversation = Conversation(
        project_id=project_id,
        kind=payload.kind,
        conversation_key=conversation_key,
        name=name,
        is_project_wide=payload.is_project_wide if payload.kind == "group" else False,
        created_by_user_id=request.state.user.id,
    )
    db.add(conversation)
    db.flush()
    for user_id in [request.state.user.id, *participants]:
        _participant(db, conversation.id, user_id, create=True)
    db.commit()
    _publish_message_event(conversation, "conversation.created", request.state.user.id)
    return _conversation_payload(db, conversation, request.state.user.id)


@router.post("/content/{item_id}/conversation", status_code=201)
def content_conversation(
    item_id: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    item = db.get(ContentItem, item_id)
    if item is None:
        raise HTTPException(404, "Материал не найден")
    project, _ = _project_access(db, item.project_id, request.state.user.id)
    key = f"content:{item.id}"
    conversation = db.scalar(select(Conversation).where(
        Conversation.project_id == project.id,
        Conversation.conversation_key == key,
    ))
    created = conversation is None
    if conversation is None:
        conversation = Conversation(
            project_id=project.id,
            content_item_id=item.id,
            kind="context",
            conversation_key=key,
            name=f"Обсуждение: {item.title}"[:120],
            is_project_wide=True,
            created_by_user_id=request.state.user.id,
        )
        db.add(conversation)
        db.flush()
    _participant(db, conversation.id, request.state.user.id, create=True)
    db.commit()
    if created:
        _publish_message_event(conversation, "conversation.created", request.state.user.id)
    return _conversation_payload(db, conversation, request.state.user.id)


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    conversation, project, member, _ = _conversation_access(db, conversation_id, request.state.user.id)
    if conversation.kind != "group" or conversation.conversation_key == "general":
        raise HTTPException(400, "Этот диалог нельзя перенастроить.")
    if conversation.created_by_user_id != request.state.user.id and member.role not in {"owner", "admin"}:
        raise HTTPException(403, "Изменять групповой чат может его создатель или администратор.")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        name = values["name"].strip()
        if not name:
            raise HTTPException(400, "Название чата не может быть пустым.")
        conversation.name = name
    if values.get("participant_user_ids") is not None:
        participant_ids = _workspace_users(db, project.workspace_id, values["participant_user_ids"])
        keep = set(participant_ids) | {conversation.created_by_user_id, request.state.user.id}
        existing = db.scalars(select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation.id
        )).all()
        for participant in existing:
            if participant.user_id not in keep:
                db.delete(participant)
        for user_id in keep:
            _participant(db, conversation.id, user_id, create=True)
    db.commit()
    result = _conversation_payload(db, conversation, request.state.user.id)
    _publish_message_event(conversation, "conversation.updated", request.state.user.id)
    return result


@router.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    request: Request,
    before: datetime | None = None,
    after: datetime | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    conversation, _, _, _ = _conversation_access(db, conversation_id, request.state.user.id)
    limit = max(1, min(limit, 100))
    statement = select(Message).where(Message.conversation_id == conversation.id)
    if after:
        statement = statement.where(Message.created_at > after).order_by(Message.created_at, Message.id).limit(limit)
        messages = db.scalars(statement).all()
    else:
        if before:
            statement = statement.where(Message.created_at < before)
        messages = list(db.scalars(
            statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
        ).all())
        has_more = len(messages) > limit
        messages = list(reversed(messages[:limit]))
        return {
            "messages": [_message_payload(db, message, request.state.user.id) for message in messages],
            "has_more": has_more,
        }
    return {
        "messages": [_message_payload(db, message, request.state.user.id) for message in messages],
        "has_more": False,
    }


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def create_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    conversation, _, _, participant = _conversation_access(db, conversation_id, request.state.user.id)
    body = (payload.body or "").strip() or None
    if body is None and not payload.attachment_id:
        raise HTTPException(400, "Напишите сообщение или прикрепите файл.")
    reply = db.get(Message, payload.reply_to_message_id) if payload.reply_to_message_id else None
    if reply is not None and reply.conversation_id != conversation.id:
        raise HTTPException(400, "Ответ относится к другому диалогу.")
    if payload.reply_to_message_id and reply is None:
        raise HTTPException(400, "Исходное сообщение не найдено.")
    attachment = db.get(ContentAttachment, payload.attachment_id) if payload.attachment_id else None
    if attachment is not None and attachment.project_id != conversation.project_id:
        raise HTTPException(400, "Файл относится к другому проекту.")
    if payload.attachment_id and attachment is None:
        raise HTTPException(400, "Файл не найден.")
    message = Message(
        conversation_id=conversation.id,
        author_user_id=request.state.user.id,
        reply_to_message_id=reply.id if reply else None,
        attachment_id=attachment.id if attachment else None,
        attachment_name=attachment.original_name if attachment else None,
        body=body,
        mentioned_user_ids=_mention_ids(db, conversation, payload.mentioned_user_ids),
        created_at=_now(),
    )
    db.add(message)
    db.flush()
    participant.last_read_at = _now()
    db.commit()
    result = _message_payload(db, message, request.state.user.id)
    _publish_message_event(conversation, "message.created", request.state.user.id, message.id)
    return result


@router.patch("/messages/{message_id}")
def update_message(
    message_id: str,
    payload: MessageUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(404, "Сообщение не найдено")
    _conversation_access(db, message.conversation_id, request.state.user.id)
    if message.author_user_id != request.state.user.id or message.deleted_at is not None:
        raise HTTPException(403, "Можно редактировать только свои сообщения.")
    body = payload.body.strip()
    if not body:
        raise HTTPException(400, "Сообщение не может быть пустым.")
    message.body = body
    if payload.mentioned_user_ids is not None:
        message.mentioned_user_ids = _mention_ids(db, db.get(Conversation, message.conversation_id), payload.mentioned_user_ids)
    message.edited_at = _now()
    db.commit()
    result = _message_payload(db, message, request.state.user.id)
    conversation = db.get(Conversation, message.conversation_id)
    _publish_message_event(conversation, "message.updated", request.state.user.id, message.id)
    return result


@router.delete("/messages/{message_id}", status_code=204)
def delete_message(
    message_id: str, request: Request, db: Session = Depends(get_db)
) -> None:
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(404, "Сообщение не найдено")
    conversation, _, _, _ = _conversation_access(db, message.conversation_id, request.state.user.id)
    if message.author_user_id != request.state.user.id:
        raise HTTPException(403, "Можно удалить только своё сообщение.")
    message.body = None
    message.attachment_id = None
    message.mentioned_user_ids = None
    message.pinned_at = None
    message.pinned_by_user_id = None
    message.deleted_at = _now()
    db.execute(delete(MessageReaction).where(MessageReaction.message_id == message.id))
    db.commit()
    _publish_message_event(conversation, "message.deleted", request.state.user.id, message.id)


@router.get("/conversations/{conversation_id}/pinned-messages")
def pinned_messages(
    conversation_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    conversation, _, _, _ = _conversation_access(db, conversation_id, request.state.user.id)
    messages = db.scalars(select(Message).where(
        Message.conversation_id == conversation.id,
        Message.pinned_at.is_not(None),
        Message.deleted_at.is_(None),
    ).order_by(Message.pinned_at.desc())).all()
    return [_message_payload(db, message, request.state.user.id) for message in messages]


@router.post("/messages/{message_id}/pin")
def pin_message(
    message_id: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    message = db.get(Message, message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(404, "Сообщение не найдено")
    conversation, _, _, _ = _conversation_access(db, message.conversation_id, request.state.user.id)
    message.pinned_at = _now()
    message.pinned_by_user_id = request.state.user.id
    db.commit()
    result = _message_payload(db, message, request.state.user.id)
    _publish_message_event(conversation, "message.pinned", request.state.user.id, message.id)
    return result


@router.delete("/messages/{message_id}/pin", status_code=204)
def unpin_message(
    message_id: str, request: Request, db: Session = Depends(get_db)
) -> None:
    message = db.get(Message, message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(404, "Сообщение не найдено")
    conversation, _, _, _ = _conversation_access(db, message.conversation_id, request.state.user.id)
    message.pinned_at = None
    message.pinned_by_user_id = None
    db.commit()
    _publish_message_event(conversation, "message.unpinned", request.state.user.id, message.id)


@router.post("/messages/{message_id}/reactions", status_code=201)
def add_reaction(
    message_id: str,
    payload: ReactionCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.emoji not in SUPPORTED_REACTIONS:
        raise HTTPException(400, "Эта реакция не поддерживается.")
    message = db.get(Message, message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(404, "Сообщение не найдено")
    conversation, _, _, _ = _conversation_access(db, message.conversation_id, request.state.user.id)
    existing = db.scalar(select(MessageReaction).where(
        MessageReaction.message_id == message.id,
        MessageReaction.user_id == request.state.user.id,
        MessageReaction.emoji == payload.emoji,
    ))
    if existing is None:
        db.add(MessageReaction(
            message_id=message.id, user_id=request.state.user.id, emoji=payload.emoji
        ))
        db.commit()
    result = _message_payload(db, message, request.state.user.id)
    _publish_message_event(conversation, "message.reacted", request.state.user.id, message.id)
    return result


@router.delete("/messages/{message_id}/reactions", status_code=204)
def remove_reaction(
    message_id: str,
    emoji: str,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    message = db.get(Message, message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(404, "Сообщение не найдено")
    conversation, _, _, _ = _conversation_access(db, message.conversation_id, request.state.user.id)
    db.execute(delete(MessageReaction).where(
        MessageReaction.message_id == message.id,
        MessageReaction.user_id == request.state.user.id,
        MessageReaction.emoji == emoji,
    ))
    db.commit()
    _publish_message_event(conversation, "message.reaction_removed", request.state.user.id, message.id)


@router.post("/conversations/{conversation_id}/read", status_code=204)
def mark_conversation_read(
    conversation_id: str, request: Request, db: Session = Depends(get_db)
) -> None:
    _, _, _, participant = _conversation_access(db, conversation_id, request.state.user.id)
    participant.last_read_at = _now()
    db.commit()
