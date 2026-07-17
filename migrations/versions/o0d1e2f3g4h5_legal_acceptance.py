"""record registration legal acceptance

Revision ID: o0d1e2f3g4h5
Revises: n9c0d1e2f3g4
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "o0d1e2f3g4h5"
down_revision = "n9c0d1e2f3g4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("legal_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("legal_version", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "legal_version")
    op.drop_column("users", "legal_accepted_at")
