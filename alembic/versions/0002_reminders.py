"""reminders + reminder_fires

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-03

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column(
            "schedule_type",
            sa.Enum("one_off", "recurring", name="schedule_type"),
            nullable=False,
        ),
        sa.Column("recurrence_rule", sa.String(length=100), nullable=True),
        sa.Column("fire_time", sa.Time(), nullable=True),
        sa.Column("one_off_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "paused", "killed", name="reminder_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
    op.create_index("ix_reminders_status", "reminders", ["status"])

    op.create_table(
        "reminder_fires",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reminder_id",
            sa.Integer(),
            sa.ForeignKey("reminders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(
                "pending",
                "done",
                "cancelled",
                "snoozed",
                "missed",
                "note_added",
                name="fire_outcome",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("snooze_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note_text", sa.Text(), nullable=True),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column("persona_text", sa.Text(), nullable=True),
        sa.Column("nag_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_nag_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reminder_fires_reminder_id", "reminder_fires", ["reminder_id"])
    op.create_index(
        "ix_reminder_fires_pending",
        "reminder_fires",
        ["acknowledged_at"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_reminder_fires_pending", table_name="reminder_fires")
    op.drop_index("ix_reminder_fires_reminder_id", table_name="reminder_fires")
    op.drop_table("reminder_fires")
    op.drop_index("ix_reminders_status", table_name="reminders")
    op.drop_index("ix_reminders_user_id", table_name="reminders")
    op.drop_table("reminders")
    sa.Enum(name="fire_outcome").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="reminder_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="schedule_type").drop(op.get_bind(), checkfirst=True)
