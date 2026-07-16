"""decision intelligence

Revision ID: k6f7a8b9c0d1
Revises: j5e6f7a8b9c0
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "k6f7a8b9c0d1"
down_revision = "j5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_insights",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False), sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False), sa.Column("source_id", sa.String(36)),
        sa.Column("source_excerpt", sa.String(1000)), sa.Column("assignee_user_id", sa.String(36)),
        sa.Column("due_at", sa.DateTime(timezone=True)), sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("impact_score", sa.Float(), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("extra", sa.JSON()), sa.Column("created_by_user_id", sa.String(36)),
        sa.Column("completed_by_user_id", sa.String(36)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('decision', 'commitment', 'action', 'risk', 'question')", name="ck_project_insights_kind"),
        sa.CheckConstraint("status IN ('open', 'in_progress', 'done', 'dismissed')", name="ck_project_insights_status"),
        sa.CheckConstraint("priority IN ('low', 'normal', 'high', 'urgent')", name="ck_project_insights_priority"),
        sa.CheckConstraint("visibility IN ('team', 'client')", name="ck_project_insights_visibility"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["completed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "fingerprint", name="uq_project_insights_fingerprint"),
    )
    for column in ("project_id", "kind", "status", "priority", "visibility", "source_type", "source_id", "assignee_user_id", "due_at", "impact_score", "created_by_user_id", "completed_by_user_id", "completed_at"):
        op.create_index(f"ix_project_insights_{column}", "project_insights", [column])

    op.create_table(
        "insight_links",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("insight_id", sa.String(36), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False), sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("relation_type", sa.String(24), nullable=False), sa.Column("weight", sa.Float(), nullable=False),
        sa.CheckConstraint("relation_type IN ('derived_from', 'impacts', 'depends_on', 'resolves')", name="ck_insight_links_relation_type"),
        sa.ForeignKeyConstraint(["insight_id"], ["project_insights.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("insight_id", "entity_type", "entity_id", "relation_type", name="uq_insight_links_relation"),
    )
    for column in ("insight_id", "entity_type", "entity_id", "relation_type"):
        op.create_index(f"ix_insight_links_{column}", "insight_links", [column])

    op.create_table(
        "project_briefings",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False), sa.Column("highlights", sa.JSON()),
        sa.Column("risks", sa.JSON()), sa.Column("next_actions", sa.JSON()),
        sa.Column("visibility", sa.String(16), nullable=False), sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120)), sa.Column("source_stats", sa.JSON()),
        sa.Column("generated_by_user_id", sa.String(36), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("visibility IN ('team', 'client')", name="ck_project_briefings_visibility"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_by_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    for column in ("project_id", "visibility", "generated_by_user_id", "generated_at"):
        op.create_index(f"ix_project_briefings_{column}", "project_briefings", [column])


def downgrade() -> None:
    op.drop_table("project_briefings")
    op.drop_table("insight_links")
    op.drop_table("project_insights")
