"""Persist one-shot Zepto checkout attempts.

Revision ID: 20261122_0006
Revises: 20261122_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_0006"
down_revision: str | None = "20261122_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zepto_checkout_attempts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "approval_id",
            sa.String(length=64),
            sa.ForeignKey("shopping_approvals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "basket_id",
            sa.String(length=64),
            sa.ForeignKey("shopping_baskets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("household_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("payable_paise", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_order_id", sa.String(length=256), nullable=True),
        sa.Column("provider_status", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "approval_id", name="uq_zepto_checkout_attempt_approval"
        ),
        sa.UniqueConstraint(
            "household_id",
            "idempotency_key_hash",
            name="uq_zepto_checkout_household_idempotency",
        ),
    )
    op.create_index(
        "ix_zepto_checkout_attempt_basket", "zepto_checkout_attempts", ["basket_id"]
    )
    op.create_index(
        "ix_zepto_checkout_attempt_status", "zepto_checkout_attempts", ["status"]
    )


def downgrade() -> None:
    op.drop_table("zepto_checkout_attempts")
