"""workspaces projects approvals

Revision ID: a6b7c8d9e0f1
Revises: 5dbe4a9120ef
Create Date: 2026-07-15 18:00:00
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, None] = "5dbe4a9120ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_workspaces_owner_user_id"), "workspaces", ["owner_user_id"])
    op.create_index(op.f("ix_workspaces_slug"), "workspaces", ["slug"])
    op.create_index(op.f("ix_workspaces_status"), "workspaces", ["status"])

    op.create_table(
        "workspace_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=24), server_default="viewer", nullable=False),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'viewer', 'client')",
            name="ck_workspace_members_role",
        ),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_workspace_user"),
    )
    op.create_index(op.f("ix_workspace_members_invited_by_user_id"), "workspace_members", ["invited_by_user_id"])
    op.create_index(op.f("ix_workspace_members_role"), "workspace_members", ["role"])
    op.create_index(op.f("ix_workspace_members_user_id"), "workspace_members", ["user_id"])
    op.create_index(op.f("ix_workspace_members_workspace_id"), "workspace_members", ["workspace_id"])

    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("color", sa.String(length=16), server_default="#7c6cff", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
    )
    op.create_index(op.f("ix_projects_created_by_user_id"), "projects", ["created_by_user_id"])
    op.create_index(op.f("ix_projects_status"), "projects", ["status"])
    op.create_index(op.f("ix_projects_workspace_id"), "projects", ["workspace_id"])

    # Media jobs existed before teams and projects. Nullable references keep
    # old jobs readable while all newly created work becomes project-scoped.
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("project_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_jobs_workspace_id_workspaces", "workspaces", ["workspace_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_jobs_project_id_projects", "projects", ["project_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index(op.f("ix_jobs_workspace_id"), ["workspace_id"])
        batch_op.create_index(op.f("ix_jobs_project_id"), ["project_id"])

    op.create_table(
        "approval_workflows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), server_default="Основной процесс", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index(op.f("ix_approval_workflows_project_id"), "approval_workflows", ["project_id"], unique=True)

    op.create_table(
        "approval_stages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("stage_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(length=16), server_default="#7c6cff", nullable=False),
        sa.Column("required_role", sa.String(length=24), nullable=True),
        sa.Column("is_terminal", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["approval_workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "position", name="uq_approval_stages_position"),
        sa.UniqueConstraint("workflow_id", "stage_key", name="uq_approval_stages_key"),
    )
    op.create_index(op.f("ix_approval_stages_workflow_id"), "approval_stages", ["workflow_id"])

    connection = op.get_bind()
    users = connection.execute(sa.text("SELECT id, email, display_name FROM users")).mappings().all()
    default_stages = (
        ("idea", "Идея", "#64748b", None, False),
        ("draft", "Подготовка", "#3b82f6", "editor", False),
        ("review", "На согласовании", "#f59e0b", "admin", False),
        ("approved", "Согласовано", "#22c55e", "admin", False),
        ("published", "Опубликовано", "#8b5cf6", "editor", True),
    )
    for user in users:
        workspace_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        workflow_id = str(uuid.uuid4())
        display = (user["display_name"] or user["email"].split("@", 1)[0])[:80]
        connection.execute(
            sa.text(
                "INSERT INTO workspaces (id, name, slug, owner_user_id, status) "
                "VALUES (:id, :name, :slug, :user_id, 'active')"
            ),
            {"id": workspace_id, "name": f"{display} — рабочее пространство", "slug": f"space-{user['id'][:8]}", "user_id": user["id"]},
        )
        connection.execute(
            sa.text(
                "INSERT INTO workspace_members (id, workspace_id, user_id, role) "
                "VALUES (:id, :workspace_id, :user_id, 'owner')"
            ),
            {"id": str(uuid.uuid4()), "workspace_id": workspace_id, "user_id": user["id"]},
        )
        connection.execute(
            sa.text(
                "INSERT INTO projects (id, workspace_id, name, slug, color, status, created_by_user_id) "
                "VALUES (:id, :workspace_id, 'Первый проект', 'first-project', '#7c6cff', 'active', :user_id)"
            ),
            {"id": project_id, "workspace_id": workspace_id, "user_id": user["id"]},
        )
        connection.execute(
            sa.text(
                "INSERT INTO approval_workflows (id, project_id, name) "
                "VALUES (:id, :project_id, 'Основной процесс')"
            ),
            {"id": workflow_id, "project_id": project_id},
        )
        for position, (key, name, color, role, terminal) in enumerate(default_stages):
            connection.execute(
                sa.text(
                    "INSERT INTO approval_stages "
                    "(id, workflow_id, stage_key, name, position, color, required_role, is_terminal) "
                    "VALUES (:id, :workflow_id, :stage_key, :name, :position, :color, :role, :terminal)"
                ),
                {"id": str(uuid.uuid4()), "workflow_id": workflow_id, "stage_key": key, "name": name, "position": position, "color": color, "role": role, "terminal": terminal},
            )


def downgrade() -> None:
    op.drop_index(op.f("ix_approval_stages_workflow_id"), table_name="approval_stages")
    op.drop_table("approval_stages")
    op.drop_index(op.f("ix_approval_workflows_project_id"), table_name="approval_workflows")
    op.drop_table("approval_workflows")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index(op.f("ix_jobs_project_id"))
        batch_op.drop_index(op.f("ix_jobs_workspace_id"))
        batch_op.drop_constraint("fk_jobs_project_id_projects", type_="foreignkey")
        batch_op.drop_constraint("fk_jobs_workspace_id_workspaces", type_="foreignkey")
        batch_op.drop_column("project_id")
        batch_op.drop_column("workspace_id")
    op.drop_index(op.f("ix_projects_workspace_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_status"), table_name="projects")
    op.drop_index(op.f("ix_projects_created_by_user_id"), table_name="projects")
    op.drop_table("projects")
    op.drop_index(op.f("ix_workspace_members_workspace_id"), table_name="workspace_members")
    op.drop_index(op.f("ix_workspace_members_user_id"), table_name="workspace_members")
    op.drop_index(op.f("ix_workspace_members_role"), table_name="workspace_members")
    op.drop_index(op.f("ix_workspace_members_invited_by_user_id"), table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index(op.f("ix_workspaces_status"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_slug"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_owner_user_id"), table_name="workspaces")
    op.drop_table("workspaces")
