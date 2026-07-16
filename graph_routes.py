from __future__ import annotations

import json
import math
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, distinct, exists, or_, select
from sqlalchemy.orm import Session

from database import get_db
from realtime_service import project_events
from saas_models import (
    ApprovalStage, ApprovalWorkflow, AssetReview, ContentAttachment, ContentItem,
    Conversation, ConversationParticipant, DiagramEdge, DiagramNode, EntityLink, Message, Project,
    ProjectDiagram, User, WorkspaceMember,
)
from workspace_service import has_role, project_membership


router = APIRouter(prefix="/api", tags=["project-graph"])
EntityType = Literal["project", "content", "asset", "conversation", "review", "user", "diagram"]
RelationType = Literal["relates_to", "depends_on", "blocks", "produces", "references", "assigned_to", "custom"]
NodeKind = Literal["start", "end", "task", "decision", "document", "asset", "person", "note"]
EdgeType = Literal["default", "success", "failure", "conditional"]


class EntityLinkCreate(BaseModel):
    source_type: EntityType
    source_id: str = Field(max_length=36)
    target_type: EntityType
    target_id: str = Field(max_length=36)
    relation_type: RelationType = "relates_to"
    label: str | None = Field(default=None, max_length=160)
    weight: float = Field(default=1, gt=0, le=100)
    extra: dict[str, object] | None = None


class DiagramCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    diagram_type: Literal["process", "flowchart", "mind_map"] = "flowchart"
    template: Literal["blank", "approval"] = "blank"


class DiagramNodeInput(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    kind: NodeKind = "task"
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=2000)
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)
    width: float = Field(default=180, ge=80, le=1000)
    height: float = Field(default=80, ge=40, le=1000)
    color: str | None = Field(default=None, max_length=16, pattern=r"^#[0-9a-fA-F]{6}$")
    entity_type: EntityType | None = None
    entity_id: str | None = Field(default=None, max_length=36)
    extra: dict[str, object] | None = None


class DiagramEdgeInput(BaseModel):
    source_key: str = Field(min_length=1, max_length=80)
    target_key: str = Field(min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=160)
    edge_type: EdgeType = "default"
    extra: dict[str, object] | None = None


class DiagramSave(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    diagram_type: Literal["process", "flowchart", "mind_map"] = "flowchart"
    viewport: dict[str, object] | None = None
    nodes: list[DiagramNodeInput] = Field(max_length=300)
    edges: list[DiagramEdgeInput] = Field(max_length=600)


def _project_access(db: Session, project_id: str, user_id: str):
    access = project_membership(db, project_id, user_id)
    if access is None:
        raise HTTPException(404, "Проект не найден.")
    return access


def _require_editor(member: WorkspaceMember) -> None:
    if not has_role(member, "editor"):
        raise HTTPException(403, "Для изменения карты нужна роль редактора.")


def _event(project_id: str, event_type: str, user_id: str, **payload: object) -> None:
    project_events.publish(project_id, {
        "type": event_type, "project_id": project_id, "actor_user_id": user_id, **payload,
    })


def _graph_node(entity_type: str, entity_id: str, label: str, *, kind: str, subtitle: str = "", status: str | None = None, x: float = 0, y: float = 0, extra: dict | None = None) -> dict[str, object]:
    return {
        "id": f"{entity_type}:{entity_id}", "entity_type": entity_type, "entity_id": entity_id,
        "label": label, "kind": kind, "subtitle": subtitle, "status": status,
        "x": x, "y": y, "extra": extra or {},
    }


def _graph_edge(source_type: str, source_id: str, target_type: str, target_id: str, relation: str, *, label: str | None = None, manual_id: str | None = None, weight: float = 1) -> dict[str, object]:
    return {
        "id": manual_id or f"auto:{source_type}:{source_id}:{relation}:{target_type}:{target_id}",
        "source": f"{source_type}:{source_id}", "target": f"{target_type}:{target_id}",
        "relation": relation, "label": label, "weight": weight, "manual": manual_id is not None,
    }


def _entity_project_id(db: Session, entity_type: str, entity_id: str) -> str | None:
    if entity_type == "project":
        return entity_id if db.get(Project, entity_id) else None
    model = {"content": ContentItem, "asset": ContentAttachment, "conversation": Conversation, "diagram": ProjectDiagram}.get(entity_type)
    if model:
        entity = db.get(model, entity_id)
        return entity.project_id if entity else None
    if entity_type == "review":
        review = db.get(AssetReview, entity_id); attachment = db.get(ContentAttachment, review.attachment_id) if review else None
        return attachment.project_id if attachment else None
    if entity_type == "user":
        return None
    return None


def _validate_entity(db: Session, project: Project, entity_type: str, entity_id: str, user_id: str | None = None) -> None:
    if entity_type == "conversation" and user_id:
        conversation = db.get(Conversation, entity_id)
        participant = db.scalar(select(ConversationParticipant.id).where(
            ConversationParticipant.conversation_id == entity_id,
            ConversationParticipant.user_id == user_id,
        ))
        if conversation is None or conversation.project_id != project.id or not (conversation.is_project_wide or participant):
            raise HTTPException(400, "Связываемая сущность не принадлежит проекту.")
        return
    if entity_type == "user":
        exists = db.scalar(select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == project.workspace_id, WorkspaceMember.user_id == entity_id,
        ))
        if exists:
            return
    elif _entity_project_id(db, entity_type, entity_id) == project.id:
        return
    raise HTTPException(400, "Связываемая сущность не принадлежит проекту.")


@router.get("/projects/{project_id}/graph")
def project_graph(
    project_id: str, request: Request, types: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project, member = _project_access(db, project_id, request.state.user.id)
    requested = {item.strip() for item in (types or "").split(",") if item.strip()}
    include = lambda value: not requested or value in requested or value == "project"
    nodes: dict[str, dict[str, object]] = {}
    edges: dict[str, dict[str, object]] = {}
    root = _graph_node("project", project.id, project.name, kind="project", subtitle="Проект", status=project.status)
    nodes[root["id"]] = root

    contents = db.scalars(select(ContentItem).where(
        ContentItem.project_id == project.id, ContentItem.status == "active"
    ).order_by(ContentItem.updated_at.desc()).limit(150)).all() if include("content") else []
    for index, item in enumerate(contents):
        angle = (index / max(1, len(contents))) * math.tau
        graph_node = _graph_node("content", item.id, item.title, kind=item.item_type, subtitle=item.channel or item.item_type, status=item.priority, x=-430 + math.cos(angle) * 150, y=math.sin(angle) * 320)
        nodes[graph_node["id"]] = graph_node
        edge = _graph_edge("project", project.id, "content", item.id, "contains"); edges[edge["id"]] = edge

    assets = db.scalars(select(ContentAttachment).where(
        ContentAttachment.project_id == project.id, ContentAttachment.is_current.is_(True)
    ).order_by(ContentAttachment.created_at.desc()).limit(150)).all() if include("asset") else []
    for index, asset in enumerate(assets):
        graph_node = _graph_node("asset", asset.id, asset.original_name, kind="asset", subtitle=f"v{asset.version_number}", status="current", x=math.cos(index * 1.7) * 230, y=math.sin(index * 1.7) * 350)
        nodes[graph_node["id"]] = graph_node
        if asset.content_item_id and f"content:{asset.content_item_id}" in nodes:
            edge = _graph_edge("content", asset.content_item_id, "asset", asset.id, "has_asset")
        else:
            edge = _graph_edge("project", project.id, "asset", asset.id, "contains")
        edges[edge["id"]] = edge

    conversations = db.scalars(select(Conversation).where(
        Conversation.project_id == project.id,
        or_(
            Conversation.is_project_wide.is_(True),
            exists().where(
                ConversationParticipant.conversation_id == Conversation.id,
                ConversationParticipant.user_id == request.state.user.id,
            ),
        ),
    ).order_by(Conversation.updated_at.desc()).limit(80)).all() if include("conversation") else []
    for index, conversation in enumerate(conversations):
        label = conversation.name or ("Личный диалог" if conversation.kind == "direct" else "Обсуждение")
        graph_node = _graph_node("conversation", conversation.id, label, kind="conversation", subtitle=conversation.kind, x=430 + math.cos(index * 2) * 130, y=math.sin(index * 2) * 300)
        nodes[graph_node["id"]] = graph_node
        target_type, target_id = ("content", conversation.content_item_id) if conversation.content_item_id and f"content:{conversation.content_item_id}" in nodes else ("project", project.id)
        edge = _graph_edge(target_type, target_id, "conversation", conversation.id, "discussed_in"); edges[edge["id"]] = edge

    if assets and conversations:
        visible_conversation_ids = [item.id for item in conversations]
        attachment_rows = db.execute(select(distinct(Message.conversation_id), Message.attachment_id).join(
            Conversation, Conversation.id == Message.conversation_id
        ).where(Conversation.project_id == project.id, Conversation.id.in_(visible_conversation_ids), Message.attachment_id.is_not(None), Message.deleted_at.is_(None)).limit(300)).all()
        for conversation_id, attachment_id in attachment_rows:
            if f"conversation:{conversation_id}" in nodes and f"asset:{attachment_id}" in nodes:
                edge = _graph_edge("conversation", conversation_id, "asset", attachment_id, "references"); edges[edge["id"]] = edge

    if include("review"):
        reviews = db.scalars(select(AssetReview).join(
            ContentAttachment, ContentAttachment.id == AssetReview.attachment_id
        ).where(ContentAttachment.project_id == project.id, AssetReview.status.in_(["open", "in_progress"])).order_by(AssetReview.updated_at.desc()).limit(100)).all()
        if member.role == "client":
            reviews = [review for review in reviews if review.visibility == "client"]
        for index, review in enumerate(reviews):
            graph_node = _graph_node("review", review.id, review.body[:100], kind="review", subtitle=review.annotation_type, status=review.status, x=220 + math.cos(index * 1.9) * 180, y=math.sin(index * 1.9) * 330)
            nodes[graph_node["id"]] = graph_node
            edge = _graph_edge("review", review.id, "asset", review.attachment_id, "about"); edges[edge["id"]] = edge

    if include("user"):
        members = db.execute(select(WorkspaceMember, User).join(User, User.id == WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == project.workspace_id
        )).all()
        for index, (workspace_member, user) in enumerate(members):
            graph_node = _graph_node("user", user.id, user.display_name or user.email, kind="person", subtitle=workspace_member.role, x=math.cos(index * 1.5) * 620, y=math.sin(index * 1.5) * 400)
            nodes[graph_node["id"]] = graph_node
            edge = _graph_edge("user", user.id, "project", project.id, "member_of"); edges[edge["id"]] = edge

    if include("diagram"):
        diagrams = db.scalars(select(ProjectDiagram).where(
            ProjectDiagram.project_id == project.id
        ).order_by(ProjectDiagram.updated_at.desc()).limit(50)).all()
        for index, diagram in enumerate(diagrams):
            graph_node = _graph_node("diagram", diagram.id, diagram.title, kind="diagram", subtitle=diagram.diagram_type,
                                     x=-120 + math.cos(index * 1.8) * 180, y=math.sin(index * 1.8) * 320)
            nodes[graph_node["id"]] = graph_node
            edge = _graph_edge("project", project.id, "diagram", diagram.id, "described_by"); edges[edge["id"]] = edge

    manual_links = db.scalars(select(EntityLink).where(EntityLink.project_id == project.id)).all()
    for link in manual_links:
        source = f"{link.source_type}:{link.source_id}"; target = f"{link.target_type}:{link.target_id}"
        if source in nodes and target in nodes:
            edge = _graph_edge(link.source_type, link.source_id, link.target_type, link.target_id, link.relation_type, label=link.label, manual_id=link.id, weight=link.weight); edges[edge["id"]] = edge
    return {"project_id": project.id, "nodes": list(nodes.values()), "edges": list(edges.values()), "counts": {"nodes": len(nodes), "edges": len(edges)}, "truncated": len(nodes) >= 500}


@router.post("/projects/{project_id}/entity-links", status_code=201)
def create_entity_link(project_id: str, payload: EntityLinkCreate, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    project, member = _project_access(db, project_id, request.state.user.id); _require_editor(member)
    if payload.source_type == payload.target_type and payload.source_id == payload.target_id:
        raise HTTPException(400, "Сущность нельзя связать с самой собой.")
    _validate_entity(db, project, payload.source_type, payload.source_id, request.state.user.id); _validate_entity(db, project, payload.target_type, payload.target_id, request.state.user.id)
    if payload.extra and len(json.dumps(payload.extra)) > 50_000:
        raise HTTPException(413, "Метаданные связи слишком большие.")
    link = EntityLink(project_id=project.id, source_type=payload.source_type, source_id=payload.source_id,
                      target_type=payload.target_type, target_id=payload.target_id, relation_type=payload.relation_type,
                      label=(payload.label or "").strip() or None, weight=payload.weight, extra=payload.extra,
                      created_by_user_id=request.state.user.id)
    db.add(link)
    try: db.commit()
    except Exception as exc:
        db.rollback(); raise HTTPException(409, "Такая связь уже существует.") from exc
    _event(project.id, "graph.link.created", request.state.user.id, link_id=link.id)
    return {"id": link.id, **payload.model_dump()}


@router.delete("/entity-links/{link_id}", status_code=204)
def delete_entity_link(link_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    link = db.get(EntityLink, link_id)
    if link is None: raise HTTPException(404, "Связь не найдена.")
    _, member = _project_access(db, link.project_id, request.state.user.id); _require_editor(member)
    project_id = link.project_id; db.delete(link); db.commit()
    _event(project_id, "graph.link.deleted", request.state.user.id, link_id=link_id)


def _diagram_payload(db: Session, diagram: ProjectDiagram, *, detailed: bool = False) -> dict[str, object]:
    payload = {"id": diagram.id, "project_id": diagram.project_id, "title": diagram.title,
               "description": diagram.description, "diagram_type": diagram.diagram_type,
               "viewport": diagram.viewport or {}, "created_at": diagram.created_at.isoformat(),
               "updated_at": diagram.updated_at.isoformat()}
    if not detailed: return payload
    nodes = db.scalars(select(DiagramNode).where(DiagramNode.diagram_id == diagram.id).order_by(DiagramNode.created_at)).all()
    node_by_id = {item.id: item for item in nodes}
    edges = db.scalars(select(DiagramEdge).where(DiagramEdge.diagram_id == diagram.id)).all()
    payload["nodes"] = [{"id": item.id, "key": item.node_key, "kind": item.kind, "title": item.title,
                         "description": item.description, "x": item.x, "y": item.y, "width": item.width,
                         "height": item.height, "color": item.color, "entity_type": item.entity_type,
                         "entity_id": item.entity_id, "extra": item.extra or {}} for item in nodes]
    payload["edges"] = [{"id": item.id, "source_key": node_by_id[item.source_node_id].node_key,
                         "target_key": node_by_id[item.target_node_id].node_key, "label": item.label,
                         "edge_type": item.edge_type, "extra": item.extra or {}} for item in edges
                        if item.source_node_id in node_by_id and item.target_node_id in node_by_id]
    return payload


@router.get("/projects/{project_id}/diagrams")
def list_diagrams(project_id: str, request: Request, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    _project_access(db, project_id, request.state.user.id)
    return [_diagram_payload(db, item) for item in db.scalars(select(ProjectDiagram).where(
        ProjectDiagram.project_id == project_id
    ).order_by(ProjectDiagram.updated_at.desc())).all()]


@router.post("/projects/{project_id}/diagrams", status_code=201)
def create_diagram(project_id: str, payload: DiagramCreate, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    _, member = _project_access(db, project_id, request.state.user.id); _require_editor(member)
    diagram = ProjectDiagram(project_id=project_id, title=payload.title.strip(), description=(payload.description or "").strip() or None,
                             diagram_type=payload.diagram_type, created_by_user_id=request.state.user.id)
    db.add(diagram); db.flush()
    if payload.template == "approval":
        workflow = db.scalar(select(ApprovalWorkflow).where(ApprovalWorkflow.project_id == project_id))
        stages = db.scalars(select(ApprovalStage).where(ApprovalStage.workflow_id == workflow.id).order_by(ApprovalStage.position)).all() if workflow else []
        previous = None
        for index, stage in enumerate(stages):
            item = DiagramNode(diagram_id=diagram.id, node_key=f"stage-{stage.stage_key}", kind="start" if index == 0 else "end" if index == len(stages) - 1 else "task",
                               title=stage.name, x=80 + index * 240, y=160, width=180, height=76, color=stage.color,
                               entity_type="project", entity_id=project_id)
            db.add(item); db.flush()
            if previous: db.add(DiagramEdge(diagram_id=diagram.id, source_node_id=previous.id, target_node_id=item.id, edge_type="default"))
            previous = item
    db.commit(); _event(project_id, "diagram.created", request.state.user.id, diagram_id=diagram.id)
    return _diagram_payload(db, diagram, detailed=True)


def _diagram_access(db: Session, diagram_id: str, user_id: str):
    diagram = db.get(ProjectDiagram, diagram_id)
    if diagram is None: raise HTTPException(404, "Схема не найдена.")
    _, member = _project_access(db, diagram.project_id, user_id)
    return diagram, member


@router.get("/diagrams/{diagram_id}")
def get_diagram(diagram_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    diagram, _ = _diagram_access(db, diagram_id, request.state.user.id)
    return _diagram_payload(db, diagram, detailed=True)


@router.put("/diagrams/{diagram_id}")
def save_diagram(diagram_id: str, payload: DiagramSave, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    diagram, member = _diagram_access(db, diagram_id, request.state.user.id); _require_editor(member)
    keys = [item.key for item in payload.nodes]
    if len(keys) != len(set(keys)): raise HTTPException(400, "Ключи узлов должны быть уникальны.")
    if any(edge.source_key not in keys or edge.target_key not in keys or edge.source_key == edge.target_key for edge in payload.edges):
        raise HTTPException(400, "Ребро ссылается на отсутствующий узел или замыкается на себя.")
    if len(json.dumps(payload.model_dump())) > 2_000_000: raise HTTPException(413, "Схема слишком большая.")
    if len(json.dumps(payload.viewport or {})) > 50_000: raise HTTPException(413, "Состояние рабочей области слишком большое.")
    diagram.title = payload.title.strip(); diagram.description = (payload.description or "").strip() or None
    diagram.diagram_type = payload.diagram_type; diagram.viewport = payload.viewport
    db.execute(delete(DiagramEdge).where(DiagramEdge.diagram_id == diagram.id))
    db.execute(delete(DiagramNode).where(DiagramNode.diagram_id == diagram.id)); db.flush()
    stored: dict[str, DiagramNode] = {}
    for item in payload.nodes:
        if item.entity_type and item.entity_id:
            project = db.get(Project, diagram.project_id); _validate_entity(db, project, item.entity_type, item.entity_id, request.state.user.id)
        created = DiagramNode(diagram_id=diagram.id, node_key=item.key, kind=item.kind, title=item.title.strip(),
                              description=(item.description or "").strip() or None, x=item.x, y=item.y,
                              width=item.width, height=item.height, color=item.color, entity_type=item.entity_type,
                              entity_id=item.entity_id, extra=item.extra)
        db.add(created); db.flush(); stored[item.key] = created
    for item in payload.edges:
        db.add(DiagramEdge(diagram_id=diagram.id, source_node_id=stored[item.source_key].id,
                           target_node_id=stored[item.target_key].id, label=(item.label or "").strip() or None,
                           edge_type=item.edge_type, extra=item.extra))
    db.commit(); _event(diagram.project_id, "diagram.updated", request.state.user.id, diagram_id=diagram.id)
    return _diagram_payload(db, diagram, detailed=True)


@router.delete("/diagrams/{diagram_id}", status_code=204)
def delete_diagram(diagram_id: str, request: Request, db: Session = Depends(get_db)) -> None:
    diagram, member = _diagram_access(db, diagram_id, request.state.user.id); _require_editor(member)
    project_id = diagram.project_id; db.delete(diagram); db.commit()
    _event(project_id, "diagram.deleted", request.state.user.id, diagram_id=diagram_id)
