"""asset version and review workflow

Revision ID: i4d5e6f7a8b9
Revises: h3c4d5e6f7a8
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "i4d5e6f7a8b9"
down_revision = "h3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("content_attachments") as batch:
        batch.add_column(sa.Column("asset_key", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("version_number", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("version_label", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("version_notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("supersedes_attachment_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.execute("UPDATE content_attachments SET asset_key = id WHERE asset_key IS NULL")
    with op.batch_alter_table("content_attachments") as batch:
        batch.alter_column("asset_key", existing_type=sa.String(length=36), nullable=False)
        batch.create_foreign_key(
            "fk_content_attachments_supersedes", "content_attachments",
            ["supersedes_attachment_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_content_attachment_asset_version", ["asset_key", "version_number"]
        )
        batch.create_index("ix_content_attachments_asset_key", ["asset_key"])
        batch.create_index("ix_content_attachments_supersedes_attachment_id", ["supersedes_attachment_id"])
        batch.create_index("ix_content_attachments_is_current", ["is_current"])

    op.create_table(
        "asset_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("attachment_id", sa.String(length=36), nullable=False),
        sa.Column("parent_review_id", sa.String(length=36), nullable=True),
        sa.Column("author_user_id", sa.String(length=36), nullable=False),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("annotation_type", sa.String(length=24), nullable=False),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("width", sa.Float(), nullable=True),
        sa.Column("height", sa.Float(), nullable=True),
        sa.Column("time_seconds", sa.Float(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("annotation_data", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("resolved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "annotation_type IN ('general', 'point', 'region', 'timestamp', 'page', 'drawing')",
            name="ck_asset_reviews_annotation_type",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'wont_fix')",
            name="ck_asset_reviews_status",
        ),
        sa.CheckConstraint("visibility IN ('team', 'client')", name="ck_asset_reviews_visibility"),
        sa.ForeignKeyConstraint(["attachment_id"], ["content_attachments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_review_id"], ["asset_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("attachment_id", "parent_review_id", "author_user_id", "assignee_user_id", "status", "visibility", "resolved_by_user_id", "resolved_at"):
        op.create_index(f"ix_asset_reviews_{column}", "asset_reviews", [column])

    op.create_table(
        "asset_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("attachment_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("decision IN ('approved', 'changes_requested')", name="ck_asset_approvals_decision"),
        sa.ForeignKeyConstraint(["attachment_id"], ["content_attachments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attachment_id", "user_id", name="uq_asset_approval_user"),
    )
    for column in ("attachment_id", "user_id", "decision", "decided_at"):
        op.create_index(f"ix_asset_approvals_{column}", "asset_approvals", [column])


def downgrade() -> None:
    op.drop_table("asset_approvals")
    op.drop_table("asset_reviews")
    with op.batch_alter_table("content_attachments") as batch:
        batch.drop_index("ix_content_attachments_is_current")
        batch.drop_index("ix_content_attachments_supersedes_attachment_id")
        batch.drop_index("ix_content_attachments_asset_key")
        batch.drop_constraint("uq_content_attachment_asset_version", type_="unique")
        batch.drop_constraint("fk_content_attachments_supersedes", type_="foreignkey")
        batch.drop_column("is_current")
        batch.drop_column("supersedes_attachment_id")
        batch.drop_column("version_notes")
        batch.drop_column("version_label")
        batch.drop_column("version_number")
        batch.drop_column("asset_key")
