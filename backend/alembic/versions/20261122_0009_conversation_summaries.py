"""Store a rolling summary with each conversation.

Revision ID: 20261122_0009
Revises: 20261122_race01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_0009"
down_revision: str | None = "20261122_race01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "summary")
