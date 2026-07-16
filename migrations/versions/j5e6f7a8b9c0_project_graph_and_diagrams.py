"""project graph and diagrams

Revision ID: j5e6f7a8b9c0
Revises: i4d5e6f7a8b9
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "j5e6f7a8b9c0"
down_revision = "i4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_links",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False), sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False), sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("relation_type", sa.String(32), nullable=False), sa.Column("label", sa.String(160)),
        sa.Column("weight", sa.Float(), nullable=False), sa.Column("extra", sa.JSON()),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("relation_type IN ('relates_to', 'depends_on', 'blocks', 'produces', 'references', 'assigned_to', 'custom')", name="ck_entity_links_relation_type"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "source_type", "source_id", "target_type", "target_id", "relation_type", name="uq_entity_links_relation"),
    )
    for column in ("project_id", "source_type", "source_id", "target_type", "target_id", "relation_type", "created_by_user_id"):
        op.create_index(f"ix_entity_links_{column}", "entity_links", [column])

    op.create_table(
        "project_diagrams",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(160), nullable=False), sa.Column("description", sa.String(1000)),
        sa.Column("diagram_type", sa.String(24), nullable=False), sa.Column("viewport", sa.JSON()),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("diagram_type IN ('process', 'flowchart', 'mind_map')", name="ck_project_diagrams_type"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    for column in ("project_id", "diagram_type", "created_by_user_id"):
        op.create_index(f"ix_project_diagrams_{column}", "project_diagrams", [column])

    op.create_table(
        "diagram_nodes",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("diagram_id", sa.String(36), nullable=False),
        sa.Column("node_key", sa.String(80), nullable=False), sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("title", sa.String(240), nullable=False), sa.Column("description", sa.String(2000)),
        sa.Column("x", sa.Float(), nullable=False), sa.Column("y", sa.Float(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False), sa.Column("height", sa.Float(), nullable=False),
        sa.Column("color", sa.String(16)), sa.Column("entity_type", sa.String(32)), sa.Column("entity_id", sa.String(36)),
        sa.Column("extra", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('start', 'end', 'task', 'decision', 'document', 'asset', 'person', 'note')", name="ck_diagram_nodes_kind"),
        sa.ForeignKeyConstraint(["diagram_id"], ["project_diagrams.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("diagram_id", "node_key", name="uq_diagram_nodes_key"),
    )
    for column in ("diagram_id", "kind", "entity_type", "entity_id"):
        op.create_index(f"ix_diagram_nodes_{column}", "diagram_nodes", [column])

    op.create_table(
        "diagram_edges",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("diagram_id", sa.String(36), nullable=False),
        sa.Column("source_node_id", sa.String(36), nullable=False), sa.Column("target_node_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(160)), sa.Column("edge_type", sa.String(24), nullable=False), sa.Column("extra", sa.JSON()),
        sa.CheckConstraint("edge_type IN ('default', 'success', 'failure', 'conditional')", name="ck_diagram_edges_type"),
        sa.ForeignKeyConstraint(["diagram_id"], ["project_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_node_id"], ["diagram_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_node_id"], ["diagram_nodes.id"], ondelete="CASCADE"),
    )
    for column in ("diagram_id", "source_node_id", "target_node_id", "edge_type"):
        op.create_index(f"ix_diagram_edges_{column}", "diagram_edges", [column])


def downgrade() -> None:
    op.drop_table("diagram_edges")
    op.drop_table("diagram_nodes")
    op.drop_table("project_diagrams")
    op.drop_table("entity_links")
