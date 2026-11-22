"""Saved shopping lists the user creates ("Weekly list", "Monthly ration").

Revision ID: 20261122_list01
Revises: 20261122_hist01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_list01"
down_revision: str | None = "20261122_hist01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_shopping_lists",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("name_key", sa.String(length=60), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name_key", name="uq_saved_shopping_lists_user_name"),
    )
    op.create_index(
        "ix_saved_shopping_lists_user_updated",
        "saved_shopping_lists",
        ["user_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_saved_shopping_lists_user_updated", table_name="saved_shopping_lists")
    op.drop_table("saved_shopping_lists")
