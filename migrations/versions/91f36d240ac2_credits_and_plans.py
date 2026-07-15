"""credits and plans

Revision ID: 91f36d240ac2
Revises: 3c21790ec1ae
Create Date: 2026-07-15 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91f36d240ac2"
down_revision: Union[str, None] = "3c21790ec1ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("credit_balance", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("reserved_credits", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.create_check_constraint(
            "ck_users_credit_balance_nonnegative", "credit_balance >= 0"
        )
        batch_op.create_check_constraint(
            "ck_users_reserved_credits_nonnegative", "reserved_credits >= 0"
        )
        batch_op.create_check_constraint(
            "ck_users_reserved_within_balance", "reserved_credits <= credit_balance"
        )

    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(sa.Column("description", sa.String(length=500)))
        batch_op.add_column(
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False)
        )

    with op.batch_alter_table("credit_ledger") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=160)))
        batch_op.create_index(
            "ix_credit_ledger_idempotency_key", ["idempotency_key"], unique=True
        )

    plans = sa.table(
        "plans",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("monthly_credits", sa.Integer),
        sa.column("price_minor", sa.Integer),
        sa.column("currency", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        plans,
        [
            {
                "id": "free",
                "name": "Пробный",
                "description": "5 стартовых обработок для знакомства с сервисом",
                "monthly_credits": 5,
                "price_minor": 0,
                "currency": "RUB",
                "is_active": True,
                "sort_order": 10,
            },
            {
                "id": "creator",
                "name": "Creator",
                "description": "Для регулярной обработки коротких видео",
                "monthly_credits": 100,
                "price_minor": 49900,
                "currency": "RUB",
                "is_active": True,
                "sort_order": 20,
            },
            {
                "id": "studio",
                "name": "Studio",
                "description": "Для команд и пакетной обработки",
                "monthly_credits": 500,
                "price_minor": 149900,
                "currency": "RUB",
                "is_active": True,
                "sort_order": 30,
            },
        ],
    )

    # Existing accounts receive the same starter grant as newly registered users.
    op.execute(
        sa.text(
            """
            INSERT INTO credit_ledger
                (id, user_id, amount, operation_type, idempotency_key, description)
            SELECT user_id, user_id, 5, 'signup_grant',
                   'signup-migration:' || user_id,
                   'Стартовые кредиты при включении биллинга'
            FROM (SELECT id AS user_id FROM users) AS existing_users
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE users
            SET credit_balance = COALESCE(
                (SELECT SUM(amount) FROM credit_ledger WHERE credit_ledger.user_id = users.id),
                0
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM credit_ledger "
            "WHERE operation_type = 'signup_grant' AND idempotency_key LIKE 'signup-migration:%'"
        )
    )
    with op.batch_alter_table("credit_ledger") as batch_op:
        batch_op.drop_index("ix_credit_ledger_idempotency_key")
        batch_op.drop_column("idempotency_key")
    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("sort_order")
        batch_op.drop_column("description")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_reserved_within_balance", type_="check")
        batch_op.drop_constraint("ck_users_reserved_credits_nonnegative", type_="check")
        batch_op.drop_constraint("ck_users_credit_balance_nonnegative", type_="check")
        batch_op.drop_column("reserved_credits")
        batch_op.drop_column("credit_balance")
