"""Per-call database sessions for the voice/typed memory tools, by verified identity.

The household id always comes from the verified session, never from model
arguments. Results are small plain views; memory ids stay server-side behind
session references issued by the tool executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import MemoryItem, MemoryStatus, MemorySubject
from app.memory.choice_signals import record_choice
from app.memory.household import (
    PREFERENCE_KINDS,
    PreferenceSpec,
    display_value_of,
    group_of,
    item_of,
    kind_of,
    origin_of,
)
from app.memory.household_service import HouseholdPreferenceService
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService


@dataclass(frozen=True)
class MemoryView:
    id: UUID
    kind: str | None
    group: str | None
    summary: str
    status: str
    origin: str
    undoable: bool
    item: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class WriteResult:
    status: Literal["saved", "proposed", "already_saved", "replaced", "pending_update"]
    memory: MemoryView
    replaced: MemoryView | None = None


@dataclass(frozen=True)
class ChoiceRecord:
    occasion_key: str
    provider: str
    item: str
    product_name: str
    brand: str | None
    pack_size: str | None
    competing_providers: list[str]


def view(memory: MemoryItem) -> MemoryView:
    return MemoryView(
        id=memory.id,
        kind=kind_of(memory),
        group=group_of(memory),
        summary=memory.text[:160],
        status=memory.status.value,
        origin=origin_of(memory),
        undoable=HouseholdPreferenceService.undoable(memory),
        item=item_of(memory),
        value=display_value_of(memory),
    )


class HouseholdPreferenceWriter(Protocol):
    async def consent_on(self, household_id: str) -> bool: ...
    async def remember(
        self, household_id: str, spec: PreferenceSpec, stated: bool
    ) -> WriteResult: ...
    async def update(
        self, household_id: str, memory_id: UUID | None, spec: PreferenceSpec, stated: bool
    ) -> WriteResult | None: ...
    async def decide(self, household_id: str, memory_id: UUID, confirm: bool) -> MemoryView: ...
    async def forget(self, household_id: str, memory_id: UUID) -> MemoryView | None: ...
    async def forget_all_token(self, household_id: str) -> tuple[str, int]: ...
    async def forget_all(self, household_id: str, token: str) -> int: ...
    async def list(self, household_id: str) -> list[MemoryView]: ...
    async def record_choice(self, household_id: str, choice: ChoiceRecord) -> None: ...
    async def current_conversation(self, household_id: str) -> str | None: ...


class DatabaseHouseholdPreferences:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    async def _actor(session: AsyncSession, household_id: str) -> MemorySubject:
        return await MemoryService(session).actor_for_external_id(household_id)

    async def consent_on(self, household_id: str) -> bool:
        async with self.session_factory() as session:
            actor = await MemoryRepository(session).find_person(household_id)
            return actor is not None and await HouseholdPreferenceService(session).consent_on(actor)

    async def remember(self, household_id: str, spec: PreferenceSpec, stated: bool) -> WriteResult:
        async with self.session_factory() as session:
            actor = await self._actor(session, household_id)
            service = HouseholdPreferenceService(session)
            if stated:
                previous = await service.find_active(actor, spec)
                memory, created = await service.save_stated(actor, spec)
                if not created:
                    return WriteResult("already_saved", view(memory))
                if previous is not None and memory.supersedes_memory_id == previous.id:
                    return WriteResult("replaced", view(memory), view(previous))
                return WriteResult("saved", view(memory))
            memory, created = await service.propose(actor, spec, origin="explicit")
            if not created:
                status = "already_saved" if memory.status == MemoryStatus.CONFIRMED else "proposed"
                return WriteResult(status, view(memory))
            return WriteResult("proposed", view(memory))

    async def update(
        self, household_id: str, memory_id: UUID | None, spec: PreferenceSpec, stated: bool
    ) -> WriteResult | None:
        async with self.session_factory() as session:
            actor = await self._actor(session, household_id)
            service = HouseholdPreferenceService(session)
            previous: MemoryItem | None
            if memory_id is not None:
                previous = await MemoryRepository(session).get_memory(memory_id, actor.id)
                if previous is None or previous.status != MemoryStatus.CONFIRMED:
                    previous = None
            else:
                previous = await service.find_active(actor, spec)
            if previous is None:
                return None
            old = view(previous)
            if stated:
                memory = await service.replace(actor, previous, spec, origin="stated")
                return WriteResult("replaced", view(memory), old)
            memory, created = await service.propose(actor, spec, supersedes=previous)
            if not created and memory.status == MemoryStatus.CONFIRMED:
                return WriteResult("already_saved", view(memory))
            return WriteResult("pending_update", view(memory), old)

    async def decide(self, household_id: str, memory_id: UUID, confirm: bool) -> MemoryView:
        async with self.session_factory() as session:
            actor = await self._actor(session, household_id)
            service = HouseholdPreferenceService(session)
            memory = (
                await service.confirm(actor, memory_id)
                if confirm
                else await service.reject(actor, memory_id, "Declined in conversation.")
            )
            return view(memory)

    async def forget(self, household_id: str, memory_id: UUID) -> MemoryView | None:
        """Forget one memory; a just-learned one is undone (restoring what it replaced)."""
        async with self.session_factory() as session:
            actor = await self._actor(session, household_id)
            service = HouseholdPreferenceService(session)
            memory = await MemoryRepository(session).get_memory(memory_id, actor.id)
            if memory is not None and service.undoable(memory):
                restored = await service.undo(actor, memory_id)
                return view(restored) if restored is not None else None
            await service.forget(actor, memory_id)
            return None

    async def forget_all_token(self, household_id: str) -> tuple[str, int]:
        async with self.session_factory() as session:
            actor = await self._actor(session, household_id)
            confirmation = await HouseholdPreferenceService(session).forget_all_confirmation(actor)
            return confirmation.token, confirmation.count

    async def forget_all(self, household_id: str, token: str) -> int:
        async with self.session_factory() as session:
            actor = await self._actor(session, household_id)
            return await HouseholdPreferenceService(session).forget_all_confirmed(actor, token)

    async def list(self, household_id: str) -> list[MemoryView]:
        async with self.session_factory() as session:
            actor = await MemoryRepository(session).find_person(household_id)
            if actor is None:
                return []
            service = HouseholdPreferenceService(session)
            memories = await service.memories(actor, {MemoryStatus.CONFIRMED, MemoryStatus.PROPOSED})
            return [view(memory) for memory in memories if kind_of(memory) in PREFERENCE_KINDS or group_of(memory)]

    async def record_choice(self, household_id: str, choice: ChoiceRecord) -> None:
        async with self.session_factory() as session:
            actor = await MemoryRepository(session).find_person(household_id)
            if actor is None or not await HouseholdPreferenceService(session).consent_on(actor):
                return
            await record_choice(
                session,
                actor,
                occasion_key=choice.occasion_key,
                provider=choice.provider,
                item=choice.item,
                product_name=choice.product_name,
                brand=choice.brand,
                pack_size=choice.pack_size,
                competing_providers=choice.competing_providers,
            )

    async def current_conversation(self, household_id: str) -> str | None:
        from app.conversations.repository import ConversationRepository

        async with self.session_factory() as session:
            actor = await MemoryRepository(session).find_person(household_id)
            if actor is None:
                return None
            conversation = await ConversationRepository(session).current(actor.id)
            return str(conversation.id) if conversation is not None else None
