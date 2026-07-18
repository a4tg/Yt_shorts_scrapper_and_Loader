"""document comments and review threads

Revision ID: r3g4h5i6j7k8
Revises: q2f3g4h5i6j7
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa


revision = "r3g4h5i6j7k8"
down_revision = "q2f3g4h5i6j7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("content_item_id", sa.String(36), nullable=False),
        sa.Column("parent_id", sa.String(36)),
        sa.Column("author_user_id", sa.String(36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("quoted_text", sa.String(1000)),
        sa.Column("start_offset", sa.Integer()),
        sa.Column("end_offset", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_by_user_id", sa.String(36)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_document_comments_status",
        ),
        sa.CheckConstraint(
            "(start_offset IS NULL AND end_offset IS NULL) OR "
            "(start_offset >= 0 AND end_offset > start_offset)",
            name="ck_document_comments_selection",
        ),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["document_comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    for column in (
        "content_item_id", "parent_id", "author_user_id", "status",
        "resolved_by_user_id", "created_at", "updated_at",
    ):
        op.create_index(f"ix_document_comments_{column}", "document_comments", [column])


def downgrade() -> None:
    op.drop_table("document_comments")
