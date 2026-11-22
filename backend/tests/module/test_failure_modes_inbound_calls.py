"""Failure modes of the inbound delivery-line call monitor (docs/FAILURE_MODES.md, FM-IC rows).

Sarvam is mocked with ``httpx.MockTransport``; nothing leaves the machine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.inbound_calls import (
    AUTH_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    POLL_SECONDS,
    InboundCallStore,
)
from tests.module.test_inbound_call_monitor import USER, World, interaction

pytestmark = pytest.mark.failure_mode


@pytest_asyncio.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'calls.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    built = World(InboundCallStore(async_sessionmaker(engine, expire_on_commit=False)))
    yield built
    await built.aclose()
    await engine.dispose()


async def test_fm_ic01_sarvam_unreachable_or_erroring_backs_off_without_tight_retries(world: World) -> None:
    await world.prepared()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    world.sarvam.responses = [down, lambda _: httpx.Response(503, json={"detail": "x"}), lambda _: httpx.Response(500)]
    delays = [await world.monitor.tick()]
    delays.append(await world.tick_later(delays[-1]))
    delays.append(await world.tick_later(delays[-1]))
    assert delays == [60.0, 120.0, 240.0]
    assert len(world.sarvam.requests) == 3
    # Recovers on the next good answer and returns to the normal cadence.
    world.sarvam.items = [interaction(1)]
    assert await world.tick_later(delays[-1]) == POLL_SECONDS
    assert len(await world.store.list_calls(USER)) == 1


async def test_fm_ic01_backoff_is_capped_and_honours_retry_after(world: World) -> None:
    await world.prepared()
    world.sarvam.responses = [lambda _: httpx.Response(429, headers={"Retry-After": "300"})]
    assert await world.monitor.tick() == 300.0
    world.monitor.failures = 20
    world.sarvam.responses = [lambda _: httpx.Response(500)]
    assert await world.tick_later(300) == MAX_BACKOFF_SECONDS


async def test_fm_ic02_rejected_key_waits_fifteen_minutes_and_saves_nothing(world: World) -> None:
    await world.prepared()
    world.sarvam.items = [interaction(1)]
    for status in (401, 403):
        world.sarvam.responses = [lambda _, status=status: httpx.Response(status, json={"detail": "bad key"})]
        assert await world.tick_later() == AUTH_BACKOFF_SECONDS
    assert await world.store.list_calls(USER) == []
    assert world.notifier.sent == []


async def test_fm_ic03_malformed_answers_are_ignored_and_back_off(world: World) -> None:
    await world.prepared(minutes_ago=60)
    world.sarvam.responses = [
        lambda _: httpx.Response(200, text="<html>"),
        lambda _: httpx.Response(200, json={"rows": []}),
        lambda _: httpx.Response(200, json=["not", "an", "object"]),
    ]
    assert await world.monitor.tick() == 60.0
    assert await world.tick_later(60) == 120.0
    assert await world.tick_later(120) == 240.0
    # Broken items inside a good page are skipped; the good one is kept.
    world.sarvam.items = [
        "junk",
        {"interaction_id": 5},
        interaction(2, start_datetime="not a time"),
        interaction(3, agent_variables={"call_outcome": "made_up"}, minutes_ago=30),
        interaction(4),
    ]
    assert await world.tick_later(240) == POLL_SECONDS
    calls = await world.store.list_calls(USER)
    # Unknown outcome on a call long finished counts as "no decision", never invented.
    assert sorted(call.outcome for call in calls) == ["no_decision", "route_confirmed"]


async def test_fm_ic04_no_calls_yet_shows_nothing_and_keeps_the_normal_cadence(world: World) -> None:
    await world.prepared()
    assert await world.monitor.tick() == POLL_SECONDS
    assert await world.tick_later() == POLL_SECONDS
    assert await world.store.list_calls(USER) == []
    assert world.notifier.sent == []
    assert len(world.sarvam.requests) == 2
