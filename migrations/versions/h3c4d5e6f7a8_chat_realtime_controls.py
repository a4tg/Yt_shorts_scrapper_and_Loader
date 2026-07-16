"""chat realtime controls

Revision ID: h3c4d5e6f7a8
Revises: g2b3c4d5e6f7
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "h3c4d5e6f7a8"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("mentioned_user_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("pinned_by_user_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_messages_pinned_by_user_id_users",
            "users", ["pinned_by_user_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_index("ix_messages_pinned_at", ["pinned_at"])
        batch_op.create_index("ix_messages_pinned_by_user_id", ["pinned_by_user_id"])

    op.create_table(
        "message_reactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("emoji", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "user_id", "emoji", name="uq_message_reaction_user_emoji"),
    )
    op.create_index("ix_message_reactions_message_id", "message_reactions", ["message_id"])
    op.create_index("ix_message_reactions_user_id", "message_reactions", ["user_id"])


def downgrade() -> None:
    op.drop_table("message_reactions")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_index("ix_messages_pinned_by_user_id")
        batch_op.drop_index("ix_messages_pinned_at")
        batch_op.drop_constraint("fk_messages_pinned_by_user_id_users", type_="foreignkey")
        batch_op.drop_column("pinned_by_user_id")
        batch_op.drop_column("pinned_at")
        batch_op.drop_column("mentioned_user_ids")
