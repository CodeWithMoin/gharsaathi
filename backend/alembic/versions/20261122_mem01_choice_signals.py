"""Record basket choices for repeated-choice memory suggestions.

Revision ID: 20261122_mem01
Revises: 20261122_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_mem01"
down_revision: str | None = "20261122_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_choice_signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("occasion_key", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("item", sa.String(length=120), nullable=False),
        sa.Column("product_name", sa.String(length=500), nullable=False),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("pack_size", sa.String(length=120), nullable=True),
        sa.Column("competing_providers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_choice_signals_subject_id", "memory_choice_signals", ["subject_id"]
    )
    op.create_index(
        "ix_memory_choice_signals_subject_created",
        "memory_choice_signals",
        ["subject_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_choice_signals_subject_created", table_name="memory_choice_signals")
    op.drop_index("ix_memory_choice_signals_subject_id", table_name="memory_choice_signals")
    op.drop_table("memory_choice_signals")
