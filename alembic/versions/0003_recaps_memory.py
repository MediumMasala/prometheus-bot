"""daily_recaps + memory_summary

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-03

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_recaps",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("done_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancelled_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "sleep_state",
            sa.Enum("unknown", "awake", "asleep", name="sleep_state"),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("recap_sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "memory_summary",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("covers_until", sa.Date(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_memory_summary_generated_at", "memory_summary", ["generated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_memory_summary_generated_at", table_name="memory_summary")
    op.drop_table("memory_summary")
    op.drop_table("daily_recaps")
    sa.Enum(name="sleep_state").drop(op.get_bind(), checkfirst=True)
