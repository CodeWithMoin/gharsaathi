import asyncio
import inspect
import stat
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.tracking.errors import TrackingNotFound, TrackingProviderError
from app.tracking.adapters import (
    SwiggyDeliveryStatusAdapter,
    ZeptoOrderStatusAdapter,
    _parse_snapshot,
)
from app.tracking.models import (
    StartTrackingRequest,
    TrackingProvider,
    TrackingSnapshot,
    TrackingStatus,
)
from app.tracking.service import OrderTrackingPoller, OrderTrackingService
from app.tracking.store import OrderTrackingStore


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeReadOnlyProvider:
    provider = TrackingProvider.SWIGGY

    def __init__(self, responses: list[TrackingSnapshot | TrackingProviderError]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str | None]] = []

    async def fetch_delivery_status(
        self, *, order_id: str, address_id: str | None
    ) -> TrackingSnapshot:
        self.calls.append((order_id, address_id))
        response = self.responses[0] if len(self.responses) == 1 else self.responses.popleft()
        if isinstance(response, TrackingProviderError):
            raise response
        return response


def make_service(
    tmp_path: Path,
    provider: FakeReadOnlyProvider,
    clock: MutableClock,
) -> OrderTrackingService:
    return OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.SWIGGY: provider},
        now=clock.now,
        jitter=lambda low, high: low,
    )


def request(order_id: str = "provider-order-sensitive") -> StartTrackingRequest:
    return StartTrackingRequest(
        provider=TrackingProvider.SWIGGY,
        order_id=order_id,
        address_id="saved-address-sensitive",
    )


@pytest.mark.asyncio
async def test_manual_and_concurrent_refreshes_cannot_bypass_provider_cadence(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    snapshot = TrackingSnapshot(
        status=TrackingStatus.PREPARING,
        provider_status="packing",
        poll_interval_seconds=60,
    )
    provider = FakeReadOnlyProvider([snapshot])
    service = make_service(tmp_path, provider, clock)

    started = await service.start("alice", request())
    assert len(provider.calls) == 1
    assert started.next_check_at == clock.now() + timedelta(seconds=60)

    # Several tabs and a user tapping refresh must all receive the cached state.
    await asyncio.gather(
        service.refresh("alice", started.order.id),
        service.refresh("alice", started.order.id),
        service.refresh("alice", started.order.id),
    )
    clock.advance(59)
    await service.refresh("alice", started.order.id)
    assert len(provider.calls) == 1

    clock.advance(1)
    await service.refresh("alice", started.order.id)
    assert len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_interval", "expected_interval"),
    [(3, 10), (60, 60), (3600, 3600)],
)
async def test_polling_is_never_faster_than_global_or_provider_interval(
    tmp_path: Path, provider_interval: int, expected_interval: int
) -> None:
    clock = MutableClock()
    provider = FakeReadOnlyProvider(
        [
            TrackingSnapshot(
                status=TrackingStatus.CONFIRMED,
                poll_interval_seconds=provider_interval,
            )
        ]
    )
    service = make_service(tmp_path / str(provider_interval), provider, clock)

    result = await service.start("alice", request(f"order-{provider_interval}"))

    assert result.next_check_at == clock.now() + timedelta(seconds=expected_interval)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            TrackingSnapshot(
                status=TrackingStatus.DELIVERED,
                delivered=True,
                poll_interval_seconds=10,
            ),
            "terminal_delivered",
        ),
        (
            TrackingSnapshot(
                status=TrackingStatus.CANCELLED,
                cancelled=True,
                poll_interval_seconds=10,
            ),
            "terminal_cancelled",
        ),
        (
            TrackingSnapshot(
                status=TrackingStatus.UNKNOWN,
                disabled=True,
                poll_interval_seconds=10,
            ),
            "provider_tracking_disabled",
        ),
    ],
)
async def test_terminal_or_disabled_response_stops_all_future_polling(
    tmp_path: Path, snapshot: TrackingSnapshot, reason: str
) -> None:
    clock = MutableClock()
    provider = FakeReadOnlyProvider([snapshot])
    service = make_service(tmp_path, provider, clock)

    result = await service.start("alice", request())
    clock.advance(3600)
    await service.poll_due("alice")

    assert result.order.active is False
    assert result.order.stop_reason == reason
    assert result.next_check_at is None
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_retry_after_is_used_exactly_without_stacked_backoff(tmp_path: Path) -> None:
    clock = MutableClock()
    rate_limited = TrackingProviderError(
        "temporarily unavailable",
        transient=True,
        retry_after_seconds=37,
    )
    recovered = TrackingSnapshot(
        status=TrackingStatus.PREPARING,
        poll_interval_seconds=45,
    )
    provider = FakeReadOnlyProvider([rate_limited, recovered])
    service = make_service(tmp_path, provider, clock)

    result = await service.start("alice", request())
    assert result.next_check_at == clock.now() + timedelta(seconds=37)

    clock.advance(36)
    await service.poll_due("alice")
    assert len(provider.calls) == 1
    clock.advance(1)
    await service.poll_due("alice")
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_unchanged_snapshot_does_not_create_duplicate_event(tmp_path: Path) -> None:
    clock = MutableClock()
    snapshot = TrackingSnapshot(
        status=TrackingStatus.OUT_FOR_DELIVERY,
        provider_status="on the way",
        message="Rider has the order",
        eta_at=clock.now() + timedelta(minutes=12),
        poll_interval_seconds=10,
    )
    provider = FakeReadOnlyProvider([snapshot])
    service = make_service(tmp_path, provider, clock)

    first = await service.start("alice", request())
    clock.advance(10)
    second = await service.refresh("alice", first.order.id)

    assert first.changed is True
    assert second.changed is False
    assert len(second.order.events) == 1


@pytest.mark.asyncio
async def test_provider_failure_never_persists_transport_secrets(tmp_path: Path) -> None:
    clock = MutableClock()
    provider = FakeReadOnlyProvider(
        [
            TrackingProviderError(
                "401 Authorization: Bearer super-secret-token; address=221B Baker Street",
                transient=False,
            )
        ]
    )
    service = make_service(tmp_path, provider, clock)

    result = await service.start("alice", request())

    persisted = result.order.last_error or ""
    assert "super-secret-token" not in persisted
    assert "221B Baker Street" not in persisted
    assert result.order.active is False


@pytest.mark.asyncio
async def test_watches_are_user_isolated_and_filenames_hide_provider_ids(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    provider = FakeReadOnlyProvider(
        [TrackingSnapshot(status=TrackingStatus.CONFIRMED, poll_interval_seconds=60)]
    )
    store = OrderTrackingStore(tmp_path / "tracking")
    service = OrderTrackingService(
        store,
        {TrackingProvider.SWIGGY: provider},
        now=clock.now,
    )

    alice = await service.start("alice", request("private-order-id"))
    bob = await service.start("bob", request("private-order-id"))

    with pytest.raises(TrackingNotFound):
        service.get("alice", bob.order.id)
    with pytest.raises(TrackingNotFound):
        service.get("bob", alice.order.id)
    paths = list(store.watches_path.glob("*.json"))
    assert len(paths) == 2
    assert all("private-order-id" not in path.name for path in paths)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)


def test_tracking_boundary_has_no_memory_or_order_mutation_dependency() -> None:
    from app.tracking import ports, service

    source = inspect.getsource(service) + inspect.getsource(ports)
    assert "app.memory" not in source
    assert "MemoryService" not in source
    assert "checkout" not in source
    assert "update_cart" not in source
    assert "place_order" not in source


@pytest.mark.asyncio
async def test_poller_shutdown_interrupts_long_idle_wait() -> None:
    called = asyncio.Event()

    class Service:
        async def poll_due(self) -> list[object]:
            called.set()
            return []

    poller = OrderTrackingPoller(Service(), idle_seconds=3600)  # type: ignore[arg-type]
    poller.start()
    await asyncio.wait_for(called.wait(), timeout=1)

    await asyncio.wait_for(poller.aclose(), timeout=1)
    assert poller._task is None


def test_provider_interval_has_no_invented_upper_cap() -> None:
    snapshot = _parse_snapshot(
        {
            "data": {
                "status": "confirmed",
                "pollIntervalSec": 7200,
            }
        }
    )

    assert snapshot.poll_interval_seconds == 7200


@pytest.mark.asyncio
async def test_provider_adapters_can_only_call_fixed_read_only_tool_names() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def list_tools(self):  # type: ignore[no-untyped-def]
            return [type("Tool", (), {"name": "get_order_status"})()]

        async def call_tool(
            self, name: str, arguments: dict[str, object], **kwargs: object
        ) -> dict[str, object]:
            del kwargs
            self.calls.append((name, arguments))
            return {"data": {"status": "confirmed", "pollIntervalSec": 60}}

    swiggy_client = Client()
    zepto_client = Client()

    await SwiggyDeliveryStatusAdapter(swiggy_client).fetch_delivery_status(
        order_id="order-1", address_id="address-1"
    )
    await ZeptoOrderStatusAdapter(zepto_client).fetch_delivery_status(
        order_id="order-2", address_id=None
    )

    assert swiggy_client.calls == [
        (
            "get_delivery_status",
            {"orderId": "order-1", "addressId": "address-1"},
        )
    ]
    assert zepto_client.calls == [("get_order_status", {"orderId": "order-2"})]
    assert all(
        name not in {"checkout", "update_cart", "clear_cart", "place_order"}
        for name, _ in swiggy_client.calls + zepto_client.calls
    )


@pytest.mark.asyncio
async def test_zepto_does_not_repurpose_order_history_when_live_tool_is_absent() -> None:
    class Client:
        call_count = 0

        async def list_tools(self):  # type: ignore[no-untyped-def]
            return [type("Tool", (), {"name": "get_orders"})()]

        async def call_tool(self, *args: object, **kwargs: object) -> dict[str, object]:
            del args, kwargs
            self.call_count += 1
            return {}

    client = Client()

    with pytest.raises(TrackingProviderError, match="does not expose") as raised:
        await ZeptoOrderStatusAdapter(client).fetch_delivery_status(
            order_id="order-2", address_id=None
        )

    assert raised.value.transient is False
    assert client.call_count == 0


@pytest.mark.asyncio
async def test_poller_survives_one_unexpected_cycle_failure() -> None:
    called_twice = asyncio.Event()

    class Service:
        def __init__(self) -> None:
            self.calls = 0

        async def poll_due(self) -> list[object]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider payload must not kill the loop")
            called_twice.set()
            return []

    service = Service()
    poller = OrderTrackingPoller(service, idle_seconds=0.01)  # type: ignore[arg-type]
    poller.start()
    await asyncio.wait_for(called_twice.wait(), timeout=1)

    await asyncio.wait_for(poller.aclose(), timeout=1)
    assert service.calls >= 2
