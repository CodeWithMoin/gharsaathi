"""docs/FAILURE_MODES.md rows FM-R01..FM-R07: rider assist (Vobiz stream ↔ OpenAI Realtime)."""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.rider import vobiz
from app.rider.service import RiderAssistService, get_rider_service
from app.rider.store import DeliveryNotes, RiderCallSummary
from tests.module.test_rider_api import CALL, rider_settings, signed
from tests.module.test_rider_bridge import bridge
from tests.rider_fakes import FakeOrders, FakeRealtime, FakeRecorder, FakeVobiz, rider_says, spoken

pytestmark = pytest.mark.failure_mode
SECRET = "s" * 40
CALL_ID = CALL["CallUUID"]


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


class Pushes:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, user_id, payload, *, dedupe_key=None):
        self.sent.append((user_id, payload, dedupe_key))


def service(store: MemoryStore, connections: Connections, *, pushes: Pushes | None = None, **settings) -> RiderAssistService:
    return RiderAssistService(rider_settings(**settings), store, FakeOrders(), None, connector_factory=connections, notifier=pushes)


def path(call_id: str = CALL_ID, token: str | None = None) -> str:
    return f"{vobiz.STREAM_PATH}?s={call_id}.{token or vobiz.stream_token(SECRET, call_id)}"


def start_message(call_id: str = CALL_ID) -> str:
    fake = FakeVobiz()
    fake.start(call_id=call_id)
    return fake.incoming.get_nowait()


def refused(svc: RiderAssistService, url: str) -> int:
    app.dependency_overrides[get_rider_service] = lambda: svc
    try:
        # No lifespan: the tracking poller must not start in a test.
        http = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as closed:
            with http.websocket_connect(url) as ws:
                ws.receive_text()
        return closed.value.code
    finally:
        app.dependency_overrides.pop(get_rider_service, None)


def test_fm_r01_realtime_down_apologises_notifies_and_closes() -> None:
    store, connections, pushes = MemoryStore(), Connections(fail=True), Pushes()
    svc = service(store, connections, pushes=pushes)
    svc.registry.answered(CALL_ID, "•••• 210", "hinglish")
    app.dependency_overrides[get_rider_service] = lambda: svc
    try:
        http = TestClient(app)
        with http.websocket_connect(path()) as ws:
            ws.send_text(start_message())
            assert ws.receive_json() == {"event": "stop", "streamId": "stream-1"}
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
        after = http.post(vobiz.AFTER_STREAM_PATH, data=CALL, headers=signed(vobiz.AFTER_STREAM_PATH))
    finally:
        app.dependency_overrides.pop(get_rider_service, None)
    assert closed.value.code == 1000
    assert connections.opened == 1
    (call,) = store.calls
    assert call.outcome == "assistant_unavailable" and "couldn't answer this call" in call.summary
    # The next XML (Redirect) gets the spoken apology; the owner gets a rider_called push.
    assert "Maaf kijiye" in after.text and "<Hangup/>" in after.text
    ((user, payload, key),) = pushes.sent
    assert user == "owner-1" and payload.kind == "rider_called" and "couldn't answer" in payload.body
    assert key == "rider_called:1"


@pytest.mark.parametrize("bad", ["0000000000x" + "0" * 48, "garbage", "expired"])
def test_fm_r02_bad_stream_token_is_refused_before_anything_runs(bad: str) -> None:
    store, connections = MemoryStore(), Connections()
    svc = service(store, connections)
    svc.registry.answered(CALL_ID, "x", "hinglish")
    token = vobiz.stream_token(SECRET, CALL_ID, now=0) if bad == "expired" else bad
    assert refused(svc, path(token=token)) == 1008
    assert connections.opened == 0 and store.calls == []


def test_fm_r02_token_for_another_call_or_an_unanswered_call_is_refused() -> None:
    store, connections = MemoryStore(), Connections()
    svc = service(store, connections)
    assert refused(svc, path()) == 1008  # valid signature, but the answer URL never let it in
    svc.registry.answered(CALL_ID, "x", "hinglish")
    assert refused(svc, path(token=vobiz.stream_token(SECRET, "other-call-uuid"))) == 1008
    assert connections.opened == 0


def test_fm_r02_unsigned_answer_webhook_is_refused() -> None:
    svc = service(MemoryStore(), Connections())
    app.dependency_overrides[get_rider_service] = lambda: svc
    try:
        http = TestClient(app)
        assert http.post(vobiz.ANSWER_PATH, data=CALL).status_code == 403
        assert http.post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH, "forged")).status_code == 403
    finally:
        app.dependency_overrides.pop(get_rider_service, None)
    assert svc.registry.get(CALL_ID) is None


def test_fm_r03_feature_flag_off_refuses_webhooks_and_the_stream() -> None:
    store, connections = MemoryStore(), Connections()
    svc = service(store, connections, rider_assist_enabled=False)
    svc.registry.answered(CALL_ID, "x", "hinglish")
    assert refused(svc, path()) == 1008
    app.dependency_overrides[get_rider_service] = lambda: svc
    try:
        assert TestClient(app).post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH)).status_code == 404
    finally:
        app.dependency_overrides.pop(get_rider_service, None)
    assert connections.opened == 0 and store.calls == []


def test_fm_r04_owner_switched_off_or_server_unconfigured_refuses_the_stream() -> None:
    connections = Connections()
    off = service(MemoryStore(enabled=False), connections)
    off.registry.answered(CALL_ID, "x", "hinglish")
    assert refused(off, path()) == 1008
    unset = service(MemoryStore(), connections, rider_assist_owner_user_id="")
    unset.registry.answered(CALL_ID, "x", "hinglish")
    assert refused(unset, path()) == 1008
    assert connections.opened == 0


@pytest.mark.asyncio
async def test_fm_r05_call_cap_wraps_up_and_closes() -> None:
    socket = FakeVobiz()
    socket.start()
    realtime = FakeRealtime(turns=[[]])

    async def feed():
        while True:
            socket.media()
            await asyncio.sleep(0.05)

    feeder = asyncio.create_task(feed())
    try:
        summary = await asyncio.wait_for(bridge(socket, realtime, max_call_seconds=0.5).run(), 4)
    finally:
        feeder.cancel()
    assert any(e.get("event_id") == "rider_wrap_up" for e in realtime.of("response.create"))
    assert socket.of("stop") and socket.closed_with == 1000 and realtime.closed
    assert summary.ended_at is not None and summary.ended_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_fm_r06_realtime_drop_mid_call_ends_and_reports_to_the_owner() -> None:
    socket = FakeVobiz()
    socket.start()
    realtime = FakeRealtime(turns=[spoken("greet", "Namaste, main Moin ki AI assistant hoon.") + rider_says("flat number?")])
    recorder = FakeRecorder()
    task = asyncio.create_task(bridge(socket, realtime, recorder=recorder).run())
    await asyncio.sleep(0.2)
    realtime.incoming.put_nowait(None)  # OpenAI closes the socket
    summary = await asyncio.wait_for(task, 3)
    assert summary.outcome == "ended_early"
    assert socket.of("stop") and socket.closed_with == 1000
    assert recorder.last.rider_said == "flat number?"


@pytest.mark.asyncio
async def test_fm_r07_goodbye_checkpoint_never_acknowledged_still_ends_the_call() -> None:
    socket = FakeVobiz(echo_checkpoints=False)  # playedStream is conditional per Vobiz
    socket.start()
    realtime = FakeRealtime(turns=[spoken("bye", "Dhanyavaad!", calls=[("end_call", {"reason": "answered"})])])
    summary = await asyncio.wait_for(bridge(socket, realtime, end_playback_timeout_seconds=0.3).run(), 3)
    assert socket.of("checkpoint") and socket.of("stop") and summary.ended_at is not None
