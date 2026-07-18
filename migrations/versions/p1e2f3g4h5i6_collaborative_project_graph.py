"""collaborative project graph state

Revision ID: p1e2f3g4h5i6
Revises: o0d1e2f3g4h5
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa


revision = "p1e2f3g4h5i6"
down_revision = "o0d1e2f3g4h5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_graph_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("viewport", sa.JSON()),
        sa.Column("positions", sa.JSON()),
        sa.Column("custom_nodes", sa.JSON()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", name="uq_project_graph_states_project_id"),
    )
    op.create_index("ix_project_graph_states_project_id", "project_graph_states", ["project_id"])
    op.create_index("ix_project_graph_states_updated_by_user_id", "project_graph_states", ["updated_by_user_id"])

    op.create_table(
        "project_graph_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("changed_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "revision", name="uq_project_graph_revisions_number"),
    )
    op.create_index("ix_project_graph_revisions_project_id", "project_graph_revisions", ["project_id"])
    op.create_index("ix_project_graph_revisions_changed_by_user_id", "project_graph_revisions", ["changed_by_user_id"])
    op.create_index("ix_project_graph_revisions_created_at", "project_graph_revisions", ["created_at"])


def downgrade() -> None:
    op.drop_table("project_graph_revisions")
    op.drop_table("project_graph_states")
