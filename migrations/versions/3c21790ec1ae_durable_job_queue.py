"""durable job queue

Revision ID: 3c21790ec1ae
Revises: ebd33af7ee16
Create Date: 2026-07-15 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3c21790ec1ae"
down_revision: Union[str, None] = "ebd33af7ee16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(length=36), nullable=True)
        batch_op.add_column(
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("worker_id", sa.String(length=80)))
        batch_op.add_column(
            sa.Column("attempts", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False)
        )
        batch_op.add_column(sa.Column("download_ticket_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("downloaded_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("delete_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True)))
        batch_op.create_index("ix_jobs_available_at", ["available_at"])
        batch_op.create_index("ix_jobs_lease_expires_at", ["lease_expires_at"])
        batch_op.create_index("ix_jobs_worker_id", ["worker_id"])
        batch_op.create_index("ix_jobs_delete_at", ["delete_at"])
        batch_op.create_index("ix_jobs_deleted_at", ["deleted_at"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_deleted_at")
        batch_op.drop_index("ix_jobs_delete_at")
        batch_op.drop_index("ix_jobs_worker_id")
        batch_op.drop_index("ix_jobs_lease_expires_at")
        batch_op.drop_index("ix_jobs_available_at")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("delete_at")
        batch_op.drop_column("downloaded_at")
        batch_op.drop_column("download_ticket_at")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempts")
        batch_op.drop_column("worker_id")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("available_at")
        batch_op.alter_column("user_id", existing_type=sa.String(length=36), nullable=False)
