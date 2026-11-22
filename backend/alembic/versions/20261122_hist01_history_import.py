"""Resumable, rate-limit-aware purchase-history import and opt-in weekly sync.

Revision ID: 20261122_hist01
Revises: 20261122_style01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_hist01"
down_revision: str | None = "20261122_style01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATE = "purchase_history_sync_states"


def upgrade() -> None:
    op.add_column(
        "purchase_order_nodes",
        sa.Column("provider_lookup_id", sa.String(length=160), nullable=True),
    )
    for name in ("imported_order_count", "skipped_known_count", "detail_failure_count"):
        op.add_column(
            _STATE,
            sa.Column(name, sa.Integer(), server_default="0", nullable=False),
        )
    op.add_column(_STATE, sa.Column("stopped_reason", sa.String(length=32), nullable=True))
    op.add_column(_STATE, sa.Column("resume_page", sa.Integer(), nullable=True))
    op.add_column(
        _STATE,
        sa.Column("backfill_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        _STATE, sa.Column("last_full_resync_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        _STATE, sa.Column("rate_limited_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        _STATE,
        sa.Column("auto_sync_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "auto_sync_enabled",
        "rate_limited_until",
        "last_full_resync_at",
        "backfill_complete",
        "resume_page",
        "stopped_reason",
        "detail_failure_count",
        "skipped_known_count",
        "imported_order_count",
    ):
        op.drop_column(_STATE, name)
    op.drop_column("purchase_order_nodes", "provider_lookup_id")
