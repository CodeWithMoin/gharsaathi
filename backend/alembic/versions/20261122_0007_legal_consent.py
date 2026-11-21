"""Persist versioned legal onboarding consent.

Revision ID: 20261122_0007
Revises: 20261122_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20261122_0007"
down_revision: str | None = "20261122_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("terms_version", sa.String(length=64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("personalization_allowed", sa.Boolean(), nullable=False),
        sa.Column("personalization_decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "terms_version", name="uq_legal_consent_user_version"),
    )
    op.create_index("ix_legal_consents_user_id", "legal_consents", ["user_id"])


def downgrade() -> None:
    op.drop_table("legal_consents")
