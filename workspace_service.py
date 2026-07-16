import re
import unicodedata
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saas_models import (
    ApprovalStage,
    ApprovalWorkflow,
    Project,
    User,
    Workspace,
    WorkspaceMember,
)


DEFAULT_APPROVAL_STAGES = (
    ("idea", "Идея", "#64748b", None, False),
    ("draft", "Подготовка", "#3b82f6", "editor", False),
    ("review", "На согласовании", "#f59e0b", "admin", False),
    ("approved", "Согласовано", "#22c55e", "admin", False),
    ("published", "Опубликовано", "#8b5cf6", "editor", True),
)

ROLE_RANK = {"client": 10, "viewer": 20, "editor": 30, "admin": 40, "owner": 50}


def _slug_base(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    return (slug or fallback)[:80]


def _unique_workspace_slug(db: Session, name: str) -> str:
    base = _slug_base(name, "workspace")
    candidate = base
    while db.scalar(select(Workspace.id).where(Workspace.slug == candidate)):
        candidate = f"{base[:70]}-{uuid.uuid4().hex[:8]}"
    return candidate


def _unique_project_slug(db: Session, workspace_id: str, name: str) -> str:
    base = _slug_base(name, "project")
    candidate = base
    while db.scalar(
        select(Project.id).where(Project.workspace_id == workspace_id, Project.slug == candidate)
    ):
        candidate = f"{base[:70]}-{uuid.uuid4().hex[:8]}"
    return candidate


def create_project(
    db: Session,
    workspace: Workspace,
    user: User,
    name: str,
    description: str | None = None,
    color: str = "#7c6cff",
) -> Project:
    project = Project(
        workspace_id=workspace.id,
        name=name.strip()[:120],
        slug=_unique_project_slug(db, workspace.id, name),
        description=(description or "").strip()[:1000] or None,
        color=color,
        created_by_user_id=user.id,
    )
    db.add(project)
    db.flush()
    workflow = ApprovalWorkflow(project_id=project.id, name="Основной процесс")
    db.add(workflow)
    db.flush()
    db.add_all(
        [
            ApprovalStage(
                workflow_id=workflow.id,
                stage_key=key,
                name=label,
                position=position,
                color=stage_color,
                required_role=required_role,
                is_terminal=is_terminal,
            )
            for position, (key, label, stage_color, required_role, is_terminal)
            in enumerate(DEFAULT_APPROVAL_STAGES)
        ]
    )
    return project


def create_personal_workspace(db: Session, user: User) -> Workspace:
    existing = db.scalar(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .limit(1)
    )
    if existing is not None:
        return existing
    display = (user.display_name or user.email.split("@", 1)[0]).strip()
    workspace = Workspace(
        name=f"{display} — рабочее пространство"[:120],
        slug=_unique_workspace_slug(db, display),
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    create_project(db, workspace, user, "Первый проект")
    return workspace


def create_workspace(db: Session, user: User, name: str) -> Workspace:
    workspace = Workspace(
        name=name.strip()[:120],
        slug=_unique_workspace_slug(db, name),
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    create_project(db, workspace, user, "Первый проект")
    return workspace


def membership_for(db: Session, workspace_id: str, user_id: str) -> WorkspaceMember | None:
    return db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )


def project_membership(db: Session, project_id: str, user_id: str) -> tuple[Project, WorkspaceMember] | None:
    row = db.execute(
        select(Project, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Project.workspace_id)
        .where(Project.id == project_id, WorkspaceMember.user_id == user_id)
    ).first()
    return (row[0], row[1]) if row else None


def has_role(member: WorkspaceMember, minimum_role: str) -> bool:
    return ROLE_RANK.get(member.role, 0) >= ROLE_RANK[minimum_role]


def workspace_payload(db: Session, workspace: Workspace, member: WorkspaceMember) -> dict[str, object]:
    project_count = db.scalar(
        select(func.count(Project.id)).where(
            Project.workspace_id == workspace.id, Project.status == "active"
        )
    ) or 0
    member_count = db.scalar(
        select(func.count(WorkspaceMember.id)).where(
            WorkspaceMember.workspace_id == workspace.id
        )
    ) or 0
    return {
        "id": workspace.id,
        "name": workspace.name,
        "slug": workspace.slug,
        "status": workspace.status,
        "role": member.role,
        "project_count": project_count,
        "member_count": member_count,
        "created_at": workspace.created_at.isoformat(),
    }

def project_payload(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "workspace_id": project.workspace_id,
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "color": project.color,
        "status": project.status,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }
