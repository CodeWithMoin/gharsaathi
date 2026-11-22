"""Rider assist API and Vobiz webhooks (FastAPI app with SQLite and fakes)."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.database.base import Base
from app.main import app
from app.rider import vobiz
from app.rider.service import RiderAssistService, get_rider_service
from app.rider.store import RiderCallSummary, RiderStore
from tests.rider_fakes import FakeOrders


def rider_settings(**overrides) -> Settings:
    values = dict(
        rider_assist_enabled=True,
        rider_assist_owner_user_id="owner-1",
        rider_assist_public_url="https://rider.example.in",
        rider_assist_stream_secret=SecretStr("s" * 40),
        rider_assist_number="+918071387423",
        vobiz_auth_id="MA_TEST01",
        vobiz_auth_token=SecretStr("vobiz-token"),
        openai_api_key=SecretStr(""),
    )
    values.update(overrides)
    return Settings(**values)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[RiderStore]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rider-api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield RiderStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


@pytest_asyncio.fixture
async def service(store: RiderStore) -> RiderAssistService:
    return RiderAssistService(rider_settings(), store, FakeOrders(), None)


@pytest_asyncio.fixture
async def client(service: RiderAssistService) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_rider_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_rider_service, None)


def as_user(user_id: str) -> dict[str, str]:
    return {"X-Test-User-Id": user_id}


@pytest.mark.asyncio
async def test_settings_reflect_the_server_flag_owner_and_user_switch(client: AsyncClient) -> None:
    owner = await client.get("/api/rider/settings", headers=as_user("owner-1"))
    assert owner.status_code == 200
    body = owner.json()
    assert body["feature_enabled"] is True and body["owner_configured"] is True
    assert body["stream_auth_configured"] is True and body["enabled"] is False
    other = (await client.get("/api/rider/settings", headers=as_user("someone-else"))).json()
    assert other["owner_configured"] is False

    saved = await client.put(
        "/api/rider/settings",
        json={"enabled": True, "notes": {"landmark": "Near the park", "directions": "Gate 2, flat 402", "handoff": "leave_at_door", "preferred_language": "english"}},
        headers=as_user("owner-1"),
    )
    assert saved.status_code == 200
    assert saved.json()["enabled"] is True
    assert saved.json()["notes"]["handoff"] == "leave_at_door"
    refused = await client.put("/api/rider/settings", json={"notes": {"directions": "OTP is 1234"}}, headers=as_user("owner-1"))
    assert refused.status_code == 422
    assert (await client.get("/api/rider/settings", headers=as_user("owner-1"))).json()["notes"]["directions"] == "Gate 2, flat 402"


@pytest.mark.asyncio
async def test_calls_are_listed_marked_seen_and_deleted_per_user(client: AsyncClient, store: RiderStore) -> None:
    from datetime import UTC, datetime

    for text in ("first", "second"):
        await store.add_call("owner-1", RiderCallSummary(started_at=datetime.now(UTC), caller="•••• 210", outcome="answered", summary=text, answered_from=["notes"], rider_said="Gate pe hoon, flat number?"))
    listed = (await client.get("/api/rider/calls", headers=as_user("owner-1"))).json()["calls"]
    assert [call["summary"] for call in listed] == ["second", "first"]
    assert (await client.get("/api/rider/calls", headers=as_user("other"))).json()["calls"] == []
    call_id = listed[0]["id"]
    assert (await client.post(f"/api/rider/calls/{call_id}/seen", headers=as_user("other"))).status_code == 404
    assert (await client.post(f"/api/rider/calls/{call_id}/seen", headers=as_user("owner-1"))).status_code == 204
    assert (await client.delete(f"/api/rider/calls/{call_id}", headers=as_user("other"))).status_code == 404
    assert (await client.delete(f"/api/rider/calls/{call_id}", headers=as_user("owner-1"))).status_code == 204
    assert (await client.delete(f"/api/rider/calls/{uuid4()}", headers=as_user("owner-1"))).status_code == 404
    assert (await client.delete("/api/rider/calls", headers=as_user("owner-1"))).status_code == 204
    assert (await client.get("/api/rider/calls", headers=as_user("owner-1"))).json()["calls"] == []


def signed(path: str, token: str = "vobiz-token", version: str = "V3") -> dict[str, str]:
    import base64
    import hashlib
    import hmac

    url = vobiz.base_url("https://rider.example.in", path)
    nonce = "12345678901234567890"
    joiner = "." if version == "V3" else ""
    signature = base64.b64encode(hmac.new(token.encode(), f"{url}{joiner}{nonce}".encode(), hashlib.sha256).digest()).decode()
    return {f"X-Vobiz-Signature-{version}": signature, f"X-Vobiz-Signature-{version}-Nonce": nonce}


CALL = {"CallUUID": "9a0e0208-d01a-4572", "From": "+919876543210", "To": "+918071387423", "Direction": "inbound"}


@pytest.mark.asyncio
async def test_answer_url_needs_a_valid_signature_and_streams_to_a_signed_path(client: AsyncClient, store: RiderStore) -> None:
    from app.rider.store import RiderSettingsUpdate

    await store.update("owner-1", RiderSettingsUpdate(enabled=True))
    assert (await client.post(vobiz.ANSWER_PATH, data=CALL)).status_code == 403
    assert (await client.post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH, "wrong"))).status_code == 403
    answered = await client.post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH))
    assert answered.status_code == 200 and answered.headers["content-type"].startswith("application/xml")
    xml = answered.text
    assert 'contentType="audio/x-mulaw;rate=8000"' in xml and 'bidirectional="true"' in xml
    assert "wss://rider.example.in/api/rider/stream?s=9a0e0208-d01a-4572." in xml
    assert "<Redirect method=\"POST\">https://rider.example.in/api/rider/vobiz/after-stream</Redirect>" in xml
    v2 = await client.post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH, version="V2"))
    assert v2.status_code == 200


@pytest.mark.asyncio
async def test_answer_url_refuses_other_numbers_and_a_switched_off_owner(client: AsyncClient, store: RiderStore) -> None:
    from app.rider.store import RiderSettingsUpdate

    off = await client.post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH))
    assert off.status_code == 200 and "<Stream" not in off.text and "<Hangup/>" in off.text
    await store.update("owner-1", RiderSettingsUpdate(enabled=True))
    other = await client.post(vobiz.ANSWER_PATH, data={**CALL, "To": "+911111111111"}, headers=signed(vobiz.ANSWER_PATH))
    assert "<Stream" not in other.text
    bad_id = await client.post(vobiz.ANSWER_PATH, data={**CALL, "CallUUID": "../x"}, headers=signed(vobiz.ANSWER_PATH))
    assert bad_id.status_code == 400


@pytest.mark.asyncio
async def test_after_stream_apologises_only_when_the_assistant_never_answered(client: AsyncClient, service: RiderAssistService, store: RiderStore) -> None:
    from app.rider.store import RiderSettingsUpdate

    await store.update("owner-1", RiderSettingsUpdate(enabled=True))
    assert (await client.post(vobiz.AFTER_STREAM_PATH, data=CALL)).status_code == 403
    await client.post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH))
    never_streamed = await client.post(vobiz.AFTER_STREAM_PATH, data=CALL, headers=signed(vobiz.AFTER_STREAM_PATH))
    assert "Maaf kijiye" in never_streamed.text and "<Hangup/>" in never_streamed.text
    service.registry.claim_stream(CALL["CallUUID"])
    service.registry.finished(CALL["CallUUID"], "answered")
    fine = await client.post(vobiz.AFTER_STREAM_PATH, data=CALL, headers=signed(vobiz.AFTER_STREAM_PATH))
    assert "Speak" not in fine.text and "<Hangup/>" in fine.text
    service.registry.finished(CALL["CallUUID"], "assistant_unavailable")
    sorry = await client.post(vobiz.AFTER_STREAM_PATH, data=CALL, headers=signed(vobiz.AFTER_STREAM_PATH))
    assert "Maaf kijiye" in sorry.text


@pytest.mark.asyncio
async def test_webhooks_are_off_with_the_feature_flag(store: RiderStore) -> None:
    service = RiderAssistService(rider_settings(rider_assist_enabled=False), store, FakeOrders(), None)
    app.dependency_overrides[get_rider_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.post(vobiz.ANSWER_PATH, data=CALL, headers=signed(vobiz.ANSWER_PATH))).status_code == 404
    finally:
        app.dependency_overrides.pop(get_rider_service, None)
    unconfigured = RiderAssistService(rider_settings(vobiz_auth_token=__import__("pydantic").SecretStr("")), store, FakeOrders(), None)
    assert (await unconfigured.answer(CALL, signed(vobiz.ANSWER_PATH))).status_code == 503
