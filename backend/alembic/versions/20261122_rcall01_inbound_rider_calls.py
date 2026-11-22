"""Inbound delivery line: prepared-context times and redacted call outcomes.

Revision ID: 20261122_rcall01
Revises: 20261122_list01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_rcall01"
down_revision: str | None = "20261122_list01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inbound_delivery_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("cohort_id", sa.String(length=200), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cohort_id"),
    )
    op.create_index(
        "ix_inbound_delivery_contexts_user_id", "inbound_delivery_contexts", ["user_id"]
    )
    op.create_index(
        "ix_inbound_delivery_contexts_prepared", "inbound_delivery_contexts", ["prepared_at"]
    )
    op.create_table(
        "inbound_rider_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("interaction_id", sa.String(length=160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=140), nullable=True),
        sa.Column("caller", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("interaction_id"),
    )
    op.create_index(
        "ix_inbound_rider_calls_user_started", "inbound_rider_calls", ["user_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_rider_calls_user_started", table_name="inbound_rider_calls")
    op.drop_table("inbound_rider_calls")
    op.drop_index("ix_inbound_delivery_contexts_prepared", table_name="inbound_delivery_contexts")
    op.drop_index("ix_inbound_delivery_contexts_user_id", table_name="inbound_delivery_contexts")
    op.drop_table("inbound_delivery_contexts")
