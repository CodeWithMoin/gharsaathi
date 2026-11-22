"""Rider assist: per-user switch + delivery notes, and rider call summaries.

Revision ID: 20261122_rider01
Revises: 20261122_pay01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_rider01"
down_revision: str | None = "20261122_pay01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rider_assist_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("landmark", sa.String(length=160), nullable=True),
        sa.Column("directions", sa.String(length=300), nullable=True),
        sa.Column("handoff", sa.String(length=24), server_default="call_again", nullable=False),
        sa.Column("preferred_language", sa.String(length=16), server_default="hinglish", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "rider_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("caller", sa.String(length=16), nullable=False),
        sa.Column("delivery_call", sa.Boolean(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("rider_said", sa.String(length=160), nullable=True),
        sa.Column("summary", sa.String(length=600), nullable=False),
        sa.Column("answered_from", sa.JSON(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rider_calls_user_started", "rider_calls", ["user_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_rider_calls_user_started", table_name="rider_calls")
    op.drop_table("rider_calls")
    op.drop_table("rider_assist_settings")
