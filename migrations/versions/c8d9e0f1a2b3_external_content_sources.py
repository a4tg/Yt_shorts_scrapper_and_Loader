"""Link content cards to imported external videos.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-15 21:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("content_items") as batch_op:
        batch_op.add_column(sa.Column("source_platform", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("source_id", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("source_url", sa.String(length=1000), nullable=True))
        batch_op.create_index(op.f("ix_content_items_source_platform"), ["source_platform"])
        batch_op.create_index(op.f("ix_content_items_source_id"), ["source_id"])
        batch_op.create_unique_constraint(
            "uq_content_items_external_source", ["project_id", "source_platform", "source_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("content_items") as batch_op:
        batch_op.drop_constraint("uq_content_items_external_source", type_="unique")
        batch_op.drop_index(op.f("ix_content_items_source_id"))
        batch_op.drop_index(op.f("ix_content_items_source_platform"))
        batch_op.drop_column("source_url")
        batch_op.drop_column("source_id")
        batch_op.drop_column("source_platform")
