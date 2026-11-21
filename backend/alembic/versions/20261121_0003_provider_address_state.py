"""Add persistent provider address snapshots and selection.

Revision ID: 20261121_0003
Revises: 20261121_0002
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261121_0003"
down_revision: str | None = "20261121_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_address_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("selected_address_id", sa.String(length=256), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subject_id",
            "provider",
            name="uq_provider_address_state_subject_provider",
        ),
    )
    op.create_index(
        "ix_provider_address_states_subject_id",
        "provider_address_states",
        ["subject_id"],
    )


def downgrade() -> None:
    op.drop_table("provider_address_states")
