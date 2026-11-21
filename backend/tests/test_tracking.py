import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tracking import router as tracking_router
from app.auth import require_user_id
from app.tracking.errors import TrackingProviderError
from app.tracking.models import StartTrackingRequest, TrackingProvider, TrackingSnapshot, TrackingStatus
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeProvider:
    provider = TrackingProvider.SWIGGY

    def __init__(self, responses: list[TrackingSnapshot | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str | None]] = []

    async def fetch_delivery_status(
        self, *, order_id: str, address_id: str | None
    ) -> TrackingSnapshot:
        self.calls.append((order_id, address_id))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def build_service(tmp_path: Path, provider: FakeProvider, clock: FakeClock) -> OrderTrackingService:
    return OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.SWIGGY: provider},
        now=clock.now,
        jitter=lambda low, high: high,
    )


def request() -> StartTrackingRequest:
    return StartTrackingRequest(
        provider=TrackingProvider.SWIGGY, order_id="order-abc", address_id="address-1"
    )


def test_start_persists_and_honours_provider_poll_interval(tmp_path: Path) -> None:
    clock = FakeClock()
    provider = FakeProvider([TrackingSnapshot(status=TrackingStatus.CONFIRMED, poll_interval_seconds=75)])
    service = build_service(tmp_path, provider, clock)

    result = asyncio.run(service.start("home", request()))

    assert result.changed is True
    assert result.order.active is True
    assert result.next_check_at == clock.now() + timedelta(seconds=75)
    assert provider.calls == [("order-abc", "address-1")]
    reloaded = OrderTrackingStore(tmp_path / "tracking").get_by_id("home", result.order.id)
    assert reloaded.order_id == "order-abc"


def test_manual_refresh_and_concurrent_tabs_cannot_bypass_cadence(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        provider = FakeProvider(
            [
                TrackingSnapshot(status=TrackingStatus.CONFIRMED, poll_interval_seconds=10),
                TrackingSnapshot(status=TrackingStatus.PREPARING, poll_interval_seconds=10),
            ]
        )
        service = build_service(tmp_path, provider, clock)
        created = await service.start("home", request())

        cached = await service.refresh("home", created.order.id)
        assert cached.changed is False
        assert len(provider.calls) == 1

        clock.advance(10)
        first, second = await asyncio.gather(
            service.refresh("home", created.order.id),
            service.refresh("home", created.order.id),
        )
        assert len(provider.calls) == 2
        assert {first.order.status, second.order.status} == {TrackingStatus.PREPARING}

    asyncio.run(scenario())


def test_retry_after_is_honoured_exactly_and_transient_backoff_is_bounded(tmp_path: Path) -> None:
    clock = FakeClock()
    provider = FakeProvider(
        [
            TrackingProviderError(retry_after_seconds=91),
            TrackingProviderError("500", transient=True),
        ]
    )
    service = build_service(tmp_path, provider, clock)

    rate_limited = asyncio.run(service.start("home", request()))
    assert rate_limited.next_check_at == clock.now() + timedelta(seconds=91)
    assert rate_limited.order.consecutive_failures == 1

    clock.advance(91)
    transient = asyncio.run(service.refresh("home", rate_limited.order.id))
    assert transient.next_check_at is not None
    assert 1 <= (transient.next_check_at - clock.now()).total_seconds() <= 30


def test_terminal_and_disabled_updates_stop_loop_and_dedupe_events(tmp_path: Path) -> None:
    clock = FakeClock()
    provider = FakeProvider(
        [
            TrackingSnapshot(status=TrackingStatus.CONFIRMED, poll_interval_seconds=10),
            TrackingSnapshot(status=TrackingStatus.DELIVERED, delivered=True),
        ]
    )
    service = build_service(tmp_path, provider, clock)
    created = asyncio.run(service.start("home", request()))
    clock.advance(10)
    delivered = asyncio.run(service.refresh("home", created.order.id))
    assert delivered.order.active is False
    assert delivered.order.stop_reason == "terminal_delivered"
    assert delivered.next_check_at is None
    assert len(delivered.order.events) == 2

    disabled_provider = FakeProvider([TrackingSnapshot(disabled=True, poll_interval_seconds=10)])
    disabled = asyncio.run(build_service(tmp_path / "other", disabled_provider, clock).start("home", request()))
    assert disabled.order.active is False
    assert disabled.order.stop_reason == "provider_tracking_disabled"


def test_opaque_monitor_api_never_puts_order_ids_in_a_route(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(tracking_router, prefix="/api/tracking")
    clock = FakeClock()
    provider = FakeProvider([TrackingSnapshot(status=TrackingStatus.CONFIRMED)])
    app.state.order_tracking_service = build_service(tmp_path, provider, clock)
    app.dependency_overrides[require_user_id] = lambda: "home"

    with TestClient(app) as client:
        created = client.post(
            "/api/tracking/monitors",
            json={"provider": "swiggy", "order_id": "private-order-42", "address_id": "home"},
        )
        assert created.status_code == 201
        monitor_id = created.json()["monitor_id"]
        fetched = client.get(f"/api/tracking/monitors/{monitor_id}")
        assert fetched.status_code == 200
        assert "private-order-42" not in fetched.request.url.path
        assert all("{order_id}" not in path for path in app.openapi()["paths"])
