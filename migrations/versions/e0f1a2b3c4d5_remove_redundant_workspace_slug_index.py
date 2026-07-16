"""Remove the redundant non-unique workspace slug index.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-07-16 14:00:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A unique constraint on workspaces.slug already creates the index needed
    # for lookups. The second non-unique index only consumed storage and made
    # the declared SQLAlchemy model diverge from the migrated schema.
    op.drop_index(op.f("ix_workspaces_slug"), table_name="workspaces")


def downgrade() -> None:
    op.create_index(op.f("ix_workspaces_slug"), "workspaces", ["slug"], unique=False)
