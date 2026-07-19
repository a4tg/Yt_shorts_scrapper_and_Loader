"""Persist checkout consent audit data.

Revision ID: s4h5i6j7k8l9
Revises: r3g4h5i6j7k8
"""

from alembic import op
import sqlalchemy as sa


revision = "s4h5i6j7k8l9"
down_revision = "r3g4h5i6j7k8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("offer_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("recurring_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("legal_version", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "legal_version")
    op.drop_column("payments", "recurring_consent_at")
    op.drop_column("payments", "offer_accepted_at")
