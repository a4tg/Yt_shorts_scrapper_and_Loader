"""add project diagram visibility

Revision ID: l7a8b9c0d1e2
Revises: k6f7a8b9c0d1
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "l7a8b9c0d1e2"
down_revision = "k6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project_diagrams") as batch_op:
        batch_op.add_column(
            sa.Column(
                "visibility",
                sa.String(length=16),
                nullable=False,
                server_default="team",
            )
        )
        batch_op.create_check_constraint(
            "ck_project_diagrams_visibility",
            "visibility IN ('team', 'client')",
        )
        batch_op.create_index("ix_project_diagrams_visibility", ["visibility"])


def downgrade() -> None:
    with op.batch_alter_table("project_diagrams") as batch_op:
        batch_op.drop_index("ix_project_diagrams_visibility")
        batch_op.drop_constraint("ck_project_diagrams_visibility", type_="check")
        batch_op.drop_column("visibility")
