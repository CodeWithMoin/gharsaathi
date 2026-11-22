"""Keep the exact bill with each approval so it can be read back without re-quoting.

Revision ID: 20261122_0010
Revises: 20261122_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_0010"
down_revision: str | None = "20261122_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shopping_approvals", sa.Column("quote", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shopping_approvals", "quote")
