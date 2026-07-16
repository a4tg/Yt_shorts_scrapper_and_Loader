"""project file workspace

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_folders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["project_folders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_folders_project_id", "project_folders", ["project_id"])
    op.create_index("ix_project_folders_parent_id", "project_folders", ["parent_id"])
    op.create_index("ix_project_folders_created_by_user_id", "project_folders", ["created_by_user_id"])

    op.add_column("content_attachments", sa.Column("project_id", sa.String(length=36), nullable=True))
    op.add_column("content_attachments", sa.Column("folder_id", sa.String(length=36), nullable=True))
    op.add_column(
        "content_attachments",
        sa.Column("source_type", sa.String(length=24), server_default="upload", nullable=False),
    )
    op.execute(
        "UPDATE content_attachments SET project_id = "
        "(SELECT project_id FROM content_items WHERE content_items.id = content_attachments.content_item_id)"
    )
    with op.batch_alter_table("content_attachments") as batch:
        batch.alter_column("project_id", existing_type=sa.String(length=36), nullable=False)
        batch.alter_column("content_item_id", existing_type=sa.String(length=36), nullable=True)
        batch.create_foreign_key(
            "fk_content_attachments_project_id", "projects", ["project_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            "fk_content_attachments_folder_id", "project_folders", ["folder_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_content_attachments_project_id", ["project_id"])
        batch.create_index("ix_content_attachments_folder_id", ["folder_id"])
        batch.create_index("ix_content_attachments_source_type", ["source_type"])


def downgrade() -> None:
    op.execute("DELETE FROM content_attachments WHERE content_item_id IS NULL")
    with op.batch_alter_table("content_attachments") as batch:
        batch.drop_index("ix_content_attachments_source_type")
        batch.drop_index("ix_content_attachments_folder_id")
        batch.drop_index("ix_content_attachments_project_id")
        batch.drop_constraint("fk_content_attachments_folder_id", type_="foreignkey")
        batch.drop_constraint("fk_content_attachments_project_id", type_="foreignkey")
        batch.alter_column("content_item_id", existing_type=sa.String(length=36), nullable=False)
        batch.drop_column("source_type")
        batch.drop_column("folder_id")
        batch.drop_column("project_id")
    op.drop_index("ix_project_folders_created_by_user_id", table_name="project_folders")
    op.drop_index("ix_project_folders_parent_id", table_name="project_folders")
    op.drop_index("ix_project_folders_project_id", table_name="project_folders")
    op.drop_table("project_folders")
