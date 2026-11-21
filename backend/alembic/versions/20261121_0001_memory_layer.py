"""Add person-scoped memory, permissions, and audit events.

Revision ID: 20261121_0001
Revises:
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261121_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_subjects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "subject_type",
            sa.Enum("PERSON", "HOUSEHOLD", "BUSINESS", name="subjecttype", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_type", "external_id", name="uq_memory_subject_identity"),
    )
    op.create_index("ix_memory_subjects_subject_type", "memory_subjects", ["subject_type"])

    op.create_table(
        "memory_permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("data_category", sa.String(length=80), nullable=False),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("granularity", sa.String(length=160), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("sharing_scope", sa.String(length=80), nullable=False),
        sa.Column("user_control", sa.String(length=80), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "data_category", "purpose", name="uq_memory_permission_subject_category_purpose"),
    )
    op.create_index("ix_memory_permissions_subject_id", "memory_permissions", ["subject_id"])

    op.create_table(
        "memory_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("memory_type", sa.String(length=80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum("EXPLICIT_STATEMENT", "CORRECTION", "OBSERVED", "INFERRED", "PROVIDER_DATA", name="memorysourcetype", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PROPOSED", "CONFIRMED", "REJECTED", "SUPERSEDED", "DELETED", name="memorystatus", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("source_conversation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "sensitivity",
            sa.Enum("LOW", "MEDIUM", "HIGH", name="memorysensitivity", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("allowed_purposes", sa.JSON(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_memory_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supersedes_memory_id"], ["memory_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_items_status", "memory_items", ["status"])
    op.create_index("ix_memory_items_subject_id", "memory_items", ["subject_id"])
    op.create_index("ix_memory_items_subject_status", "memory_items", ["subject_id", "status"])
    op.create_index("ix_memory_items_subject_type", "memory_items", ["subject_id", "memory_type"])

    op.create_table(
        "memory_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum("PROPOSED", "CONFIRMED", "REJECTED", "CORRECTED", "SUPERSEDED", "DELETED", name="memoryeventtype", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("actor_subject_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_subject_id"], ["memory_subjects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["memory_id"], ["memory_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_events_actor_subject_id", "memory_events", ["actor_subject_id"])
    op.create_index("ix_memory_events_memory_id", "memory_events", ["memory_id"])
    op.create_index("ix_memory_events_memory_created", "memory_events", ["memory_id", "created_at"])


def downgrade() -> None:
    op.drop_table("memory_events")
    op.drop_table("memory_items")
    op.drop_table("memory_permissions")
    op.drop_table("memory_subjects")
