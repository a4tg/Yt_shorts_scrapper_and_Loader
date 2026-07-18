"""approval queue and event history

Revision ID: q2f3g4h5i6j7
Revises: p1e2f3g4h5i6
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa


revision = "q2f3g4h5i6j7"
down_revision = "p1e2f3g4h5i6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("attachment_id", sa.String(36), nullable=False),
        sa.Column("stage_id", sa.String(36)),
        sa.Column("requested_by_user_id", sa.String(36), nullable=False),
        sa.Column("assignee_user_id", sa.String(36)),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="team"),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'changes_requested', 'cancelled')",
            name="ck_approval_requests_status",
        ),
        sa.CheckConstraint("visibility IN ('team', 'client')", name="ck_approval_requests_visibility"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attachment_id"], ["content_attachments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["approval_stages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("attachment_id", name="uq_approval_requests_attachment"),
    )
    for column in (
        "project_id", "attachment_id", "stage_id", "requested_by_user_id",
        "assignee_user_id", "due_at", "status", "visibility",
    ):
        op.create_index(f"ix_approval_requests_{column}", "approval_requests", [column])

    op.create_table(
        "approval_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("approval_request_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    for column in ("approval_request_id", "event_type", "actor_user_id", "created_at"):
        op.create_index(f"ix_approval_events_{column}", "approval_events", [column])


def downgrade() -> None:
    op.drop_table("approval_events")
    op.drop_table("approval_requests")
