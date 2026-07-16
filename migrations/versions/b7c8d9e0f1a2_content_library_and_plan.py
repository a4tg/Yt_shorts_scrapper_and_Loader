"""Add project content library, revisions, and attachments.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-15 20:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("stage_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("item_type", sa.String(length=24), server_default="post", nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("channel", sa.String(length=80), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("priority", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "item_type IN ('post', 'video', 'banner', 'document', 'campaign', 'note')",
            name="ck_content_items_type",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_content_items_priority",
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_content_items_status"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["approval_stages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "project_id", "stage_id", "item_type", "channel", "priority", "status",
        "planned_at", "due_at", "assignee_user_id", "created_by_user_id",
    ):
        op.create_index(op.f(f"ix_content_items_{column}"), "content_items", [column])

    op.create_table(
        "content_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("content_item_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("changed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_item_id", "version_number", name="uq_content_revisions_version"),
    )
    op.create_index(op.f("ix_content_revisions_content_item_id"), "content_revisions", ["content_item_id"])
    op.create_index(op.f("ix_content_revisions_changed_by_user_id"), "content_revisions", ["changed_by_user_id"])
    op.create_index(op.f("ix_content_revisions_created_at"), "content_revisions", ["created_at"])

    op.create_table(
        "content_attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("content_item_id", sa.String(length=36), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=1000), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path"),
    )
    op.create_index(op.f("ix_content_attachments_content_item_id"), "content_attachments", ["content_item_id"])
    op.create_index(op.f("ix_content_attachments_uploaded_by_user_id"), "content_attachments", ["uploaded_by_user_id"])
    op.create_index(op.f("ix_content_attachments_created_at"), "content_attachments", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_content_attachments_created_at"), table_name="content_attachments")
    op.drop_index(op.f("ix_content_attachments_uploaded_by_user_id"), table_name="content_attachments")
    op.drop_index(op.f("ix_content_attachments_content_item_id"), table_name="content_attachments")
    op.drop_table("content_attachments")
    op.drop_index(op.f("ix_content_revisions_created_at"), table_name="content_revisions")
    op.drop_index(op.f("ix_content_revisions_changed_by_user_id"), table_name="content_revisions")
    op.drop_index(op.f("ix_content_revisions_content_item_id"), table_name="content_revisions")
    op.drop_table("content_revisions")
    for column in reversed((
        "project_id", "stage_id", "item_type", "channel", "priority", "status",
        "planned_at", "due_at", "assignee_user_id", "created_by_user_id",
    )):
        op.drop_index(op.f(f"ix_content_items_{column}"), table_name="content_items")
    op.drop_table("content_items")
