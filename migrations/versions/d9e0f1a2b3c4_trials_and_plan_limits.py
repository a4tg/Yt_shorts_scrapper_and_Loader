"""trials and plan limits

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d9e0f1a2b3c4"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("trial_expires_at", sa.DateTime(timezone=True)))
        batch_op.create_index(op.f("ix_users_trial_expires_at"), ["trial_expires_at"])
    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(sa.Column("feature_limits", sa.JSON()))
    op.execute(sa.text("UPDATE users SET trial_expires_at = created_at + INTERVAL '14 days'") if op.get_bind().dialect.name == "postgresql" else sa.text("UPDATE users SET trial_expires_at = datetime(created_at, '+14 days')"))
    plans = {
        "free": {"workspaces": 2, "projects": 3, "members": 3, "storage_mb": 1000, "active_jobs": 10, "clips_per_job": 1},
        "creator": {"workspaces": 1, "projects": 5, "members": 5, "storage_mb": 25600, "active_jobs": 20, "clips_per_job": 3},
        "studio": {"workspaces": 3, "projects": 25, "members": 20, "storage_mb": 204800, "active_jobs": 100, "clips_per_job": 5},
    }
    for plan_id, limits in plans.items():
        statement = sa.text("UPDATE plans SET feature_limits = :limits WHERE id = :id").bindparams(
            sa.bindparam("limits", type_=sa.JSON), sa.bindparam("id", type_=sa.String)
        )
        op.get_bind().execute(statement, {"id": plan_id, "limits": limits})
    op.execute(sa.text("UPDATE plans SET monthly_credits=200, price_minor=149000, description='Для маркетолога и небольшого портфеля брендов' WHERE id='creator'"))
    op.execute(sa.text("UPDATE plans SET monthly_credits=1000, price_minor=399000, description='Для команд и агентств с несколькими клиентами' WHERE id='studio'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE plans SET monthly_credits=100, price_minor=49900 WHERE id='creator'"))
    op.execute(sa.text("UPDATE plans SET monthly_credits=500, price_minor=149900 WHERE id='studio'"))
    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("feature_limits")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index(op.f("ix_users_trial_expires_at"))
        batch_op.drop_column("trial_expires_at")
