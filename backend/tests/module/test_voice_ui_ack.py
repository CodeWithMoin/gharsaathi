"""POST /api/voice/sessions/{id}/ui-ack: only the session's creator can report what the screen shows."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.dependencies import get_sideband_hub
from app.main import app
from app.voice.sideband import SidebandHub


class OfflineHub(SidebandHub):
    async def _attach(self, session_id, executor, channel) -> None:
        return None


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


ACK = {"ack_id": "ack_12345678", "form_visible": True, "reason": "shown", "filled_fields": ["building_name", "floor"]}


async def test_creator_ack_resolves_the_waiting_tool(hub_and_client) -> None:
    hub, client = hub_and_client
    channel = hub.start("live_ack", object(), owner_id="alice", events_token="t")  # type: ignore[arg-type]
    waiter = asyncio.create_task(channel.acks.wait("ack_12345678", 2))
    await asyncio.sleep(0)

    response = await client.post(
        "/api/voice/sessions/live_ack/ui-ack", json=ACK, headers={"X-Test-User-Id": "alice"}
    )

    assert response.status_code == 204
    ack = await waiter
    assert ack is not None and ack.form_visible is True
    assert ack.filled_fields == ["building_name", "floor"]


async def test_other_user_and_unknown_session_get_404(hub_and_client) -> None:
    hub, client = hub_and_client
    channel = hub.start("live_ack2", object(), owner_id="alice", events_token="t")  # type: ignore[arg-type]

    other = await client.post("/api/voice/sessions/live_ack2/ui-ack", json=ACK, headers={"X-Test-User-Id": "mallory"})
    unknown = await client.post("/api/voice/sessions/nope/ui-ack", json=ACK, headers={"X-Test-User-Id": "alice"})

    assert other.status_code == 404 and unknown.status_code == 404
    assert await channel.acks.wait("ack_12345678", 0.01) is None


@pytest.mark.parametrize(
    "body",
    [
        {**ACK, "filled_fields": ["contact_number"]},
        {**ACK, "reason": "because"},
        {**ACK, "ack_id": "x"},
        {**ACK, "extra": 1},
    ],
)
async def test_ack_body_is_strict(hub_and_client, body) -> None:
    hub, client = hub_and_client
    hub.start("live_ack3", object(), owner_id="alice", events_token="t")  # type: ignore[arg-type]
    response = await client.post("/api/voice/sessions/live_ack3/ui-ack", json=body, headers={"X-Test-User-Id": "alice"})
    assert response.status_code == 422
