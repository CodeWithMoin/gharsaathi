from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.swiggy.models import AddressList
from app.database.models import ProviderAddressState


SWIGGY_PROVIDER = "swiggy_instamart"


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
        state = await self.get(subject_id, provider)
        now = datetime.now(UTC)
        snapshot = addresses.model_dump(mode="json", exclude={"cached", "needs_refresh", "refreshed_at"})
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
            state.snapshot = snapshot
            state.refreshed_at = now
            if state.selected_address_id not in address_ids:
                state.selected_address_id = (
                    provider_default if provider_default in address_ids else None
                )
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
        if address_id not in {address.id for address in addresses.addresses}:
            return None
        state.selected_address_id = address_id
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
        addresses.resolution.default_address_id = state.selected_address_id
        addresses.resolution.resolution_source = "saved_selection"
    return addresses
