"""Persist user-scoped baskets and approvals.

Revision ID: 20261122_0005
Revises: 20261121_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_0005"
down_revision: str | None = "20261121_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shopping_baskets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("household_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("address_id", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shopping_baskets_household_id", "shopping_baskets", ["household_id"])
    op.create_index("ix_shopping_baskets_status", "shopping_baskets", ["status"])
    op.create_index("ix_shopping_baskets_updated_at", "shopping_baskets", ["updated_at"])
    op.create_index("ix_shopping_baskets_household_provider", "shopping_baskets", ["household_id", "provider"])
    op.create_table(
        "shopping_approvals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("basket_id", sa.String(length=64), sa.ForeignKey("shopping_baskets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("basket_revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("payable_paise", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_shopping_approvals_basket", "shopping_approvals", ["basket_id"])
    op.create_index("ix_shopping_approvals_status", "shopping_approvals", ["status"])


def downgrade() -> None:
    op.drop_table("shopping_approvals")
    op.drop_table("shopping_baskets")
