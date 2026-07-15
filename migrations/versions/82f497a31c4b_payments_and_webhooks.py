"""payments and webhooks

Revision ID: 82f497a31c4b
Revises: 91f36d240ac2
Create Date: 2026-07-15 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "82f497a31c4b"
down_revision: Union[str, None] = "91f36d240ac2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.add_column(sa.Column("payment_method_id", sa.String(length=160)))
        batch_op.add_column(sa.Column("renewal_attempted_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("grace_until", sa.DateTime(timezone=True)))
        batch_op.create_index("ix_subscriptions_grace_until", ["grace_until"])

    with op.batch_alter_table("payments") as batch_op:
        batch_op.alter_column(
            "provider_payment_id", existing_type=sa.String(length=160), nullable=True
        )
        batch_op.add_column(sa.Column("plan_id", sa.String(length=40)))
        batch_op.add_column(sa.Column("subscription_id", sa.String(length=36)))
        batch_op.add_column(
            sa.Column("credits", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("confirmation_url", sa.Text()))
        batch_op.add_column(sa.Column("billing_period_key", sa.String(length=160)))
        batch_op.add_column(sa.Column("provider_payment_method_id", sa.String(length=160)))
        batch_op.add_column(sa.Column("provider_created_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("failure_reason", sa.Text()))
        batch_op.create_check_constraint("ck_payments_credits_nonnegative", "credits >= 0")
        batch_op.create_foreign_key("fk_payments_plan_id", "plans", ["plan_id"], ["id"])
        batch_op.create_foreign_key(
            "fk_payments_subscription_id",
            "subscriptions",
            ["subscription_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_payments_plan_id", ["plan_id"])
        batch_op.create_index("ix_payments_subscription_id", ["subscription_id"])
        batch_op.create_index(
            "ix_payments_billing_period_key", ["billing_period_key"], unique=True
        )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("event_key", sa.String(length=240), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.String(length=160), nullable=False),
        sa.Column("source_ip", sa.String(length=64)),
        sa.Column("payload", sa.JSON()),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_events_provider", "webhook_events", ["provider"])
    op.create_index("ix_webhook_events_event_key", "webhook_events", ["event_key"], unique=True)
    op.create_index("ix_webhook_events_event_type", "webhook_events", ["event_type"])
    op.create_index("ix_webhook_events_object_id", "webhook_events", ["object_id"])
    op.create_index("ix_webhook_events_status", "webhook_events", ["status"])
    op.create_index("ix_webhook_events_received_at", "webhook_events", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_events_received_at", table_name="webhook_events")
    op.drop_index("ix_webhook_events_status", table_name="webhook_events")
    op.drop_index("ix_webhook_events_object_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_event_type", table_name="webhook_events")
    op.drop_index("ix_webhook_events_event_key", table_name="webhook_events")
    op.drop_index("ix_webhook_events_provider", table_name="webhook_events")
    op.drop_table("webhook_events")
    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_index("ix_payments_billing_period_key")
        batch_op.drop_index("ix_payments_subscription_id")
        batch_op.drop_index("ix_payments_plan_id")
        batch_op.drop_constraint("fk_payments_subscription_id", type_="foreignkey")
        batch_op.drop_constraint("fk_payments_plan_id", type_="foreignkey")
        batch_op.drop_constraint("ck_payments_credits_nonnegative", type_="check")
        batch_op.drop_column("failure_reason")
        batch_op.drop_column("provider_created_at")
        batch_op.drop_column("provider_payment_method_id")
        batch_op.drop_column("billing_period_key")
        batch_op.drop_column("confirmation_url")
        batch_op.drop_column("credits")
        batch_op.drop_column("subscription_id")
        batch_op.drop_column("plan_id")
        batch_op.alter_column(
            "provider_payment_id", existing_type=sa.String(length=160), nullable=False
        )
    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.drop_index("ix_subscriptions_grace_until")
        batch_op.drop_column("grace_until")
        batch_op.drop_column("renewal_attempted_at")
        batch_op.drop_column("payment_method_id")
