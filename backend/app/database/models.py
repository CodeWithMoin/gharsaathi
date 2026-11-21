from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class SubjectType(str, enum.Enum):
    PERSON = "person"
    HOUSEHOLD = "household"
    BUSINESS = "business"


class MemoryStatus(str, enum.Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemorySourceType(str, enum.Enum):
    EXPLICIT_STATEMENT = "explicit_statement"
    CORRECTION = "correction"
    OBSERVED = "observed"
    INFERRED = "inferred"
    PROVIDER_DATA = "provider_data"


class MemorySensitivity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryEventType(str, enum.Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CORRECTED = "corrected"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class LegalConsentRecord(Base):
    """A versioned record of an authenticated user's legal acceptance."""

    __tablename__ = "legal_consents"
    __table_args__ = (
        UniqueConstraint("user_id", "terms_version", name="uq_legal_consent_user_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    terms_version: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    personalization_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    personalization_decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserProfileRecord(Base):
    """How the signed-in user asked to be addressed; always set by the user."""

    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), unique=True)
    first_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address_form: Mapped[str] = mapped_column(String(16), default="neutral")
    language: Mapped[str] = mapped_column(String(16), default="hinglish")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemorySubject(Base):
    __tablename__ = "memory_subjects"
    __table_args__ = (
        UniqueConstraint("subject_type", "external_id", name="uq_memory_subject_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_type: Mapped[SubjectType] = mapped_column(
        Enum(SubjectType, native_enum=False, length=24),
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memories: Mapped[list[MemoryItem]] = relationship(
        back_populates="subject",
        foreign_keys="MemoryItem.subject_id",
    )


class MemoryPermission(Base):
    __tablename__ = "memory_permissions"
    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "data_category",
            "purpose",
            name="uq_memory_permission_subject_category_purpose",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    data_category: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(80))
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    granularity: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sharing_scope: Mapped[str] = mapped_column(String(80), default="agent_and_user")
    user_control: Mapped[str] = mapped_column(
        String(80), default="view_edit_delete"
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        Index("ix_memory_items_subject_status", "subject_id", "status"),
        Index("ix_memory_items_subject_type", "subject_id", "memory_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    memory_type: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text)
    source_type: Mapped[MemorySourceType] = mapped_column(
        Enum(MemorySourceType, native_enum=False, length=32)
    )
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[MemoryStatus] = mapped_column(
        Enum(MemoryStatus, native_enum=False, length=24),
        default=MemoryStatus.PROPOSED,
        index=True,
    )
    sensitivity: Mapped[MemorySensitivity] = mapped_column(
        Enum(MemorySensitivity, native_enum=False, length=16)
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    allowed_purposes: Mapped[list[str]] = mapped_column(JSON)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supersedes_memory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    subject: Mapped[MemorySubject] = relationship(
        back_populates="memories", foreign_keys=[subject_id]
    )
    events: Mapped[list[MemoryEvent]] = relationship(
        back_populates="memory", cascade="all, delete-orphan"
    )


class MemoryEvent(Base):
    __tablename__ = "memory_events"
    __table_args__ = (Index("ix_memory_events_memory_created", "memory_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[MemoryEventType] = mapped_column(
        Enum(MemoryEventType, native_enum=False, length=24)
    )
    actor_subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    memory: Mapped[MemoryItem] = relationship(back_populates="events")


class ShoppingBasketRecord(Base):
    __tablename__ = "shopping_baskets"
    __table_args__ = (
        Index("ix_shopping_baskets_household_provider", "household_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    address_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ShoppingApprovalRecord(Base):
    __tablename__ = "shopping_approvals"
    __table_args__ = (Index("ix_shopping_approvals_basket", "basket_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    basket_id: Mapped[str] = mapped_column(
        ForeignKey("shopping_baskets.id", ondelete="CASCADE")
    )
    basket_revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payable_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ZeptoCheckoutAttemptRecord(Base):
    """Durable one-shot claim for a consequential Zepto order submission."""

    __tablename__ = "zepto_checkout_attempts"
    __table_args__ = (
        UniqueConstraint("approval_id", name="uq_zepto_checkout_attempt_approval"),
        UniqueConstraint(
            "household_id",
            "idempotency_key_hash",
            name="uq_zepto_checkout_household_idempotency",
        ),
        Index("ix_zepto_checkout_attempt_basket", "basket_id"),
        Index("ix_zepto_checkout_attempt_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("shopping_approvals.id", ondelete="RESTRICT")
    )
    basket_id: Mapped[str] = mapped_column(
        ForeignKey("shopping_baskets.id", ondelete="RESTRICT")
    )
    household_id: Mapped[str] = mapped_column(String(128))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    payable_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    provider_order_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PurchaseOrderNode(Base):
    """A minimal provider order node; deliberately excludes address/payment PII."""

    __tablename__ = "purchase_order_nodes"
    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "provider",
            "provider_order_id",
            name="uq_purchase_order_subject_provider_order",
        ),
        UniqueConstraint("id", "subject_id", name="uq_purchase_order_id_subject"),
        Index("ix_purchase_order_subject_occurred", "subject_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    provider_order_id: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_weekday: Mapped[int] = mapped_column(Integer)
    local_hour: Mapped[int] = mapped_column(Integer)
    utc_offset_minutes: Mapped[int] = mapped_column(Integer)
    total_paise: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    provenance_hash: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PurchaseProductNode(Base):
    __tablename__ = "purchase_product_nodes"
    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "provider",
            "identity_key",
            name="uq_purchase_product_subject_provider_identity",
        ),
        UniqueConstraint("id", "subject_id", name="uq_purchase_product_id_subject"),
        Index("ix_purchase_product_subject_name", "subject_id", "normalized_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    identity_key: Mapped[str] = mapped_column(String(160))
    provider_item_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PurchaseOccurrenceEdge(Base):
    """Person-scoped edge: an order contained a product at a point in time."""

    __tablename__ = "purchase_occurrence_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["order_id", "subject_id"],
            ["purchase_order_nodes.id", "purchase_order_nodes.subject_id"],
            ondelete="CASCADE",
            name="fk_purchase_occurrence_owned_order",
        ),
        ForeignKeyConstraint(
            ["product_id", "subject_id"],
            ["purchase_product_nodes.id", "purchase_product_nodes.subject_id"],
            ondelete="CASCADE",
            name="fk_purchase_occurrence_owned_product",
        ),
        UniqueConstraint(
            "order_id", "product_id", name="uq_purchase_occurrence_order_product"
        ),
        Index("ix_purchase_occurrence_subject_observed", "subject_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    quantity: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(80), default="provider_order_history")


class PurchaseCategoryNode(Base):
    __tablename__ = "purchase_category_nodes"
    __table_args__ = (
        UniqueConstraint(
            "subject_id", "slug", name="uq_purchase_category_subject_slug"
        ),
        UniqueConstraint("id", "subject_id", name="uq_purchase_category_id_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(160))


class ProductCategoryEdge(Base):
    __tablename__ = "product_category_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "subject_id"],
            ["purchase_product_nodes.id", "purchase_product_nodes.subject_id"],
            ondelete="CASCADE",
            name="fk_product_category_owned_product",
        ),
        ForeignKeyConstraint(
            ["category_id", "subject_id"],
            ["purchase_category_nodes.id", "purchase_category_nodes.subject_id"],
            ondelete="CASCADE",
            name="fk_product_category_owned_category",
        ),
        UniqueConstraint(
            "product_id", "category_id", name="uq_product_category_edge"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(80))


class PurchaseHistorySyncState(Base):
    __tablename__ = "purchase_history_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "subject_id", "provider", name="uq_purchase_sync_subject_provider"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_order_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ReorderSuggestionFeedback(Base):
    __tablename__ = "reorder_suggestion_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "subject_id"],
            ["purchase_product_nodes.id", "purchase_product_nodes.subject_id"],
            ondelete="CASCADE",
            name="fk_reorder_feedback_owned_product",
        ),
        Index(
            "ix_reorder_feedback_subject_product_created",
            "subject_id",
            "product_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(32))
    features: Mapped[dict[str, float]] = mapped_column(JSON)
    model_score: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PersonalRecommendationModel(Base):
    __tablename__ = "personal_recommendation_models"
    __table_args__ = (
        UniqueConstraint("subject_id", name="uq_personal_recommender_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    model_version: Mapped[str] = mapped_column(String(80))
    weights: Mapped[dict[str, float]] = mapped_column(JSON)
    bias: Mapped[float] = mapped_column(Float)
    training_examples: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_subject_active", "subject_id", "archived_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProviderAddressState(Base):
    __tablename__ = "provider_address_states"
    __table_args__ = (
        UniqueConstraint(
            "subject_id", "provider", name="uq_provider_address_state_subject_provider"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_subjects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    selected_address_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
