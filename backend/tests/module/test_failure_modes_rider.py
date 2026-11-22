"""docs/FAILURE_MODES.md rows FM-R01..FM-R06: rider assist (Exotel stream ↔ OpenAI Realtime)."""

import asyncio
import base64
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.rider.service import RiderAssistService, get_rider_service
from app.rider.store import DeliveryNotes, RiderCallSummary
from tests.module.test_rider_api import rider_settings
from tests.module.test_rider_bridge import bridge
from tests.rider_fakes import FakeExotel, FakeOrders, FakeRealtime, FakeRecorder, rider_says, spoken

pytestmark = pytest.mark.failure_mode
AUTH = {"Authorization": "Basic " + base64.b64encode(b"exotel:s3cret-password-123456").decode()}


class MemoryStore:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.calls: list[RiderCallSummary] = []

    async def enabled_and_notes(self, user_id: str):
        return self.enabled, DeliveryNotes(directions="Gate 2")

    async def add_call(self, user_id: str, call: RiderCallSummary):
        self.calls.append(call)
        return type("Saved", (), {"id": str(len(self.calls))})()

    async def update_call(self, user_id: str, call_id: str, call: RiderCallSummary) -> bool:
        self.calls[int(call_id) - 1] = call
        return True


class Connections:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.opened = 0

    def __call__(self, settings, owner):
        async def connect():
            self.opened += 1
            if self.fail:
                raise OSError("OpenAI unreachable")
            return FakeRealtime(turns=[[]])

        return connect


def service(store: MemoryStore, connections: Connections, **settings) -> RiderAssistService:
    return RiderAssistService(rider_settings(**settings), store, FakeOrders(), None, connector_factory=connections)


def refused(svc: RiderAssistService, headers: dict[str, str]) -> int:
    app.dependency_overrides[get_rider_service] = lambda: svc
    try:
        # No lifespan: the tracking poller must not start in a test.
        http = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as closed:
            with http.websocket_connect("/api/rider/stream?sample-rate=16000", headers=headers) as ws:
                ws.receive_text()
        return closed.value.code
    finally:
        app.dependency_overrides.pop(get_rider_service, None)


def start_message() -> str:
    exotel = FakeExotel()
    exotel.start()
    exotel.incoming.get_nowait()
    return exotel.incoming.get_nowait()


def test_fm_r01_realtime_down_closes_the_stream_and_tells_the_owner() -> None:
    store, connections = MemoryStore(), Connections(fail=True)
    app.dependency_overrides[get_rider_service] = lambda: service(store, connections)
    try:
        http = TestClient(app)
        with http.websocket_connect("/api/rider/stream?sample-rate=16000", headers=AUTH) as ws:
            ws.send_text('{"event":"connected"}')
            ws.send_text(start_message())
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
    finally:
        app.dependency_overrides.pop(get_rider_service, None)
    assert closed.value.code == 1000
    assert connections.opened == 1
    (call,) = store.calls
    assert call.outcome == "assistant_unavailable"
    assert "couldn't answer this call" in call.summary


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic " + base64.b64encode(b"exotel:wrong").decode()}, {"Authorization": "Bearer token"}],
)
def test_fm_r02_stream_auth_failure_is_refused_before_anything_runs(headers) -> None:
    store, connections = MemoryStore(), Connections()
    assert refused(service(store, connections), headers) == 1008
    assert connections.opened == 0 and store.calls == []


def test_fm_r02_unconfigured_stream_credentials_refuse_every_caller() -> None:
    store, connections = MemoryStore(), Connections()
    assert refused(service(store, connections, exotel_stream_user=""), AUTH) == 1008
    assert connections.opened == 0


def test_fm_r03_feature_flag_off_refuses_the_stream() -> None:
    store, connections = MemoryStore(), Connections()
    assert refused(service(store, connections, rider_assist_enabled=False), AUTH) == 1008
    assert connections.opened == 0 and store.calls == []


def test_fm_r04_owner_switched_off_or_unset_refuses_the_stream() -> None:
    connections = Connections()
    assert refused(service(MemoryStore(enabled=False), connections), AUTH) == 1008
    assert refused(service(MemoryStore(), connections, rider_assist_owner_user_id=""), AUTH) == 1008
    assert connections.opened == 0


@pytest.mark.asyncio
async def test_fm_r05_call_cap_wraps_up_and_closes() -> None:
    exotel = FakeExotel()
    exotel.start()
    realtime = FakeRealtime(turns=[[]])

    async def feed():
        while True:
            exotel.media()
            await asyncio.sleep(0.05)

    feeder = asyncio.create_task(feed())
    try:
        summary = await asyncio.wait_for(bridge(exotel, realtime, max_call_seconds=0.5).run(), 4)
    finally:
        feeder.cancel()
    assert any(e.get("event_id") == "rider_wrap_up" for e in realtime.of("response.create"))
    assert exotel.closed_with == 1000 and realtime.closed
    assert summary.ended_at is not None and summary.ended_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_fm_r06_realtime_drop_mid_call_ends_and_reports_to_the_owner() -> None:
    exotel = FakeExotel()
    exotel.start()
    realtime = FakeRealtime(turns=[spoken("greet", "Namaste, main Moin ki AI assistant hoon.") + rider_says("flat number?")])
    recorder = FakeRecorder()
    b = bridge(exotel, realtime, recorder=recorder)
    task = asyncio.create_task(b.run())
    await asyncio.sleep(0.2)
    realtime.incoming.put_nowait(None)  # OpenAI closes the socket
    summary = await asyncio.wait_for(task, 3)
    assert summary.outcome == "ended_early"
    assert exotel.closed_with == 1000
    assert recorder.last.rider_said == "flat number?"
