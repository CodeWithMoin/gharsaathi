"""Voice session start: stage timings, read caps, connection reuse and prewarm."""

import asyncio
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.dependencies import get_voice_service
from app.main import app
from app.voice import service as voice_service
from app.voice.service import LiveSession, LiveSessionService

OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
ANSWER = {"session": {"id": "live_fast"}, "transport": {"type": "webrtc", "sdp": "answer"}}


class NoopHub:
    def start(self, *args: object, **kwargs: object) -> None:
        return None


class NoSearch:
    async def search(self, *args: object) -> object:
        raise AssertionError("search must not run here")


class SlowReads:
    """Profile, memory and context readers that each take ``delay`` seconds."""

    def __init__(self, delay: float, context_delay: float | None = None) -> None:
        self.delay = delay
        self.context_delay = delay if context_delay is None else context_delay
        self.calls: list[str] = []

    async def read(self, household_id: str) -> None:
        self.calls.append("profile")
        await asyncio.sleep(self.delay)

    async def household_memory(self, household_id: str) -> None:
        self.calls.append("memory")
        await asyncio.sleep(self.delay)

    async def load(self, household_id: str) -> None:
        self.calls.append("context")
        await asyncio.sleep(self.context_delay)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, openai_api_key=SecretStr("sk-test-key"), **overrides)  # type: ignore[arg-type]


def make_service(app_settings: Settings, reads: SlowReads, client: httpx.AsyncClient | None) -> LiveSessionService:
    return LiveSessionService(
        app_settings,
        NoSearch(),  # type: ignore[arg-type]
        NoopHub(),  # type: ignore[arg-type]
        client,
        preference_recall=reads,  # type: ignore[arg-type]
        profile_reader=reads,  # type: ignore[arg-type]
        context_reader=reads,  # type: ignore[arg-type]
    )


def answering(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/live/sessions"):
            return httpx.Response(201, json=ANSWER)
        return httpx.Response(401, json={"error": {"message": "missing key"}})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_reads_run_concurrently() -> None:
    reads = SlowReads(0.2)
    async with httpx.AsyncClient(transport=answering([])) as client:
        started = time.perf_counter()
        await make_service(settings(), reads, client).create(OFFER, household_id="user-a")
        elapsed = time.perf_counter() - started

    assert sorted(reads.calls) == ["context", "memory", "profile"]
    # Three 200 ms reads in sequence would take 600 ms.
    assert elapsed < 0.4


@pytest.mark.asyncio
async def test_slow_context_is_dropped_after_300_ms(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[httpx.Request] = []
    reads = SlowReads(0.0, context_delay=2.5)
    async with httpx.AsyncClient(transport=answering(seen)) as client:
        started = time.perf_counter()
        await make_service(settings(), reads, client).create(OFFER, household_id="user-a")
        elapsed = time.perf_counter() - started

    assert elapsed < 0.6
    assert "input" not in json.loads(seen[0].content)["session"]
    assert "Voice chat context read failed: TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_typed_chat_keeps_the_longer_context_wait() -> None:
    reads = SlowReads(0.0, context_delay=0.5)
    service = make_service(settings(), reads, None)
    # The default (typed chat) cap is 2 s, so a 500 ms load is still awaited.
    started = time.perf_counter()
    await service.conversation_context("user-a")
    assert time.perf_counter() - started >= 0.45
    assert reads.calls == ["context"]


@pytest.mark.asyncio
async def test_pre_negotiated_data_channel_id_is_forwarded() -> None:
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=answering(seen)) as client:
        await make_service(settings(), SlowReads(0), client).create(OFFER, data_channel_id=4)
    assert json.loads(seen[0].content)["transport"] == {"type": "webrtc", "sdp": OFFER, "dcid": 4}


@pytest.mark.asyncio
async def test_timings_are_content_free_and_only_returned_with_the_log_flag(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="app.voice.service")
    async with httpx.AsyncClient(transport=answering([])) as client:
        quiet = await make_service(settings(), SlowReads(0), client).create(OFFER, household_id="user-a")
        logged = await make_service(settings(voice_event_log=True), SlowReads(0), client).create(
            OFFER, household_id="user-a"
        )

    assert quiet.start_timing_ms is None
    assert logged.start_timing_ms is not None
    assert {"profile", "memory", "context", "reads", "config", "openai", "total"} <= set(logged.start_timing_ms)
    assert all(isinstance(value, float) for value in logged.start_timing_ms.values())
    lines = [record.getMessage() for record in caplog.records if "voice_session_start_timing" in record.getMessage()]
    assert len(lines) == 1
    assert "user-a" not in lines[0] and "sk-test-key" not in lines[0]


@pytest.mark.asyncio
async def test_shared_connection_is_prewarmed_without_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []
    created: list[httpx.AsyncClient] = []
    real_client = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        client = real_client(transport=answering(seen), **kwargs)  # type: ignore[arg-type]
        created.append(client)
        return client

    monkeypatch.setattr(voice_service.httpx, "AsyncClient", factory)
    service = make_service(settings(), SlowReads(0), None)

    assert await service.prewarm() is True
    # Throttled: a second hover does not send another request.
    assert await service.prewarm() is False
    await service.create(OFFER)
    await service.create(OFFER)
    await service.aclose()

    assert len(created) == 1
    paths = [request.url.path for request in seen]
    assert paths == ["/v1/models", "/v1/live/sessions", "/v1/live/sessions"]
    assert "authorization" not in seen[0].headers
    assert seen[1].headers["authorization"] == "Bearer sk-test-key"


@pytest.mark.asyncio
async def test_prewarm_does_nothing_without_an_api_key() -> None:
    service = LiveSessionService(Settings(_env_file=None), NoSearch(), NoopHub())  # type: ignore[arg-type]
    assert await service.prewarm() is False


class FakePrewarm:
    def __init__(self) -> None:
        self.calls = 0

    async def prewarm(self) -> bool:
        self.calls += 1
        return True

    async def create(self, *args: object, **kwargs: object) -> LiveSession:
        raise AssertionError("prewarm must not create a session")


def test_prewarm_endpoint_checks_origin_and_creates_no_session() -> None:
    fake = FakePrewarm()
    app.dependency_overrides[get_voice_service] = lambda: fake
    try:
        client = TestClient(app)
        ok = client.post("/api/voice/prewarm", headers={"Origin": "http://localhost:3003"})
        rejected = client.post("/api/voice/prewarm", headers={"Origin": "https://evil.example"})
    finally:
        app.dependency_overrides.pop(get_voice_service, None)
    assert ok.status_code == 204
    assert rejected.status_code == 403
    assert fake.calls == 1


def test_session_request_validates_the_data_channel_id() -> None:
    app.dependency_overrides[get_voice_service] = lambda: FakePrewarm()
    try:
        response = TestClient(app).post(
            "/api/voice/session",
            json={"sdp": OFFER, "dcid": 70000},
            headers={"Origin": "http://localhost:3003"},
        )
    finally:
        app.dependency_overrides.pop(get_voice_service, None)
    assert response.status_code == 422
