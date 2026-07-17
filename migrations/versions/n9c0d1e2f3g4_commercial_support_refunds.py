"""commercial support audit and payment refunds

Revision ID: n9c0d1e2f3g4
Revises: m8b9c0d1e2f3
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa


revision = "n9c0d1e2f3g4"
down_revision = "m8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_refunds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=160), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("credits_reversed", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("provider_details", sa.JSON(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_payment_refunds_amount_positive"),
        sa.CheckConstraint("credits_reversed >= 0", name="ck_payment_refunds_credits_nonnegative"),
        sa.CheckConstraint(
            "status IN ('creating', 'pending', 'succeeded', 'canceled', 'error')",
            name="ck_payment_refunds_status",
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("payment_id", name="uq_payment_refunds_payment"),
        sa.UniqueConstraint("provider_refund_id"),
    )
    for column in (
        "payment_id", "user_id", "requested_by_user_id", "provider_refund_id",
        "status", "completed_at",
    ):
        op.create_index(f"ix_payment_refunds_{column}", "payment_refunds", [column])

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=160), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("actor_user_id", "action", "target_type", "target_id", "created_at"):
        op.create_index(f"ix_admin_audit_log_{column}", "admin_audit_log", [column])


def downgrade() -> None:
    op.drop_table("admin_audit_log")
    op.drop_table("payment_refunds")
