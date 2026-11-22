from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    MemoryEventType,
    MemoryItem,
    MemoryPermission,
    MemoryStatus,
    MemorySubject,
)
from app.memory.errors import (
    MemoryNotFoundError,
    MemoryPermissionError,
    MemoryProhibitedUseError,
    MemoryTransitionError,
)
from app.memory.repository import MemoryRepository
from app.memory.schemas import (
    MemoryCorrection,
    MemoryPermissionUpsert,
    MemoryProposal,
    MemoryRetrieveRequest,
)


PROHIBITED_PURPOSES = {
    "credit_decision",
    "differential_pricing",
    "eligibility_scoring",
    "employment_decision",
    "housing_decision",
    "insurance_decision",
    "pressure_targeting",
}


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = MemoryRepository(session)

    async def actor_for_external_id(self, external_id: str) -> MemorySubject:
        actor = await self.repository.get_or_create_person(external_id)
        await self.session.commit()
        await self.session.refresh(actor)
        return actor

    async def propose(
        self, actor: MemorySubject, payload: MemoryProposal
    ) -> MemoryItem:
        self._validate_purposes(payload.allowed_purposes)
        now = datetime.now(UTC)
        memory = MemoryItem(
            subject_id=actor.id,
            memory_type=payload.memory_type,
            value=payload.value,
            text=payload.text,
            source_type=payload.source_type,
            source_conversation_id=payload.source_conversation_id,
            status=MemoryStatus.PROPOSED,
            sensitivity=payload.sensitivity,
            confidence=payload.confidence,
            allowed_purposes=payload.allowed_purposes,
            valid_from=payload.valid_from or now,
            expires_at=payload.expires_at,
        )
        self.session.add(memory)
        await self.session.flush()
        self.repository.add_event(memory, MemoryEventType.PROPOSED, actor.id)
        await self.session.commit()
        await self.session.refresh(memory)
        return memory

    async def confirm(self, actor: MemorySubject, memory_id: UUID) -> MemoryItem:
        memory = await self._owned_memory(actor, memory_id)
        self._require_status(memory, MemoryStatus.PROPOSED)
        memory.status = MemoryStatus.CONFIRMED
        memory.confirmed_at = datetime.now(UTC)
        # A proposed replacement supersedes the memory it corrects once confirmed.
        if memory.supersedes_memory_id is not None:
            previous = await self.repository.get_memory(memory.supersedes_memory_id, actor.id)
            if previous is not None and previous.status == MemoryStatus.CONFIRMED:
                previous.status = MemoryStatus.SUPERSEDED
                self.repository.add_event(previous, MemoryEventType.SUPERSEDED, actor.id)
        self.repository.add_event(memory, MemoryEventType.CONFIRMED, actor.id)
        await self.session.commit()
        await self.session.refresh(memory)
        return memory

    async def reject(
        self, actor: MemorySubject, memory_id: UUID, reason: str | None
    ) -> MemoryItem:
        memory = await self._owned_memory(actor, memory_id)
        self._require_status(memory, MemoryStatus.PROPOSED)
        memory.status = MemoryStatus.REJECTED
        self.repository.add_event(memory, MemoryEventType.REJECTED, actor.id, reason)
        await self.session.commit()
        await self.session.refresh(memory)
        return memory

    async def correct(
        self,
        actor: MemorySubject,
        memory_id: UUID,
        payload: MemoryCorrection,
    ) -> MemoryItem:
        previous = await self._owned_memory(actor, memory_id)
        self._require_status(previous, MemoryStatus.CONFIRMED)
        self._validate_purposes(payload.allowed_purposes)
        now = datetime.now(UTC)
        replacement = MemoryItem(
            subject_id=actor.id,
            memory_type=payload.memory_type,
            value=payload.value,
            text=payload.text,
            source_type=payload.source_type,
            source_conversation_id=payload.source_conversation_id,
            status=MemoryStatus.CONFIRMED,
            sensitivity=payload.sensitivity,
            confidence=payload.confidence,
            allowed_purposes=payload.allowed_purposes,
            valid_from=payload.valid_from or now,
            expires_at=payload.expires_at,
            supersedes_memory_id=previous.id,
            confirmed_at=now,
        )
        previous.status = MemoryStatus.SUPERSEDED
        self.session.add(replacement)
        await self.session.flush()
        self.repository.add_event(
            previous, MemoryEventType.SUPERSEDED, actor.id, payload.reason
        )
        self.repository.add_event(
            replacement, MemoryEventType.CORRECTED, actor.id, payload.reason
        )
        await self.session.commit()
        await self.session.refresh(replacement)
        return replacement

    async def forget(self, actor: MemorySubject, memory_id: UUID) -> None:
        memory = await self._owned_memory(actor, memory_id)
        if memory.status == MemoryStatus.DELETED:
            return
        memory.status = MemoryStatus.DELETED
        memory.deleted_at = datetime.now(UTC)
        self.repository.add_event(memory, MemoryEventType.DELETED, actor.id)
        await self.session.commit()

    async def list_memories(
        self,
        actor: MemorySubject,
        status: MemoryStatus | None,
        include_deleted: bool,
    ) -> list[MemoryItem]:
        return await self.repository.list_memories(actor.id, status, include_deleted)

    async def set_permission(
        self,
        actor: MemorySubject,
        data_category: str,
        purpose: str,
        payload: MemoryPermissionUpsert,
    ) -> MemoryPermission:
        if payload.allowed:
            self._validate_purposes([purpose])
        now = datetime.now(UTC)
        permission = await self.repository.get_permission(
            actor.id, data_category, purpose
        )
        if permission is None:
            permission = MemoryPermission(
                subject_id=actor.id,
                data_category=data_category,
                purpose=purpose,
                allowed=payload.allowed,
                granularity=payload.granularity,
                retention_days=payload.retention_days,
                sharing_scope=payload.sharing_scope,
                user_control=payload.user_control,
                confirmed_at=now,
                revoked_at=None if payload.allowed else now,
            )
            self.session.add(permission)
        else:
            permission.allowed = payload.allowed
            permission.granularity = payload.granularity
            permission.retention_days = payload.retention_days
            permission.sharing_scope = payload.sharing_scope
            permission.user_control = payload.user_control
            permission.confirmed_at = now
            permission.revoked_at = None if payload.allowed else now
        await self.session.commit()
        await self.session.refresh(permission)
        return permission

    async def list_permissions(
        self, actor: MemorySubject
    ) -> list[MemoryPermission]:
        return await self.repository.list_permissions(actor.id)

    async def retrieve(
        self, actor: MemorySubject, request: MemoryRetrieveRequest
    ) -> list[MemoryItem]:
        self._validate_purposes([request.purpose])
        candidates = await self.repository.list_confirmed_candidates(
            actor.id,
            request.memory_types,
            candidate_limit=max(request.limit * 5, 100),
        )
        query = request.query.casefold() if request.query else None
        now = datetime.now(UTC)
        results: list[MemoryItem] = []
        for memory in candidates:
            if request.purpose not in memory.allowed_purposes:
                continue
            if self._is_expired(memory, now):
                continue
            permission = await self.repository.get_permission(
                actor.id, memory.memory_type, request.purpose
            )
            if permission is None or not permission.allowed or permission.revoked_at:
                continue
            if self._permission_expired(permission, memory, now):
                continue
            if query and query not in memory.text.casefold():
                continue
            results.append(memory)
            if len(results) >= request.limit:
                break
        return results

    async def _owned_memory(
        self, actor: MemorySubject, memory_id: UUID
    ) -> MemoryItem:
        memory = await self.repository.get_memory(memory_id, actor.id)
        if memory is None:
            raise MemoryNotFoundError("Memory was not found for this person.")
        return memory

    @staticmethod
    def _require_status(memory: MemoryItem, expected: MemoryStatus) -> None:
        if memory.status != expected:
            raise MemoryTransitionError(
                f"Memory must be {expected.value}; current status is {memory.status.value}."
            )

    @staticmethod
    def _validate_purposes(purposes: list[str]) -> None:
        prohibited = sorted(set(purposes) & PROHIBITED_PURPOSES)
        if prohibited:
            raise MemoryProhibitedUseError(
                f"Prohibited memory purpose: {', '.join(prohibited)}."
            )

    @staticmethod
    def _is_expired(memory: MemoryItem, now: datetime) -> bool:
        if memory.expires_at is None:
            return False
        expires_at = memory.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= now

    @staticmethod
    def _permission_expired(
        permission: MemoryPermission, memory: MemoryItem, now: datetime
    ) -> bool:
        if permission.retention_days is None:
            return False
        created_at = memory.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at + timedelta(days=permission.retention_days) <= now
