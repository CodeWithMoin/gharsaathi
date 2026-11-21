"""Allow at most one open basket per household and provider.

Existing duplicates are resolved deterministically before the index is built:
per (household_id, provider) the open basket kept is the one in checkout
(never abandon an in-flight order), then the most recently updated, then the
highest id. The others become ``abandoned`` and their pending/approved
approvals become ``superseded``, so no approval survives for a basket the user
can no longer see.

Revision ID: 20261122_race01
Revises: 20261122_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_race01"
down_revision: str | None = "20261122_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPEN_PREDICATE = "status IN ('draft', 'awaiting_approval', 'approved', 'checkout_pending')"
DUPLICATES = f"""
    SELECT id FROM (
        SELECT
            id,
            row_number() OVER (
                PARTITION BY household_id, provider
                ORDER BY (status = 'checkout_pending') DESC, updated_at DESC, id DESC
            ) AS position
        FROM shopping_baskets
        WHERE {OPEN_PREDICATE}
    ) AS ranked
    WHERE position > 1
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE shopping_approvals
        SET status = 'superseded', decided_at = CURRENT_TIMESTAMP
        WHERE status IN ('pending', 'approved') AND basket_id IN ({DUPLICATES})
        """
    )
    op.execute(f"UPDATE shopping_baskets SET status = 'abandoned' WHERE id IN ({DUPLICATES})")
    op.create_index(
        "uq_shopping_baskets_one_open",
        "shopping_baskets",
        ["household_id", "provider"],
        unique=True,
        postgresql_where=sa.text(OPEN_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_shopping_baskets_one_open", table_name="shopping_baskets")
