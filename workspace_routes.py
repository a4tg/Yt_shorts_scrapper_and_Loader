import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth_service import normalize_email
from billing_service import require_plan_capacity
from database import get_db
from saas_models import ApprovalStage, ApprovalWorkflow, Project, User, Workspace, WorkspaceMember
from workspace_service import (
    create_project,
    create_workspace,
    has_role,
    membership_for,
    project_membership,
    project_payload,
    workspace_payload,
)


router = APIRouter(prefix="/api", tags=["workspaces"])
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
MemberRole = Literal["admin", "editor", "viewer", "client"]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    color: str = "#7c6cff"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = None
    status: Literal["active", "archived"] | None = None


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: MemberRole


class MemberUpdate(BaseModel):
    role: MemberRole


class ApprovalStageInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str = "#7c6cff"
    required_role: Literal["admin", "editor", "viewer", "client"] | None = None
    is_terminal: bool = False


class ApprovalWorkflowUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    stages: list[ApprovalStageInput] = Field(min_length=2, max_length=20)


def _workspace_access(db: Session, workspace_id: str, user_id: str) -> tuple[Workspace, WorkspaceMember]:
    workspace = db.get(Workspace, workspace_id)
    member = membership_for(db, workspace_id, user_id)
    if workspace is None or workspace.status != "active" or member is None:
        raise HTTPException(404, "Рабочее пространство не найдено.")
    return workspace, member


def _require_role(member: WorkspaceMember, role: str) -> None:
    if not has_role(member, role):
        raise HTTPException(403, "Недостаточно прав для этой операции.")


def _valid_color(value: str) -> str:
    if not COLOR_PATTERN.fullmatch(value):
        raise HTTPException(400, "Цвет должен быть указан в формате #RRGGBB.")
    return value.lower()


@router.get("/workspaces")
def list_workspaces(request: Request, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == request.state.user.id, Workspace.status == "active")
        .order_by(Workspace.created_at)
    ).all()
    return [workspace_payload(db, workspace, member) for workspace, member in rows]


@router.post("/workspaces", status_code=201)
def add_workspace(
    payload: WorkspaceCreate, request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    current = db.scalar(select(func.count(Workspace.id)).where(Workspace.owner_user_id == request.state.user.id, Workspace.status == "active")) or 0
    require_plan_capacity(db, request.state.user.id, "workspaces", int(current))
    workspace = create_workspace(db, request.state.user, payload.name)
    db.commit()
    member = membership_for(db, workspace.id, request.state.user.id)
    return workspace_payload(db, workspace, member)


@router.patch("/workspaces/{workspace_id}")
def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    workspace, member = _workspace_access(db, workspace_id, request.state.user.id)
    _require_role(member, "admin")
    workspace.name = payload.name.strip()
    db.commit()
    return workspace_payload(db, workspace, member)


@router.get("/workspaces/{workspace_id}/projects")
def list_projects(
    workspace_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    _workspace_access(db, workspace_id, request.state.user.id)
    projects = db.scalars(
        select(Project)
        .where(Project.workspace_id == workspace_id)
        .order_by(Project.status, Project.updated_at.desc())
    ).all()
    return [project_payload(project) for project in projects]


@router.post("/workspaces/{workspace_id}/projects", status_code=201)
def add_project(
    workspace_id: str,
    payload: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    workspace, member = _workspace_access(db, workspace_id, request.state.user.id)
    _require_role(member, "editor")
    current = db.scalar(select(func.count(Project.id)).where(Project.workspace_id == workspace_id, Project.status == "active")) or 0
    require_plan_capacity(db, request.state.user.id, "projects", int(current))
    project = create_project(
        db, workspace, request.state.user, payload.name, payload.description, _valid_color(payload.color)
    )
    db.commit()
    return project_payload(project)


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    access = project_membership(db, project_id, request.state.user.id)
    if access is None:
        raise HTTPException(404, "Проект не найден.")
    project, member = access
    _require_role(member, "editor")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        project.name = values["name"].strip()
    if "description" in values:
        project.description = (values["description"] or "").strip() or None
    if "color" in values:
        project.color = _valid_color(values["color"])
    if "status" in values:
        project.status = values["status"]
    db.commit()
    return project_payload(project)


@router.get("/workspaces/{workspace_id}/members")
def list_members(
    workspace_id: str, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    _workspace_access(db, workspace_id, request.state.user.id)
    rows = db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at)
    ).all()
    return [
        {
            "id": member.id,
            "user_id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": member.role,
            "joined_at": member.joined_at.isoformat(),
        }
        for member, user in rows
    ]


@router.post("/workspaces/{workspace_id}/members", status_code=201)
def add_member(
    workspace_id: str,
    payload: MemberCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _, acting_member = _workspace_access(db, workspace_id, request.state.user.id)
    _require_role(acting_member, "admin")
    current = db.scalar(select(func.count(WorkspaceMember.id)).where(WorkspaceMember.workspace_id == workspace_id)) or 0
    require_plan_capacity(db, request.state.user.id, "members", int(current))
    try:
        email = normalize_email(payload.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if user is None:
        raise HTTPException(404, "Сначала пользователь должен зарегистрироваться в сервисе.")
    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user.id,
        role=payload.role,
        invited_by_user_id=request.state.user.id,
    )
    db.add(member)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Пользователь уже состоит в рабочем пространстве.") from exc
    return {
        "id": member.id,
        "user_id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": member.role,
        "joined_at": member.joined_at.isoformat(),
    }


@router.patch("/workspaces/{workspace_id}/members/{member_id}")
def update_member(
    workspace_id: str,
    member_id: str,
    payload: MemberUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _, acting_member = _workspace_access(db, workspace_id, request.state.user.id)
    _require_role(acting_member, "admin")
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.id == member_id, WorkspaceMember.workspace_id == workspace_id
        )
    )
    if member is None:
        raise HTTPException(404, "Участник не найден.")
    if member.role == "owner":
        raise HTTPException(409, "Роль владельца нельзя изменить.")
    member.role = payload.role
    db.commit()
    return {"status": "ok", "role": member.role}


@router.delete("/workspaces/{workspace_id}/members/{member_id}", status_code=204)
def remove_member(
    workspace_id: str, member_id: str, request: Request, db: Session = Depends(get_db)
) -> None:
    _, acting_member = _workspace_access(db, workspace_id, request.state.user.id)
    _require_role(acting_member, "admin")
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.id == member_id, WorkspaceMember.workspace_id == workspace_id
        )
    )
    if member is None:
        raise HTTPException(404, "Участник не найден.")
    if member.role == "owner":
        raise HTTPException(409, "Владельца нельзя удалить из рабочего пространства.")
    db.delete(member)
    db.commit()


def _workflow_payload(db: Session, project: Project, workflow: ApprovalWorkflow) -> dict[str, object]:
    stages = db.scalars(
        select(ApprovalStage)
        .where(ApprovalStage.workflow_id == workflow.id)
        .order_by(ApprovalStage.position)
    ).all()
    return {
        "id": workflow.id,
        "project_id": project.id,
        "name": workflow.name,
        "stages": [
            {
                "id": stage.id,
                "key": stage.stage_key,
                "name": stage.name,
                "position": stage.position,
                "color": stage.color,
                "required_role": stage.required_role,
                "is_terminal": stage.is_terminal,
            }
            for stage in stages
        ],
    }


@router.get("/projects/{project_id}/approval-workflow")
def get_approval_workflow(
    project_id: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    access = project_membership(db, project_id, request.state.user.id)
    if access is None:
        raise HTTPException(404, "Проект не найден.")
    project, _ = access
    workflow = db.scalar(select(ApprovalWorkflow).where(ApprovalWorkflow.project_id == project.id))
    if workflow is None:
        raise HTTPException(404, "Процесс согласования не найден.")
    return _workflow_payload(db, project, workflow)


@router.put("/projects/{project_id}/approval-workflow")
def update_approval_workflow(
    project_id: str,
    payload: ApprovalWorkflowUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    access = project_membership(db, project_id, request.state.user.id)
    if access is None:
        raise HTTPException(404, "Проект не найден.")
    project, member = access
    _require_role(member, "editor")
    workflow = db.scalar(select(ApprovalWorkflow).where(ApprovalWorkflow.project_id == project.id))
    if workflow is None:
        workflow = ApprovalWorkflow(project_id=project.id, name=payload.name.strip())
        db.add(workflow)
        db.flush()
    workflow.name = payload.name.strip()
    db.execute(delete(ApprovalStage).where(ApprovalStage.workflow_id == workflow.id))
    for position, stage in enumerate(payload.stages):
        stage_key = f"stage-{position + 1}"
        db.add(
            ApprovalStage(
                workflow_id=workflow.id,
                stage_key=stage_key,
                name=stage.name.strip(),
                position=position,
                color=_valid_color(stage.color),
                required_role=stage.required_role,
                is_terminal=stage.is_terminal,
            )
        )
    db.commit()
    return _workflow_payload(db, project, workflow)
