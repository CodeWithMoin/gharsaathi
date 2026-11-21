"""The voice events stream is readable only with the token issued to its creator."""

import asyncio
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.logging import RedactOAuthCallbackFilter
from app.dependencies import get_sideband_hub
from app.main import app
from app.voice import sideband as sideband_module
from app.voice.sideband import SidebandHub


class OfflineHub(SidebandHub):
    """Never dials OpenAI: the sideband ends as soon as it starts."""

    async def _attach(self, session_id, executor, channel) -> None:
        channel.publish({"type": "tool.started", "name": "search_products"})
        channel.finish()


@pytest.fixture
async def hub_and_client():
    hub = OfflineHub(get_settings())
    app.dependency_overrides[get_sideband_hub] = lambda: hub
    origin = get_settings().allowed_origins[0]
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Origin": origin}
        ) as client:
            yield hub, client
    finally:
        app.dependency_overrides.pop(get_sideband_hub, None)


async def _started(hub: SidebandHub, session_id: str, token: str) -> None:
    hub.start(session_id, object(), owner_id="alice", events_token=token)  # type: ignore[arg-type]
    await asyncio.sleep(0)


async def test_creator_token_streams_events(hub_and_client) -> None:
    hub, client = hub_and_client
    await _started(hub, "live_1", "alice-token")

    response = await client.get("/api/voice/sessions/live_1/events", params={"token": "alice-token"})

    assert response.status_code == 200
    assert '"tool.started"' in response.text and '"session.finished"' in response.text


@pytest.mark.parametrize("token", [None, "", "wrong-token", "alice-token-but-longer"])
async def test_missing_or_wrong_token_looks_like_an_unknown_session(hub_and_client, token) -> None:
    hub, client = hub_and_client
    await _started(hub, "live_1", "alice-token")
    params = {} if token is None else {"token": token}

    response = await client.get("/api/voice/sessions/live_1/events", params=params)

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown voice session."}


async def test_a_token_only_opens_its_own_session(hub_and_client) -> None:
    hub, client = hub_and_client
    await _started(hub, "live_1", "alice-token")
    await _started(hub, "live_2", "other-token")

    response = await client.get("/api/voice/sessions/live_2/events", params={"token": "alice-token"})

    assert response.status_code == 404


async def test_expired_token_is_refused(hub_and_client, monkeypatch) -> None:
    hub, client = hub_and_client
    await _started(hub, "live_1", "alice-token")
    later = sideband_module.time.monotonic() + sideband_module.EVENTS_TOKEN_TTL_SECONDS + 1
    monkeypatch.setattr(sideband_module.time, "monotonic", lambda: later)

    response = await client.get("/api/voice/sessions/live_1/events", params={"token": "alice-token"})

    assert response.status_code == 404


def test_hub_refuses_a_session_without_owner_or_token() -> None:
    hub = SidebandHub(get_settings())
    with pytest.raises(ValueError):
        hub.start("live_1", object(), owner_id="", events_token="t")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        hub.start("live_1", object(), owner_id="alice", events_token="")  # type: ignore[arg-type]


def test_events_token_is_redacted_from_access_logs() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", "GET", "/api/voice/sessions/live_1/events?token=secret-token", "1.1", 200),
        exc_info=None,
    )

    RedactOAuthCallbackFilter().filter(record)

    assert "secret-token" not in record.getMessage()
    assert "/api/voice/sessions/live_1/events?[REDACTED]" in record.getMessage()
