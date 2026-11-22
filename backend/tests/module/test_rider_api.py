"""Rider assist API and stream gatekeeping (FastAPI app with SQLite and fakes)."""

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
from app.rider.service import RiderAssistService, get_rider_service
from app.rider.store import RiderCallSummary, RiderStore
from tests.rider_fakes import FakeOrders


def rider_settings(**overrides) -> Settings:
    values = dict(
        rider_assist_enabled=True,
        rider_assist_owner_user_id="owner-1",
        exotel_stream_user="exotel",
        exotel_stream_password=SecretStr("s3cret-password-123456"),
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
async def client(store: RiderStore) -> AsyncIterator[AsyncClient]:
    service = RiderAssistService(rider_settings(), store, FakeOrders(), None)
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
