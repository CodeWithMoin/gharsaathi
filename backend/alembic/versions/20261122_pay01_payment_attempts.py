"""Record the payment method and payment state of each Zepto checkout attempt.

Revision ID: 20261122_pay01
Revises: 20261122_mem01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_pay01"
down_revision: str | None = "20261122_mem01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "zepto_checkout_attempts",
        sa.Column("payment_method", sa.String(16), nullable=False, server_default="cod"),
    )
    op.add_column(
        "zepto_checkout_attempts",
        sa.Column("payment_url", sa.String(2048), nullable=True),
    )
    op.add_column(
        "zepto_checkout_attempts",
        sa.Column("payment_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "zepto_checkout_attempts",
        sa.Column(
            "payment_poll_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("zepto_checkout_attempts", "payment_poll_allowed")
    op.drop_column("zepto_checkout_attempts", "payment_checked_at")
    op.drop_column("zepto_checkout_attempts", "payment_url")
    op.drop_column("zepto_checkout_attempts", "payment_method")
