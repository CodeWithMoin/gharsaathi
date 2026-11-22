"""Failure modes at the OpenAI and voice-session boundary, photos, and restarts (docs/FAILURE_MODES.md).

OpenAI is never called: the Responses API, the GPT-Live session endpoint and
the sideband websocket are fakes from tests/fault_injection.py.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.chat.service import EMPTY_REPLY, ChatProviderError, ChatTurnService
from app.core.config import Settings
from app.photos.extraction import OpenAIPhotoExtractor, PhotoExtractionError, PhotoReader
from app.photos.store import PhotoAttachmentStore
from app.voice import sideband as sideband_module
from app.voice.photo_tools import PhotoToolExecutor
from app.voice.service import LiveSessionService, VoiceConfigurationError, VoiceProviderError
from app.voice.sideband import FINISHED, SidebandHub, SidebandRunner
from app.voice.tools import ToolOutcome
from tests.fault_injection import (
    HOUSEHOLD,
    SWIGGY_ADDRESS,
    FaultWorld,
    FaultySearcher,
    Raise,
    Slow,
    FaultyResponses,
    function_call,
    live_sessions_transport,
    openai_error,
    product,
)

pytestmark = pytest.mark.failure_mode

FAKE_KEY = "sk-test-failure-modes-not-a-real-key"
SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"_env_file": None, "openai_api_key": SecretStr(FAKE_KEY), "openai_base_url": "https://api.openai.test/v1"}
    values.update(overrides)
    return Settings(**values)


# ---------------------------------------------------------------- FM-AI01..04 voice session creation


def voice_service(answer: object, config: Settings | None = None) -> tuple[LiveSessionService, SidebandHub]:
    config = config or settings()
    hub = SidebandHub(config)
    client = httpx.AsyncClient(transport=live_sessions_transport(answer))
    return LiveSessionService(config, FaultySearcher(), hub, http_client=client), hub


@pytest.mark.parametrize(
    ("answer", "status_code", "message"),
    [
        (httpx.ConnectError("connection refused"), 502, "Could not reach OpenAI"),
        (httpx.ReadTimeout("timed out"), 502, "Could not reach OpenAI"),
        (httpx.Response(429, json={"error": {"message": "Rate limit reached for requests"}}), None, "could not start"),
        (httpx.Response(500, json={"error": {"message": "The server had an error"}}), 502, "could not start"),
        (httpx.Response(503, text="<html>upstream</html>"), 502, "HTTP 503"),
        ({"session": {}}, 502, "unexpected session response"),
        ({"session": {"id": 7}, "transport": {"sdp": None}}, 502, "unexpected session response"),
    ],
    ids=["unreachable", "timeout", "rate_limit", "server_error", "html_error", "missing_fields", "wrong_types"],
)
async def test_fm_ai01_voice_session_start_failures_are_clean_errors_with_no_session(
    answer: object, status_code: int | None, message: str
) -> None:
    service, hub = voice_service(answer)

    with pytest.raises(VoiceProviderError) as raised:
        await service.create(SDP, SWIGGY_ADDRESS, None, HOUSEHOLD)

    assert message in str(raised.value)
    if status_code is not None:
        assert raised.value.status_code == status_code
    else:
        assert raised.value.status_code in {400, 429, 502, 503}
    assert FAKE_KEY not in str(raised.value)
    assert hub._channels == {}  # no sideband, no events token, nothing billed on our side


async def test_fm_ai02_voice_without_an_api_key_is_a_configuration_error() -> None:
    service, hub = voice_service({"never": "called"}, settings(openai_api_key=SecretStr("")))

    with pytest.raises(VoiceConfigurationError):
        await service.create(SDP, SWIGGY_ADDRESS, None, HOUSEHOLD)
    assert hub._channels == {}


# ---------------------------------------------------------------- FM-AI05..08 typed chat


def chat(world: FaultWorld, responses: FaultyResponses) -> ChatTurnService:
    config = settings()
    voice = LiveSessionService(
        config,
        FaultySearcher([product("Amul Taaza Milk", "500 ml", 28, prefix="s")]),
        SidebandHub(config),
        baskets=world.baskets,
        approvals=world.approvals,
    )
    return ChatTurnService(config, voice, responses=responses)


@pytest.mark.parametrize("kind", ["rate_limit", "timeout", "connection", "server"])
async def test_fm_ai05_typed_chat_openai_errors_are_a_hinglish_error_and_change_nothing(kind: str) -> None:
    world = FaultWorld()
    service = chat(world, FaultyResponses([openai_error(kind)]))

    with pytest.raises(ChatProviderError) as raised:
        await service.run_turn(HOUSEHOLD, None, "doodh chahiye", swiggy_address_id=SWIGGY_ADDRESS)

    assert "Thodi der mein phir likhiye" in str(raised.value)
    assert await world.snapshot("swiggy_instamart") == (0, [], "draft")


@pytest.mark.parametrize("output", ["", "   "], ids=["empty", "refusal"])
async def test_fm_ai06_typed_chat_empty_output_or_refusal_gets_a_fixed_retry_line(output: str) -> None:
    world = FaultWorld()
    service = chat(world, FaultyResponses([output]))

    result = await service.run_turn(HOUSEHOLD, None, "doodh chahiye", swiggy_address_id=SWIGGY_ADDRESS)

    assert result.reply == EMPTY_REPLY
    assert result.ui_events == []


@pytest.mark.xfail(
    strict=True,
    reason="FM-AI07: when the model fails after a basket-changing tool round, /api/chat/turn "
    "returns 502 and drops that round's ui_events, so the basket changed server-side but the "
    "chat shows no basket card until a reload.",
)
async def test_fm_ai07_model_failure_after_a_basket_change_still_reports_the_change() -> None:
    world = FaultWorld()
    responses = FaultyResponses(
        [
            [function_call("search_products", "c1", query="Amul Taaza milk")],
            [function_call("add_to_basket", "c2", option_id="opt1", quantity=1)],
            openai_error("rate_limit"),
        ]
    )
    service = chat(world, responses)

    result = await service.run_turn(HOUSEHOLD, None, "Amul Taaza add karo", swiggy_address_id=SWIGGY_ADDRESS)

    assert "basket.updated" in [event["type"] for event in result.ui_events]


async def test_fm_ai07_the_basket_change_itself_is_kept_when_the_model_fails() -> None:
    world = FaultWorld()
    responses = FaultyResponses(
        [
            [function_call("search_products", "c1", query="Amul Taaza milk")],
            [function_call("add_to_basket", "c2", option_id="opt1", quantity=1)],
            openai_error("rate_limit"),
        ]
    )
    with pytest.raises(ChatProviderError):
        await chat(world, responses).run_turn(HOUSEHOLD, None, "Amul Taaza add karo", swiggy_address_id=SWIGGY_ADDRESS)

    revision, lines, _ = await world.snapshot("swiggy_instamart")
    assert len(lines) == 1  # one add, not repeated by any retry


async def test_fm_ai08_a_failing_tool_mid_turn_is_reported_to_the_model_not_raised() -> None:
    world = FaultWorld()

    class Exploding:
        async def search(self, query: str, address_id: str, offset: int):  # type: ignore[no-untyped-def]
            raise RuntimeError("unexpected bug")

    config = settings()
    voice = LiveSessionService(config, Exploding(), SidebandHub(config), baskets=world.baskets, approvals=world.approvals)
    responses = FaultyResponses([[function_call("search_products", "c1", query="Amul Taaza milk")], "Abhi search nahi ho paya."])
    service = ChatTurnService(config, voice, responses=responses)

    result = await service.run_turn(HOUSEHOLD, None, "doodh", swiggy_address_id=SWIGGY_ADDRESS)

    sent = json.loads(responses.requests[1]["input"][0]["output"])
    assert sent["status"] in {"error", "provider_error"}
    assert result.reply == "Abhi search nahi ho paya."
    assert [event["type"] for event in result.ui_events] == ["tool.error"]


# ---------------------------------------------------------------- FM-V01/V02, FM-AI03/AI04 GPT-Live sideband


class FakeConnection:
    """A sideband websocket: yields scripted events, records what the server sends."""

    def __init__(self, events: list[dict[str, Any]], hold: asyncio.Event | None = None) -> None:
        self.events = events
        self.sent: list[dict[str, Any]] = []
        self.hold = hold

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self):  # type: ignore[no-untyped-def]
        return self._iterate()

    async def _iterate(self):  # type: ignore[no-untyped-def]
        for event in self.events:
            if event.get("type") == "_wait" and self.hold is not None:
                await self.hold.wait()
                continue
            yield json.dumps(event)


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "response.event",
        "delegation_id": "d1",
        "event": {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)},
        },
    }


async def test_fm_v01_sideband_connect_failure_tells_the_browser_and_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        raise OSError("connection refused")

    monkeypatch.setattr(sideband_module, "connect", refuse)
    hub = SidebandHub(settings())
    channel = hub.start("sess_1", object(), owner_id=HOUSEHOLD, events_token="token-123")  # type: ignore[arg-type]
    assert channel.task is not None
    await channel.task

    assert channel.finished
    assert channel.history[-2]["type"] == "tool.error"
    assert "connection toot gaya" in channel.history[-2]["message"]
    assert channel.history[-1] is FINISHED


async def test_fm_v02_session_closed_mid_tool_call_cancels_the_call_and_detaches_order_results() -> None:
    world = FaultWorld()
    started = asyncio.Event()

    class Hanging:
        async def search(self, query: str, address_id: str, offset: int):  # type: ignore[no-untyped-def]
            started.set()
            await asyncio.sleep(30)

    toolbox = world.toolbox(swiggy=Hanging(), zepto_address=None)
    connection = FakeConnection(
        [
            tool_call("c1", "search_products", {"query": "Amul Taaza milk"}),
            {"type": "response.event", "delegation_id": "d1", "event": {"type": "response.completed"}},
            {"type": "session.closed"},
        ]
    )
    runner = SidebandRunner(connection, toolbox, lambda event: None)

    await asyncio.wait_for(runner.run(), 2)

    assert toolbox.order._sender is None  # a later tap result is queued, never sent to a closed socket
    assert not any(item.get("type") == "response.create" for item in connection.sent)


async def test_fm_ai03_a_gpt_live_error_or_failed_delegation_does_not_crash_the_session() -> None:
    runner = SidebandRunner(FakeConnection([]), FaultWorld().toolbox(), lambda event: None)

    assert await runner.handle({"type": "error", "error": {"code": "rate_limit_exceeded", "message": "slow down"}}) is False
    assert await runner.handle({"type": "response.event", "delegation_id": "d1", "event": {"type": "response.failed"}}) is False


async def test_fm_ai04_a_voice_tool_that_raises_is_an_error_output_and_a_toast() -> None:
    published: list[dict[str, Any]] = []

    class Exploding:
        order = None

        async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
            raise RuntimeError("bug")

    connection = FakeConnection([])
    runner = SidebandRunner(connection, Exploding(), published.append)  # type: ignore[arg-type]
    await runner.handle(tool_call("c1", "search_products", {"query": "milk"}))
    await asyncio.gather(*runner._pending["d1"])

    output = json.loads(connection.sent[0]["item"]["output"])
    assert output["status"] == "error"
    assert published[-1]["type"] == "tool.error"


# ---------------------------------------------------------------- FM-PH photos


class FakeExtractor:
    def __init__(self, error: Exception | None = None, delay: float = 0) -> None:
        self.error = error
        self.delay = delay

    async def extract(self, jpeg: bytes):  # type: ignore[no-untyped-def]
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        raise AssertionError("not reached")


@pytest.mark.parametrize("kind", ["rate_limit", "timeout", "connection", "server"])
async def test_fm_ph03_vision_call_errors_become_a_photo_extraction_error(kind: str) -> None:
    extractor = OpenAIPhotoExtractor("k", "https://api.openai.test/v1", "gpt-test", responses=FaultyResponses([openai_error(kind)]))

    with pytest.raises(PhotoExtractionError):
        await extractor.extract(b"\xff\xd8jpeg")


@pytest.mark.parametrize("output", ["", "not json at all", "[1, 2]"], ids=["empty_or_refusal", "not_json", "wrong_shape"])
async def test_fm_ph04_empty_refused_or_malformed_vision_output_is_an_error(output: str) -> None:
    extractor = OpenAIPhotoExtractor("k", "https://api.openai.test/v1", "gpt-test", responses=FaultyResponses([output]))

    with pytest.raises(PhotoExtractionError):
        await extractor.extract(b"\xff\xd8jpeg")


@pytest.mark.parametrize(
    "extractor",
    [FakeExtractor(PhotoExtractionError("The photo could not be read just now.")), FakeExtractor(delay=5)],
    ids=["error", "slow"],
)
async def test_fm_ph03_failed_or_slow_extraction_keeps_the_photo_and_asks_to_retry(extractor: FakeExtractor) -> None:
    store = PhotoAttachmentStore()
    photo = store.put(HOUSEHOLD, b"\xff\xd8jpeg")
    reader = PhotoReader(store, extractor, timeout_seconds=0.05)  # type: ignore[arg-type]
    tool = PhotoToolExecutor(reader, HOUSEHOLD)

    outcome = await tool.execute("read_attached_photo", "{}")

    assert outcome.model_output["status"] == "error"
    assert "try once more" in outcome.model_output["message"]
    assert outcome.ui_event is None  # the chip stays
    assert store.get(HOUSEHOLD, photo.attachment_id) is not None


async def test_fm_s02_backend_restart_drops_pending_photos_and_the_tool_asks_to_attach_again() -> None:
    old = PhotoAttachmentStore()
    old.put(HOUSEHOLD, b"\xff\xd8jpeg")
    restarted = PhotoReader(PhotoAttachmentStore(), FakeExtractor())  # type: ignore[arg-type]

    outcome = await PhotoToolExecutor(restarted, HOUSEHOLD).execute("read_attached_photo", "{}")

    assert outcome.model_output["status"] == "no_photo"
    assert "attach it again" in outcome.model_output["message"]


@pytest.mark.parametrize(
    ("data", "status_code"),
    [(b"GIF89a....", 415), (b"\xff\xd8\xff\xe0 truncated jpeg", 422), (b"", 422)],
    ids=["unsupported_type", "corrupt", "empty"],
)
def test_fm_ph01_bad_uploads_are_refused_with_a_status_the_chip_explains(data: bytes, status_code: int) -> None:
    from app.dependencies import get_photo_store
    from app.main import app

    store = PhotoAttachmentStore()
    app.dependency_overrides[get_photo_store] = lambda: store
    try:
        response = TestClient(app).post("/api/chat/attachments", files={"file": ("p.jpg", data, "image/jpeg")})
    finally:
        app.dependency_overrides.pop(get_photo_store, None)

    assert response.status_code == status_code
    assert len(store) == 0


# ---------------------------------------------------------------- FM-S03 restart: voice events


def test_fm_s03_backend_restart_forgets_voice_event_tokens_and_the_stream_is_a_404() -> None:
    from app.dependencies import get_sideband_hub
    from app.main import app

    restarted = SidebandHub(settings())
    app.dependency_overrides[get_sideband_hub] = lambda: restarted
    try:
        response = TestClient(app).get(
            "/api/voice/sessions/sess_before_restart/events?token=old-token",
            headers={"Origin": "http://localhost:3003"},
        )
    finally:
        app.dependency_overrides.pop(get_sideband_hub, None)

    assert response.status_code == 404


# ---------------------------------------------------------------- FM-S06 database unavailable


def test_fm_s06_database_down_fails_the_chat_turn_before_any_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import OperationalError

    from app.api import chat as chat_api
    from app.dependencies import get_chat_service
    from app.main import app

    class DownSession:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))

        async def __aexit__(self, *exc: object) -> None:
            return None

    responses = FaultyResponses(["never"])
    monkeypatch.setattr(chat_api, "get_session_factory", lambda: (lambda: DownSession()))
    app.dependency_overrides[get_chat_service] = lambda: chat(FaultWorld(), responses)
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/chat/turn", json={"message": "doodh chahiye"}
        )
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code >= 500
    assert responses.requests == []
    assert "connection refused" not in response.text
