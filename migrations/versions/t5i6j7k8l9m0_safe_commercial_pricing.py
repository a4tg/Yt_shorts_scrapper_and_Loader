"""safe commercial pricing

Revision ID: t5i6j7k8l9m0
Revises: s4h5i6j7k8l9
Create Date: 2026-07-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "t5i6j7k8l9m0"
down_revision: str | None = "s4h5i6j7k8l9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _plans() -> sa.TableClause:
    return sa.table(
        "plans",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("monthly_credits", sa.Integer),
        sa.column("price_minor", sa.Integer),
        sa.column("currency", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("feature_limits", sa.JSON),
    )


def upgrade() -> None:
    plans = _plans()
    bind = op.get_bind()
    catalog = {
        "free": {
            "name": "Пробный",
            "description": "7 дней для проверки ключевых возможностей сервиса",
            "monthly_credits": 20,
            "price_minor": 0,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 10,
            "feature_limits": {
                "workspaces": 1,
                "projects": 1,
                "members": 1,
                "storage_mb": 2048,
                "active_jobs": 1,
                "clips_per_job": 1,
            },
        },
        "creator": {
            "name": "Creator",
            "description": "Для самостоятельного маркетолога и небольшого портфеля брендов",
            "monthly_credits": 200,
            "price_minor": 149000,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 20,
            "feature_limits": {
                "workspaces": 1,
                "projects": 5,
                "members": 3,
                "storage_mb": 20480,
                "active_jobs": 10,
                "clips_per_job": 3,
            },
        },
        "studio": {
            "name": "Team",
            "description": "Для контент-команд и агентств с несколькими клиентами",
            "monthly_credits": 700,
            "price_minor": 449000,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 30,
            "feature_limits": {
                "workspaces": 3,
                "projects": 25,
                "members": 10,
                "storage_mb": 102400,
                "active_jobs": 30,
                "clips_per_job": 5,
            },
        },
        "agency": {
            "name": "Agency",
            "description": "Для агентств и внутренних редакций с большим объёмом контента",
            "monthly_credits": 1800,
            "price_minor": 999000,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 40,
            "feature_limits": {
                "workspaces": 10,
                "projects": 50,
                "members": 25,
                "storage_mb": 256000,
                "active_jobs": 50,
                "clips_per_job": 10,
            },
        },
    }
    existing = set(bind.execute(sa.select(plans.c.id)).scalars())
    for plan_id, values in catalog.items():
        if plan_id in existing:
            bind.execute(plans.update().where(plans.c.id == plan_id).values(**values))
        else:
            op.bulk_insert(plans, [{"id": plan_id, **values}])


def downgrade() -> None:
    plans = _plans()
    bind = op.get_bind()
    bind.execute(
        plans.update().where(plans.c.id == "free").values(
            name="Пробный",
            description="Пробный доступ к возможностям сервиса",
            monthly_credits=5,
            price_minor=0,
            sort_order=10,
            feature_limits={
                "workspaces": 2,
                "projects": 3,
                "members": 3,
                "storage_mb": 1000,
                "active_jobs": 10,
                "clips_per_job": 1,
            },
        )
    )
    bind.execute(
        plans.update().where(plans.c.id == "creator").values(
            name="Creator",
            description="Для маркетолога и небольшого портфеля брендов",
            monthly_credits=200,
            price_minor=149000,
            sort_order=20,
            feature_limits={
                "workspaces": 1,
                "projects": 5,
                "members": 5,
                "storage_mb": 25600,
                "active_jobs": 20,
                "clips_per_job": 3,
            },
        )
    )
    bind.execute(
        plans.update().where(plans.c.id == "studio").values(
            name="Studio",
            description="Для команд и агентств с несколькими клиентами",
            monthly_credits=1000,
            price_minor=399000,
            sort_order=30,
            feature_limits={
                "workspaces": 3,
                "projects": 25,
                "members": 20,
                "storage_mb": 204800,
                "active_jobs": 100,
                "clips_per_job": 5,
            },
        )
    )
    # Keep historical foreign-key references valid if Agency was already purchased.
    bind.execute(
        plans.update().where(plans.c.id == "agency").values(is_active=False)
    )
