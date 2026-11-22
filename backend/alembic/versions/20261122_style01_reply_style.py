"""Profile reply style: quick (default) or detailed.

Revision ID: 20261122_style01
Revises: 20261122_rider01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_style01"
down_revision: str | None = "20261122_rider01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("reply_style", sa.String(length=16), server_default="quick", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "reply_style")
