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
