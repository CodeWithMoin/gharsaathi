"""Automatic, bounded Zepto saved-address refresh on PostgreSQL snapshots.

The Zepto source is a local fake; no provider network call is possible.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.commerce.provider_state import ProviderAddressRepository, cached_address_list
from app.commerce.swiggy.models import Address, AddressList, AddressResolution, Pagination
from app.commerce.zepto.address_refresh import ZeptoAddressRefresher
from app.dependencies import (
    get_swiggy_service,
    get_zepto_address_refresher,
    get_zepto_service,
)
from app.memory.service import MemoryService
from app.shopping.models import BasketStatus
from tests.integration.helpers import as_user
from tests.shopping_fakes import World, line


USER = "zepto-owner"


def _addresses(*ids: str) -> AddressList:
    return AddressList(
        addresses=[Address(id=item, tag=item.title(), address_line=f"{item} street") for item in ids],
        pagination=Pagination(page=1, page_size=10, total=len(ids), total_pages=1),
        resolution=AddressResolution(
            default_address_id=ids[0] if len(ids) == 1 else None,
            resolution_source="zepto_saved_addresses",
        ),
    )


class FakeZepto:
    def __init__(self, *ids: str, delay: float = 0.0) -> None:
        self.next = _addresses(*ids)
        self.calls = 0
        self.delay = delay
        self.removed: list[str] = []

    async def addresses(self) -> AddressList:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.next

    def mark_address_removed(self, address_id: str) -> None:
        self.removed.append(address_id)


class Clock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value


async def _seed(
    sessions: async_sessionmaker, addresses: AddressList, selected: str, age_minutes: float
) -> None:
    async with sessions() as session:
        actor = await MemoryService(session).actor_for_external_id(USER)
        repository = ProviderAddressRepository(session)
        state = await repository.save(actor.id, addresses, "zepto")
        state.selected_address_id = selected
        state.refreshed_at = datetime.now(UTC) - timedelta(minutes=age_minutes)
        await session.commit()


async def _state(sessions: async_sessionmaker) -> AddressList:
    async with sessions() as session:
        actor = await MemoryService(session).actor_for_external_id(USER)
        state = await ProviderAddressRepository(session).get(actor.id, "zepto")
        assert state is not None
        return cached_address_list(state)


def _refresher(source: FakeZepto, sessions: async_sessionmaker, world: World | None = None, **kwargs) -> ZeptoAddressRefresher:  # type: ignore[no-untyped-def]
    return ZeptoAddressRefresher(
        source, sessions, world.baskets if world else None, max_age_seconds=30 * 60, **kwargs
    )


async def test_stale_snapshot_is_refreshed_at_session_start(session_factory) -> None:
    await _seed(session_factory, _addresses("home", "office"), "home", age_minutes=45)
    source = FakeZepto("home", "office", "parents")

    outcome = await _refresher(source, session_factory).refresh_if_stale(USER, reason="voice_start")

    assert source.calls == 1
    assert outcome is not None and outcome.selection_removed is False
    saved = await _state(session_factory)
    assert [item.id for item in saved.addresses] == ["home", "office", "parents"]
    assert saved.resolution.default_address_id == "home"


async def test_fresh_snapshot_and_missing_snapshot_are_not_refreshed(session_factory) -> None:
    source = FakeZepto("home")
    refresher = _refresher(source, session_factory)
    # No snapshot yet: the first load stays the user's explicit tap.
    assert await refresher.refresh_if_stale(USER, reason="voice_start") is None
    await _seed(session_factory, _addresses("home"), "home", age_minutes=10)
    assert await refresher.refresh_if_stale(USER, reason="voice_start") is None
    assert source.calls == 0


async def test_picker_open_uses_the_five_minute_bound(session_factory) -> None:
    await _seed(session_factory, _addresses("home"), "home", age_minutes=6)
    source = FakeZepto("home", "office")

    outcome = await _refresher(source, session_factory).refresh_if_stale(
        USER, max_age_seconds=5 * 60, reason="auto_picker"
    )

    assert source.calls == 1
    assert outcome is not None and len(outcome.addresses.addresses) == 2


async def test_refreshes_are_debounced_and_never_concurrent(session_factory) -> None:
    await _seed(session_factory, _addresses("home"), "home", age_minutes=45)
    source = FakeZepto("home", delay=0.05)
    clock = Clock()
    refresher = _refresher(source, session_factory, clock=clock)

    first, second = await asyncio.gather(
        refresher.refresh_if_stale(USER, reason="a"), refresher.refresh_if_stale(USER, reason="b")
    )
    assert source.calls == 1
    assert first is not None and second is not None

    # Stale again, but within two minutes of the last start: no new call.
    await _seed(session_factory, _addresses("home"), "home", age_minutes=45)
    clock.value += 60
    assert await refresher.refresh_if_stale(USER, reason="c") is None
    assert source.calls == 1
    clock.value += 61
    assert await refresher.refresh_if_stale(USER, reason="d") is not None
    assert source.calls == 2


async def test_slow_refresh_does_not_block_and_lands_later(session_factory) -> None:
    await _seed(session_factory, _addresses("home"), "home", age_minutes=45)
    source = FakeZepto("home", "office", delay=0.3)
    refresher = _refresher(source, session_factory, wait_seconds=0.05)

    assert await refresher.refresh_if_stale(USER, reason="chat_turn") is None
    await asyncio.sleep(0.5)
    assert len((await _state(session_factory)).addresses) == 2


async def test_deleted_selection_is_cleared_not_replaced_and_the_bill_invalidated(session_factory) -> None:
    await _seed(session_factory, _addresses("hackathon", "home"), "hackathon", age_minutes=45)
    world = World()
    world.provider.name = "zepto"
    item = line()
    item.ref = item.ref.model_copy(update={"provider": "zepto"})
    await world.baskets.add_line(USER, "zepto", item)
    await world.baskets.set_address(USER, "zepto", "hackathon")
    basket = await world.baskets.active(USER, "zepto")
    request = await world.approvals.request(basket.id)
    assert (await world.baskets.active(USER, "zepto")).status == BasketStatus.AWAITING_APPROVAL

    # Only one address is left after the deletion: it is still not chosen for the user.
    source = FakeZepto("home")
    outcome = await _refresher(source, session_factory, world).refresh_if_stale(USER, reason="voice_start")

    assert outcome is not None and outcome.selection_removed is True
    saved = await _state(session_factory)
    assert saved.selection_removed is True
    assert saved.resolution.default_address_id is None
    assert source.removed == ["hackathon"]
    after = await world.baskets.active(USER, "zepto")
    assert after.address_id is None
    assert after.status == BasketStatus.DRAFT
    assert len(after.lines) == 1
    assert after.revision > request.approval.basket_revision

    # The user's own choice clears the notice.
    async with session_factory() as session:
        actor = await MemoryService(session).actor_for_external_id(USER)
        await ProviderAddressRepository(session).select(actor.id, "home", "zepto")
    chosen = await _state(session_factory)
    assert chosen.selection_removed is False
    assert chosen.resolution.default_address_id == "home"


async def test_search_detected_missing_address_updates_the_snapshot(session_factory) -> None:
    await _seed(session_factory, _addresses("hackathon", "home"), "hackathon", age_minutes=1)
    source = FakeZepto("home")

    await _refresher(source, session_factory).address_missing(_addresses("home"), "hackathon")

    saved = await _state(session_factory)
    assert saved.selection_removed is True
    assert [item.id for item in saved.addresses] == ["home"]
    assert source.calls == 0


class SwiggyCounter:
    def __init__(self) -> None:
        self.calls = 0

    async def addresses(self, page: int, page_size: int) -> AddressList:
        self.calls += 1
        return _addresses("sw-home")


def test_picker_route_refreshes_zepto_and_never_swiggy(api: TestClient, override, session_factory) -> None:
    source = FakeZepto("home", "office")
    swiggy = SwiggyCounter()
    refresher = _refresher(source, session_factory)
    override(get_zepto_service, lambda: source)
    override(get_swiggy_service, lambda: swiggy)
    override(get_zepto_address_refresher, lambda: refresher)
    headers = as_user(USER)

    asyncio.run(_seed(session_factory, _addresses("home"), "home", age_minutes=6))
    picked = api.get("/api/zepto/addresses", headers=headers, params={"auto": "picker"}).json()
    assert [item["id"] for item in picked["addresses"]] == ["home", "office"]
    assert source.calls == 1

    # Swiggy stays manual: an ordinary load never calls it, whatever the snapshot age.
    api.get("/api/swiggy/addresses", headers=headers)
    assert swiggy.calls == 0
