"""account recovery tokens

Revision ID: 5dbe4a9120ef
Revises: 82f497a31c4b
Create Date: 2026-07-15 20:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5dbe4a9120ef"
down_revision: Union[str, None] = "82f497a31c4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing installations predate email verification. Mark their current
    # accounts as trusted before requiring verification for new registrations.
    op.execute(
        "UPDATE users SET email_verified_at = CURRENT_TIMESTAMP "
        "WHERE email_verified_at IS NULL"
    )
    op.create_table(
        "account_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')",
            name="ck_account_tokens_purpose",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_account_tokens_user_id", "account_tokens", ["user_id"])
    op.create_index("ix_account_tokens_purpose", "account_tokens", ["purpose"])
    op.create_index("ix_account_tokens_token_hash", "account_tokens", ["token_hash"], unique=True)
    op.create_index("ix_account_tokens_expires_at", "account_tokens", ["expires_at"])
    op.create_index("ix_account_tokens_used_at", "account_tokens", ["used_at"])


def downgrade() -> None:
    op.drop_index("ix_account_tokens_used_at", table_name="account_tokens")
    op.drop_index("ix_account_tokens_expires_at", table_name="account_tokens")
    op.drop_index("ix_account_tokens_token_hash", table_name="account_tokens")
    op.drop_index("ix_account_tokens_purpose", table_name="account_tokens")
    op.drop_index("ix_account_tokens_user_id", table_name="account_tokens")
    op.drop_table("account_tokens")
