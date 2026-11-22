"""Conservative purchase-history import: call counts, spacing, resume, cutoffs, gating.

Every provider is an in-memory fake driven by a fake clock; nothing touches the network.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commerce.swiggy.errors import SwiggyRateLimited
from app.commerce.swiggy.history import SwiggyOrderHistoryAdapter
from app.commerce.zepto.errors import (
    ZeptoRateLimited,
    ZeptoSchemaError,
    ZeptoSessionExpired,
)
from app.commerce.zepto.history import parse_order_detail, parse_order_history
from app.commerce.zepto.purchase_history import ZeptoPurchaseHistoryAdapter
from app.database.base import Base
from app.database.models import (
    MemoryPermission,
    MemorySubject,
    PurchaseHistorySyncState,
    PurchaseOrderNode,
    SubjectType,
)
from app.memory.errors import MemoryPermissionError
from app.memory.purchase_graph import (
    FullResyncUnavailable,
    HistoryCooldown,
    PurchaseGraphService,
)

BASE = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def now(self) -> datetime:
        return BASE + timedelta(seconds=self.t)

    def advance(self, delta: timedelta) -> None:
        self.t += delta.total_seconds()


def _summary(order_id: str, status: str = "Delivered") -> dict[str, object]:
    return {
        "id": order_id,
        "code": f"C-{order_id}",
        "formattedStatus": status,
        "grandTotalAmount": 6900,
        "itemQuantityCount": 1,
        "productsNamesAndCounts": [{"name": "Milk", "count": 1}],
        "totalArrivalTimeInMinutes": 12,
        "totalRefundAmount": 0,
    }


def _detail(order_id: str, placed: datetime) -> dict[str, object]:
    stamp = placed.isoformat().replace("+00:00", "Z")
    return {
        "id": order_id,
        "orderId": f"provider-{order_id}",
        "code": f"C-{order_id}",
        "formattedStatus": "Delivered",
        "orderType": "INSTANT",
        "placedTime": stamp,
        "userAddressFormatted": "redacted",
        "itemQuantityCount": 1,
        "grandTotalAmount": 6900,
        "grandTotalAmountPreDiscount": 7200,
        "totalRefundAmount": 0,
        "walletBalanceUsed": 0,
        "isInvoiceAvailable": False,
        "invoiceUrl": None,
        "isEligibleForRating": False,
        "billSummary": {"itemTotal": 6900, "totalBill": 6900, "totalSaved": 300},
        "shipments": [
            {
                "shipmentId": "s1",
                "shipmentCode": "S1",
                "status": "DELIVERED",
                "formattedStatus": "Delivered",
                "placedTime": stamp,
                "deliveryTime": stamp,
                "arrivedTime": stamp,
                "committedEtaInMins": 15,
                "totalArrivalTimeInMinutes": 12,
                "shipmentWeightInGms": 1000,
                "products": [
                    {
                        "id": f"item-{order_id}",
                        "productName": "Taaza Milk",
                        "brand": "Amul",
                        "packsize": "1 L",
                        "imagePath": "https://example.test/milk.png",
                        "status": "DELIVERED",
                        "quantityOrdered": 1,
                        "quantityFulfilled": 1,
                        "quantityRejected": 0,
                        "unitMRP": 7200,
                        "unitSellingPrice": 6900,
                        "totalMRP": 7200,
                        "totalFinalSellingPrice": 6900,
                        "totalRefundAmount": 0,
                    }
                ],
            }
        ],
    }


class FakeZepto:
    """Newest-first order history with per-call timing and concurrency tracking."""

    def __init__(self, clock: FakeClock, orders: list[tuple[str, datetime]]) -> None:
        self.clock = clock
        self.orders = orders
        self.calls: list[tuple[float, str, object]] = []
        self.detail_errors: dict[str, Exception] = {}
        self.list_errors: dict[int, Exception] = {}
        self.in_flight = 0
        self.max_in_flight = 0

    async def _enter(self) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0)

    async def order_history(self, *, limit: int, page_number: int, recover: bool = True):
        assert recover is False, "history import must never use the recovering retry"
        self.calls.append((self.clock.t, "list", page_number))
        await self._enter()
        try:
            if page_number in self.list_errors:
                raise self.list_errors[page_number]
            chunk = self.orders[(page_number - 1) * limit : page_number * limit]
            return parse_order_history(
                {"count": len(self.orders), "orders": [_summary(oid) for oid, _ in chunk]},
                limit=limit,
                page_number=page_number,
            )
        finally:
            self.in_flight -= 1

    async def order_detail(self, order_id: str, *, recover: bool = True):
        assert recover is False
        self.calls.append((self.clock.t, "detail", order_id))
        await self._enter()
        try:
            if order_id in self.detail_errors:
                raise self.detail_errors[order_id]
            placed = dict(self.orders)[order_id]
            return parse_order_detail(_detail(order_id, placed))
        finally:
            self.in_flight -= 1

    def kinds(self) -> list[str]:
        return [kind for _, kind, _ in self.calls]


def _orders(count: int, *, start: int = 0, step_days: float = 1.0) -> list[tuple[str, datetime]]:
    return [
        (f"z{start + index}", BASE - timedelta(days=1 + index * step_days))
        for index in range(count)
    ]


def _assert_spaced(calls: list[tuple[float, str, object]], minimum: float) -> None:
    times = [t for t, _, _ in calls]
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    assert all(gap >= minimum for gap in gaps), gaps


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


async def _actor(db: AsyncSession, retention_days: int | None = 365) -> MemorySubject:
    actor = MemorySubject(subject_type=SubjectType.PERSON, external_id="person-1")
    db.add(actor)
    await db.flush()
    if retention_days is not None:
        db.add(
            MemoryPermission(
                subject_id=actor.id,
                data_category="purchase_history",
                purpose="proactive_reorder",
                allowed=True,
                retention_days=retention_days,
                sharing_scope="agent_and_user",
                user_control="view_edit_delete",
                confirmed_at=BASE,
            )
        )
    await db.commit()
    return actor


def _zepto_reader(service: FakeZepto, clock: FakeClock) -> ZeptoPurchaseHistoryAdapter:
    return ZeptoPurchaseHistoryAdapter(
        service, monotonic=clock.monotonic, sleep=clock.sleep  # type: ignore[arg-type]
    )


async def _state(db: AsyncSession, provider: str) -> PurchaseHistorySyncState:
    return (
        await db.execute(
            select(PurchaseHistorySyncState).where(PurchaseHistorySyncState.provider == provider)
        )
    ).scalar_one()


# --- Zepto -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zepto_three_pages_are_all_imported_sequentially_and_spaced(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    zepto = FakeZepto(clock, _orders(25))
    graph = PurchaseGraphService(session, clock=clock.now)

    with caplog.at_level(logging.INFO, logger="app.memory.purchase_graph"):
        result = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert result.imported_orders == 25
    assert result.fetched_orders == 25
    assert (result.list_calls, result.detail_calls) == (3, 25)
    assert [page for _, kind, page in zepto.calls if kind == "list"] == [1, 2, 3]
    assert result.stopped_reason == "complete" and result.complete is True
    assert result.coverage == "full_history"
    assert result.window_start == BASE - timedelta(days=25)
    assert result.window_end == BASE - timedelta(days=1)
    assert zepto.max_in_flight == 1
    _assert_spaced(zepto.calls, 1.0)
    assert (
        "event=purchase_history_sync provider=zepto list_calls=3 detail_calls=25 "
        "stopped_reason=complete"
    ) in caplog.text
    state = await _state(session, "zepto")
    assert state.backfill_complete is True and state.resume_page is None
    assert (state.fetched_order_count, state.imported_order_count) == (25, 25)


@pytest.mark.asyncio
async def test_zepto_detail_budget_records_progress_and_next_sync_resumes(
    session: AsyncSession,
) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    zepto = FakeZepto(clock, _orders(45))
    graph = PurchaseGraphService(session, clock=clock.now)

    first = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert first.detail_calls == 30
    assert first.list_calls == 4
    assert first.stopped_reason == "detail_budget" and first.complete is False
    state = await _state(session, "zepto")
    assert (state.resume_page, state.backfill_complete) == (4, False)

    zepto.calls.clear()
    clock.advance(timedelta(minutes=11))
    second = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert [page for _, kind, page in zepto.calls if kind == "list"] == [4, 5]
    assert second.detail_calls == 15
    assert second.imported_orders == 15
    assert second.complete is True
    total = (await session.execute(select(PurchaseOrderNode))).scalars().all()
    assert len(total) == 45


@pytest.mark.asyncio
async def test_zepto_incremental_sync_stops_at_first_known_order(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    zepto = FakeZepto(clock, _orders(5, start=10))
    graph = PurchaseGraphService(session, clock=clock.now)
    await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    zepto.orders = [("new-1", BASE - timedelta(hours=1)), ("new-2", BASE - timedelta(hours=2))] + zepto.orders
    zepto.calls.clear()
    clock.advance(timedelta(minutes=11))
    result = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert zepto.kinds() == ["list", "detail", "detail"]
    assert result.imported_orders == 2
    assert result.skipped_known == 1
    assert result.stopped_reason == "known"


@pytest.mark.asyncio
async def test_zepto_lookback_cutoff_stops_reading_older_orders(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session, retention_days=1000)
    ages = [10, 200, 400, 500]
    zepto = FakeZepto(clock, [(f"z{age}", BASE - timedelta(days=age)) for age in ages])
    graph = PurchaseGraphService(session, clock=clock.now)

    result = await graph.sync(
        actor, _zepto_reader(zepto, clock), provider="zepto", lookback_days=365
    )

    assert result.imported_orders == 2
    assert result.detail_calls == 3  # the first too-old detail ends the walk
    assert result.stopped_reason == "lookback" and result.complete is True


@pytest.mark.asyncio
async def test_zepto_retention_cutoff_never_imports_older_orders(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session, retention_days=30)
    ages = [5, 20, 40, 60]
    zepto = FakeZepto(clock, [(f"z{age}", BASE - timedelta(days=age)) for age in ages])
    graph = PurchaseGraphService(session, clock=clock.now)

    result = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert result.imported_orders == 2
    assert result.detail_calls == 3
    assert result.stopped_reason == "retention"
    stored = (await session.execute(select(PurchaseOrderNode.provider_order_id))).scalars().all()
    assert sorted(stored) == ["provider-z20", "provider-z5"]


@pytest.mark.asyncio
async def test_zepto_tolerates_a_malformed_detail_and_counts_it(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    zepto = FakeZepto(clock, _orders(4))
    zepto.detail_errors["z1"] = ZeptoSchemaError("bad detail")
    graph = PurchaseGraphService(session, clock=clock.now)

    result = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert result.detail_calls == 4
    assert result.detail_failures == 1
    assert result.imported_orders == 3
    assert (await _state(session, "zepto")).detail_failure_count == 1


@pytest.mark.asyncio
async def test_zepto_rate_limit_stops_at_once_keeps_progress_and_blocks_next_sync(
    session: AsyncSession,
) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    zepto = FakeZepto(clock, _orders(8))
    zepto.detail_errors["z2"] = ZeptoRateLimited("slow down")
    graph = PurchaseGraphService(session, clock=clock.now)

    result = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert zepto.kinds() == ["list", "detail", "detail", "detail"]  # no retry, no more calls
    assert result.imported_orders == 2
    assert result.stopped_reason == "rate_limited"
    assert result.error_code == "ZeptoRateLimited"
    state = await _state(session, "zepto")
    assert state.last_error_code == "ZeptoRateLimited"
    assert state.resume_page == 1

    calls = len(zepto.calls)
    clock.advance(timedelta(minutes=11))
    with pytest.raises(HistoryCooldown):
        await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")
    assert len(zepto.calls) == calls

    clock.advance(timedelta(hours=1))
    zepto.detail_errors.clear()
    resumed = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")
    assert resumed.imported_orders == 6
    assert resumed.skipped_known == 2


@pytest.mark.asyncio
async def test_zepto_session_error_on_first_list_raises_after_one_call(
    session: AsyncSession,
) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    zepto = FakeZepto(clock, _orders(3))
    zepto.list_errors[1] = ZeptoSessionExpired("expired")
    graph = PurchaseGraphService(session, clock=clock.now)

    with pytest.raises(ZeptoSessionExpired):
        await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    assert zepto.kinds() == ["list"]
    assert (await _state(session, "zepto")).last_error_code == "ZeptoSessionExpired"


@pytest.mark.asyncio
async def test_full_resync_skips_the_cache_once_per_24_hours(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    zepto = FakeZepto(clock, _orders(3))
    graph = PurchaseGraphService(session, clock=clock.now)
    await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")

    cached = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto")
    assert cached.cached is True

    zepto.calls.clear()
    full = await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto", full=True)
    assert full.cached is False and full.full is True
    assert zepto.kinds() == ["list"]  # known orders are not re-read in detail
    assert full.skipped_known == 3

    full_at = clock.now()
    clock.advance(timedelta(hours=2))
    with pytest.raises(FullResyncUnavailable) as refused:
        await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto", full=True)
    assert refused.value.available_at == full_at + timedelta(hours=24)
    zepto_status = next(s for s in await graph.sync_statuses(actor) if s.provider == "zepto")
    assert zepto_status.full_resync_available_at == full_at + timedelta(hours=24)

    clock.advance(timedelta(hours=22, minutes=1))
    assert (await graph.sync(actor, _zepto_reader(zepto, clock), provider="zepto", full=True)).full


# --- Swiggy ------------------------------------------------------------------


def _swiggy_order(order_id: str, days_ago: float) -> dict[str, object]:
    stamp = (BASE - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "orderId": order_id,
        "status": "DELIVERED",
        "createdAt": stamp,
        "updatedAt": stamp,
        "itemCount": 1,
        "totalAmount": 69,
        "orderType": "INSTAMART",
        "isActive": False,
        "currentStatus": "DELIVERED",
        "historyStatus": "COMPLETED",
        "items": [{"name": "Amul Taaza Milk 1 L", "quantity": 1, "itemId": "milk"}],
    }


class FakeSwiggyClient:
    def __init__(self, clock: FakeClock, pages: list[object], cooldown: int = 0) -> None:
        self.clock = clock
        self.pages = pages
        self.cooldown = cooldown
        self.calls: list[tuple[float, str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((self.clock.t, name, dict(arguments)))
        page = self.pages[len(self.calls) - 1]
        if isinstance(page, Exception):
            raise page
        return page  # type: ignore[return-value]

    async def cooldown_remaining(self) -> int:
        return self.cooldown


def _swiggy_reader(client: FakeSwiggyClient, clock: FakeClock) -> SwiggyOrderHistoryAdapter:
    return SwiggyOrderHistoryAdapter(
        client, monotonic=clock.monotonic, sleep=clock.sleep  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_swiggy_reads_instamart_20_and_follows_cursor_for_at_most_two_pages(
    session: AsyncSession,
) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    client = FakeSwiggyClient(
        clock,
        [
            {"data": {"orders": [_swiggy_order("s1", 1)], "hasMore": True, "nextCursor": "c1"}},
            {"data": {"orders": [_swiggy_order("s2", 5)], "hasMore": True}, "nextCursor": "c2"},
            {"data": {"orders": [_swiggy_order("s3", 9)], "hasMore": False}},
        ],
    )
    graph = PurchaseGraphService(session, clock=clock.now)

    result = await graph.sync(actor, _swiggy_reader(client, clock), provider="swiggy")

    assert [arguments for _, _, arguments in client.calls] == [
        {"orderType": "INSTAMART", "count": 20},
        {"orderType": "INSTAMART", "count": 20, "cursor": "c1"},
    ]
    _assert_spaced(client.calls, 2.0)
    assert clock.sleeps == [2.0]
    assert result.imported_orders == 2
    assert result.stopped_reason == "page_limit"
    assert result.coverage == "last_15_days"
    swiggy_status = next(s for s in await graph.sync_statuses(actor) if s.provider == "swiggy")
    assert swiggy_status.coverage == "last_15_days"


@pytest.mark.asyncio
async def test_swiggy_empty_history_is_one_call(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    client = FakeSwiggyClient(clock, [{"data": {"orders": [], "hasMore": False}}])
    graph = PurchaseGraphService(session, clock=clock.now)

    result = await graph.sync(actor, _swiggy_reader(client, clock), provider="swiggy")

    assert len(client.calls) == 1
    assert (result.imported_orders, result.fetched_orders) == (0, 0)
    assert result.window_start is None and result.stopped_reason == "complete"


@pytest.mark.asyncio
async def test_swiggy_429_is_never_retried_and_records_the_cooldown(
    session: AsyncSession,
) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    client = FakeSwiggyClient(clock, [SwiggyRateLimited("quota", 120)])
    graph = PurchaseGraphService(session, clock=clock.now)

    with pytest.raises(SwiggyRateLimited):
        await graph.sync(actor, _swiggy_reader(client, clock), provider="swiggy")

    assert len(client.calls) == 1
    state = await _state(session, "swiggy")
    assert state.last_error_code == "SwiggyRateLimited"
    assert PurchaseGraphService._as_utc(state.rate_limited_until) == BASE + timedelta(seconds=120)


@pytest.mark.asyncio
async def test_swiggy_429_on_second_page_keeps_the_first(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    client = FakeSwiggyClient(
        clock,
        [
            {"data": {"orders": [_swiggy_order("s1", 1)], "nextCursor": "c1"}},
            SwiggyRateLimited("quota", 60),
        ],
    )
    graph = PurchaseGraphService(session, clock=clock.now)

    result = await graph.sync(actor, _swiggy_reader(client, clock), provider="swiggy")

    assert len(client.calls) == 2
    assert result.imported_orders == 1
    assert result.stopped_reason == "rate_limited"


@pytest.mark.asyncio
async def test_swiggy_known_cooldown_means_no_call(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    client = FakeSwiggyClient(clock, [], cooldown=30)
    graph = PurchaseGraphService(session, clock=clock.now)

    with pytest.raises(SwiggyRateLimited):
        await graph.sync(actor, _swiggy_reader(client, clock), provider="swiggy")
    assert client.calls == []


# --- Opt-in weekly sync -------------------------------------------------------


@pytest.mark.asyncio
async def test_weekly_auto_sync_is_opt_in_and_runs_at_most_once_a_week(
    session: AsyncSession,
) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    client = FakeSwiggyClient(clock, [{"data": {"orders": [], "hasMore": False}}] * 5)
    graph = PurchaseGraphService(session, clock=clock.now)

    off = await graph.auto_sync(actor, _swiggy_reader(client, clock), "swiggy")
    assert (off.ran, off.reason) == (False, "disabled")
    assert client.calls == []

    status = await graph.set_auto_sync(actor, "swiggy", True)
    assert status.auto_sync_enabled is True
    first = await graph.auto_sync(actor, _swiggy_reader(client, clock), "swiggy")
    assert (first.ran, first.reason) == (True, "synced")
    assert len(client.calls) == 1

    clock.advance(timedelta(days=3))
    early = await graph.auto_sync(actor, _swiggy_reader(client, clock), "swiggy")
    assert (early.ran, early.reason) == (False, "not_due")
    assert len(client.calls) == 1

    clock.advance(timedelta(days=4, minutes=1))
    again = await graph.auto_sync(actor, _swiggy_reader(client, clock), "swiggy")
    assert again.reason == "synced"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_weekly_auto_sync_skips_during_a_cooldown(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session)
    client = FakeSwiggyClient(clock, [SwiggyRateLimited("quota", 600)])
    graph = PurchaseGraphService(session, clock=clock.now)
    await graph.set_auto_sync(actor, "swiggy", True)

    provider_cooldown = await graph.auto_sync(
        actor, _swiggy_reader(client, clock), "swiggy", provider_cooldown_seconds=30
    )
    assert provider_cooldown.reason == "cooldown"
    assert client.calls == []

    failed = await graph.auto_sync(actor, _swiggy_reader(client, clock), "swiggy")
    assert (failed.reason, failed.error_code) == ("error", "SwiggyRateLimited")
    assert len(client.calls) == 1

    clock.advance(timedelta(days=7, minutes=1))
    state = await _state(session, "swiggy")
    state.rate_limited_until = clock.now() + timedelta(minutes=5)
    await session.commit()
    recorded = await graph.auto_sync(actor, _swiggy_reader(client, clock), "swiggy")
    assert recorded.reason == "cooldown"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_auto_sync_needs_history_permission(session: AsyncSession) -> None:
    clock = FakeClock()
    actor = await _actor(session, retention_days=None)
    zepto = FakeZepto(clock, _orders(2))
    graph = PurchaseGraphService(session, clock=clock.now)

    with pytest.raises(MemoryPermissionError):
        await graph.set_auto_sync(actor, "zepto", True)
    outcome = await graph.auto_sync(actor, _zepto_reader(zepto, clock), "zepto")
    assert outcome.reason == "no_permission"
    assert zepto.calls == []
