from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.commerce.swiggy.models import Address, AddressList
from app.database.models import MemorySubject, ProviderAddressState, SubjectType


SWIGGY_PROVIDER = "swiggy_instamart"
DEMO_RESOLUTION_SOURCE = "demo_fixture"
# Stores whose vanished selection is cleared for the user to pick again, instead
# of falling back to the provider default (Zepto snapshots refresh automatically).
ASK_AGAIN_PROVIDERS = frozenset({"zepto"})


def is_demo_address_list(addresses: AddressList) -> bool:
    """Return true for local fixtures that must never enter live account state."""

    return (
        addresses.resolution.resolution_source == DEMO_RESOLUTION_SOURCE
        or any(address.id.startswith("demo-") for address in addresses.addresses)
    )


def is_demo_address_state(state: ProviderAddressState) -> bool:
    return is_demo_address_list(AddressList.model_validate(state.snapshot))


class ProviderAddressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, subject_id: UUID, provider: str = SWIGGY_PROVIDER
    ) -> ProviderAddressState | None:
        result = await self.session.execute(
            select(ProviderAddressState).where(
                ProviderAddressState.subject_id == subject_id,
                ProviderAddressState.provider == provider,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self,
        subject_id: UUID,
        addresses: AddressList,
        provider: str = SWIGGY_PROVIDER,
    ) -> ProviderAddressState:
        if is_demo_address_list(addresses):
            raise ValueError("Demo addresses cannot be persisted as provider state.")
        state = await self.get(subject_id, provider)
        now = datetime.now(UTC)
        snapshot = addresses.model_dump(
            mode="json", exclude={"cached", "needs_refresh", "refreshed_at", "selection_removed"}
        )
        address_ids = {address.id for address in addresses.addresses}
        provider_default = addresses.resolution.default_address_id
        if state is None:
            state = ProviderAddressState(
                subject_id=subject_id,
                provider=provider,
                snapshot=snapshot,
                selected_address_id=(
                    provider_default if provider_default in address_ids else None
                ),
                refreshed_at=now,
            )
            self.session.add(state)
        else:
            previous = state.selected_address_id
            removed = bool(state.snapshot.get("selection_removed")) and previous is None
            if (
                provider in ASK_AGAIN_PROVIDERS
                and previous is not None
                and previous not in address_ids
            ):
                # Never switch to another address on the user's behalf.
                state.selected_address_id = None
                removed = True
            elif previous not in address_ids and not removed:
                state.selected_address_id = (
                    provider_default if provider_default in address_ids else None
                )
            state.snapshot = {**snapshot, "selection_removed": removed}
            state.refreshed_at = now
        await self.session.commit()
        await self.session.refresh(state)
        return state

    async def select(
        self,
        subject_id: UUID,
        address_id: str,
        provider: str = SWIGGY_PROVIDER,
    ) -> ProviderAddressState | None:
        state = await self.get(subject_id, provider)
        if state is None:
            return None
        addresses = AddressList.model_validate(state.snapshot)
        if is_demo_address_list(addresses):
            return None
        if address_id not in {address.id for address in addresses.addresses}:
            return None
        state.selected_address_id = address_id
        if state.snapshot.get("selection_removed"):
            state.snapshot = {**state.snapshot, "selection_removed": False}
        await self.session.commit()
        await self.session.refresh(state)
        return state

    async def clear(
        self, subject_id: UUID, provider: str = SWIGGY_PROVIDER
    ) -> None:
        await self.session.execute(
            delete(ProviderAddressState).where(
                ProviderAddressState.subject_id == subject_id,
                ProviderAddressState.provider == provider,
            )
        )
        await self.session.commit()


def cached_address_list(state: ProviderAddressState) -> AddressList:
    addresses = AddressList.model_validate(state.snapshot)
    addresses.cached = True
    addresses.needs_refresh = False
    addresses.refreshed_at = state.refreshed_at
    if state.selected_address_id is not None:
        addresses.selection_removed = False
        addresses.resolution.default_address_id = state.selected_address_id
        addresses.resolution.resolution_source = "saved_selection"
    elif addresses.selection_removed:
        # Even a single remaining address waits for the user's tap.
        addresses.resolution.default_address_id = None
        addresses.resolution.needs_user_clarification = True
        addresses.resolution.resolution_source = "selection_removed"
    return addresses


SHORT_ADDRESS_LENGTH = 60


@dataclass(frozen=True)
class SavedAddressSummary:
    """A display summary of one saved provider address, for the voice agent."""

    label: str | None
    short_address: str


def summarise_address(address: Address) -> SavedAddressSummary:
    """Label plus the leading part of the provider's own address line."""

    label = next(
        (value.strip() for value in (address.tag, address.category) if value and value.strip()),
        None,
    )
    line = " ".join(address.address_line.split())
    if len(line) > SHORT_ADDRESS_LENGTH:
        cut = line[:SHORT_ADDRESS_LENGTH]
        comma = cut.rfind(",")
        line = (cut[:comma] if comma > SHORT_ADDRESS_LENGTH // 2 else cut).rstrip(" ,") + "…"
    return SavedAddressSummary(label=label, short_address=line)


class SavedAddressDirectory:
    """Read-only lookup of a user's stored provider address snapshot.

    Only addresses present in the server-side snapshot are described; an
    unknown, demo or unsnapshotted address yields ``None`` rather than a guess.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def describe(
        self, external_user_id: str, provider: str, address_id: str
    ) -> SavedAddressSummary | None:
        async with self.sessions() as session:
            subject_id = (
                await session.execute(
                    select(MemorySubject.id).where(
                        MemorySubject.subject_type == SubjectType.PERSON,
                        MemorySubject.external_id == external_user_id,
                    )
                )
            ).scalar_one_or_none()
            if subject_id is None:
                return None
            state = await ProviderAddressRepository(session).get(subject_id, provider)
            if state is None:
                return None
            addresses = AddressList.model_validate(state.snapshot)
        if is_demo_address_list(addresses):
            return None
        match = next((item for item in addresses.addresses if item.id == address_id), None)
        return summarise_address(match) if match is not None else None


class SavedAddressBook:
    """Saved addresses per store from the server-side snapshot; never a provider call.

    ``select`` is the same repository write as ``PUT /api/{store}/addresses/selected``
    and accepts only an id in the user's own snapshot.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @staticmethod
    async def _subject(session: AsyncSession, external_user_id: str) -> UUID | None:
        return (
            await session.execute(
                select(MemorySubject.id).where(
                    MemorySubject.subject_type == SubjectType.PERSON,
                    MemorySubject.external_id == external_user_id,
                )
            )
        ).scalar_one_or_none()

    async def saved(self, external_user_id: str, provider: str) -> AddressList | None:
        async with self.sessions() as session:
            subject_id = await self._subject(session, external_user_id)
            if subject_id is None:
                return None
            state = await ProviderAddressRepository(session).get(subject_id, provider)
            if state is None or is_demo_address_state(state):
                return None
            return cached_address_list(state)

    async def select(
        self, external_user_id: str, provider: str, address_id: str
    ) -> AddressList | None:
        async with self.sessions() as session:
            subject_id = await self._subject(session, external_user_id)
            if subject_id is None:
                return None
            state = await ProviderAddressRepository(session).select(subject_id, address_id, provider)
            return cached_address_list(state) if state is not None else None
