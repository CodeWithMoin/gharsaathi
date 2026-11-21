from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    MemoryEvent,
    MemoryEventType,
    MemoryItem,
    MemoryPermission,
    MemoryStatus,
    MemorySubject,
    SubjectType,
)


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_person(self, external_id: str) -> MemorySubject:
        result = await self.session.execute(
            select(MemorySubject).where(
                MemorySubject.subject_type == SubjectType.PERSON,
                MemorySubject.external_id == external_id,
            )
        )
        subject = result.scalar_one_or_none()
        if subject is not None:
            return subject
        subject = MemorySubject(
            subject_type=SubjectType.PERSON,
            external_id=external_id,
        )
        self.session.add(subject)
        await self.session.flush()
        return subject

    async def find_person(self, external_id: str) -> MemorySubject | None:
        result = await self.session.execute(
            select(MemorySubject).where(
                MemorySubject.subject_type == SubjectType.PERSON,
                MemorySubject.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_memory(self, memory_id: UUID, subject_id: UUID) -> MemoryItem | None:
        result = await self.session.execute(
            select(MemoryItem).where(
                MemoryItem.id == memory_id,
                MemoryItem.subject_id == subject_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_memories(
        self,
        subject_id: UUID,
        status: MemoryStatus | None,
        include_deleted: bool,
    ) -> list[MemoryItem]:
        statement = select(MemoryItem).where(MemoryItem.subject_id == subject_id)
        if status is not None:
            statement = statement.where(MemoryItem.status == status)
        elif not include_deleted:
            statement = statement.where(MemoryItem.status != MemoryStatus.DELETED)
        result = await self.session.execute(
            statement.order_by(MemoryItem.updated_at.desc(), MemoryItem.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_confirmed_candidates(
        self,
        subject_id: UUID,
        memory_types: list[str] | None,
        candidate_limit: int,
    ) -> list[MemoryItem]:
        statement = select(MemoryItem).where(
            MemoryItem.subject_id == subject_id,
            MemoryItem.status == MemoryStatus.CONFIRMED,
        )
        if memory_types:
            statement = statement.where(MemoryItem.memory_type.in_(memory_types))
        result = await self.session.execute(
            statement.order_by(MemoryItem.updated_at.desc()).limit(candidate_limit)
        )
        return list(result.scalars().all())

    async def get_permission(
        self, subject_id: UUID, data_category: str, purpose: str
    ) -> MemoryPermission | None:
        result = await self.session.execute(
            select(MemoryPermission).where(
                MemoryPermission.subject_id == subject_id,
                MemoryPermission.data_category == data_category,
                MemoryPermission.purpose == purpose,
            )
        )
        return result.scalar_one_or_none()

    async def list_permissions(self, subject_id: UUID) -> list[MemoryPermission]:
        result = await self.session.execute(
            select(MemoryPermission)
            .where(MemoryPermission.subject_id == subject_id)
            .order_by(MemoryPermission.data_category, MemoryPermission.purpose)
        )
        return list(result.scalars().all())

    def add_event(
        self,
        memory: MemoryItem,
        event_type: MemoryEventType,
        actor_subject_id: UUID,
        reason: str | None = None,
    ) -> None:
        self.session.add(
            MemoryEvent(
                memory=memory,
                event_type=event_type,
                actor_subject_id=actor_subject_id,
                reason=reason,
                snapshot=self.snapshot(memory),
            )
        )

    @staticmethod
    def snapshot(memory: MemoryItem) -> dict[str, object]:
        return {
            "id": str(memory.id),
            "subject_id": str(memory.subject_id),
            "memory_type": memory.memory_type,
            "value": memory.value,
            "text": memory.text,
            "source_type": memory.source_type.value,
            "status": memory.status.value,
            "sensitivity": memory.sensitivity.value,
            "confidence": memory.confidence,
            "allowed_purposes": memory.allowed_purposes,
            "valid_from": memory.valid_from.isoformat(),
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
