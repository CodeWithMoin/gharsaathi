"""Inbound delivery line: saved per-address directions and the line's last state.

Revision ID: 20261122_line01
Revises: 20261122_rcall01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_line01"
down_revision: str | None = "20261122_rcall01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_directions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("address_id", sa.String(length=128), nullable=False),
        sa.Column("landmark", sa.String(length=120), nullable=False),
        sa.Column("route_directions", sa.JSON(), nullable=False),
        sa.Column("gate_note", sa.String(length=120), nullable=True),
        sa.Column("handover_instructions", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "address_id", name="uq_delivery_directions_user_address"),
    )
    op.create_table(
        "delivery_line_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("auto_share", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("phase", sa.String(length=16), server_default="none", nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=True),
        sa.Column("address_id", sa.String(length=128), nullable=True),
        sa.Column("cohort_id", sa.String(length=200), nullable=True),
        sa.Column("monitor_id", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("delivery_line_states")
    op.drop_table("delivery_directions")
