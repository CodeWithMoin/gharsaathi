"""Add person-scoped purchase history graph.

Revision ID: 20261121_0004
Revises: 20261121_0003
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261121_0004"
down_revision: str | None = "20261121_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_order_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_order_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_weekday", sa.Integer(), nullable=False),
        sa.Column("local_hour", sa.Integer(), nullable=False),
        sa.Column("utc_offset_minutes", sa.Integer(), nullable=False),
        sa.Column("total_paise", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("provenance_hash", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "subject_id", name="uq_purchase_order_id_subject"),
        sa.UniqueConstraint("subject_id", "provider", "provider_order_id", name="uq_purchase_order_subject_provider_order"),
    )
    op.create_index("ix_purchase_order_nodes_subject_id", "purchase_order_nodes", ["subject_id"])
    op.create_index("ix_purchase_order_subject_occurred", "purchase_order_nodes", ["subject_id", "occurred_at"])

    op.create_table(
        "purchase_product_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("identity_key", sa.String(length=160), nullable=False),
        sa.Column("provider_item_id", sa.String(length=160), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("normalized_name", sa.String(length=300), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "subject_id", name="uq_purchase_product_id_subject"),
        sa.UniqueConstraint("subject_id", "provider", "identity_key", name="uq_purchase_product_subject_provider_identity"),
    )
    op.create_index("ix_purchase_product_nodes_subject_id", "purchase_product_nodes", ["subject_id"])
    op.create_index("ix_purchase_product_subject_name", "purchase_product_nodes", ["subject_id", "normalized_name"])

    op.create_table(
        "purchase_category_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "subject_id", name="uq_purchase_category_id_subject"),
        sa.UniqueConstraint("subject_id", "slug", name="uq_purchase_category_subject_slug"),
    )
    op.create_index("ix_purchase_category_nodes_subject_id", "purchase_category_nodes", ["subject_id"])

    op.create_table(
        "purchase_history_sync_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_order_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "provider", name="uq_purchase_sync_subject_provider"),
    )
    op.create_index("ix_purchase_history_sync_states_subject_id", "purchase_history_sync_states", ["subject_id"])

    op.create_table(
        "purchase_occurrence_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(["order_id", "subject_id"], ["purchase_order_nodes.id", "purchase_order_nodes.subject_id"], name="fk_purchase_occurrence_owned_order", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id", "subject_id"], ["purchase_product_nodes.id", "purchase_product_nodes.subject_id"], name="fk_purchase_occurrence_owned_product", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "product_id", name="uq_purchase_occurrence_order_product"),
    )
    op.create_index("ix_purchase_occurrence_edges_subject_id", "purchase_occurrence_edges", ["subject_id"])
    op.create_index("ix_purchase_occurrence_subject_observed", "purchase_occurrence_edges", ["subject_id", "observed_at"])

    op.create_table(
        "product_category_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(["category_id", "subject_id"], ["purchase_category_nodes.id", "purchase_category_nodes.subject_id"], name="fk_product_category_owned_category", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id", "subject_id"], ["purchase_product_nodes.id", "purchase_product_nodes.subject_id"], name="fk_product_category_owned_product", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "category_id", name="uq_product_category_edge"),
    )
    op.create_index("ix_product_category_edges_subject_id", "product_category_edges", ["subject_id"])

    op.create_table(
        "reorder_suggestion_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("model_score", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["product_id", "subject_id"], ["purchase_product_nodes.id", "purchase_product_nodes.subject_id"], name="fk_reorder_feedback_owned_product", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reorder_suggestion_feedback_subject_id", "reorder_suggestion_feedback", ["subject_id"])
    op.create_index("ix_reorder_feedback_subject_product_created", "reorder_suggestion_feedback", ["subject_id", "product_id", "created_at"])

    op.create_table(
        "personal_recommendation_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("bias", sa.Float(), nullable=False),
        sa.Column("training_examples", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["memory_subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", name="uq_personal_recommender_subject"),
    )
    op.create_index("ix_personal_recommendation_models_subject_id", "personal_recommendation_models", ["subject_id"])


def downgrade() -> None:
    op.drop_table("personal_recommendation_models")
    op.drop_table("reorder_suggestion_feedback")
    op.drop_table("product_category_edges")
    op.drop_table("purchase_occurrence_edges")
    op.drop_table("purchase_history_sync_states")
    op.drop_table("purchase_category_nodes")
    op.drop_table("purchase_product_nodes")
    op.drop_table("purchase_order_nodes")
