"""RiderCallMonitor end to end on SQLite with a mocked Sarvam List interactions API.

Gating (only while someone waits), one request per 30 s, dedupe, user scoping,
redaction, one browser notification per call, and the calls API. No network:
every Sarvam response comes from ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.database.base import Base
from app.dependencies import active_order_windows, get_inbound_call_store
from app.inbound_calls import (
    CONTEXT_WINDOW,
    POLL_SECONDS,
    InboundCallStore,
    RiderCallMonitor,
    delete_user_inbound_calls,
)
from app.commerce.demo import DemoAddressBook
from app.inbound_delivery import InboundDeliveryService
from app.inbound_delivery.models import DirectionsUpdate, PrepareRequest
from app.inbound_delivery.store import DeliveryLineStore
from app.main import app
from app.notifications.models import SubscriptionIn
from app.notifications.service import PushNotifier
from app.notifications.store import PushStore
from app.tracking.models import OrderWatch, TrackingProvider, TrackingStatus
from app.tracking.store import OrderTrackingStore
from tests.push_fakes import (
    TEST_ENCRYPTION_KEY,
    TEST_PUBLIC_KEY,
    RecordingSender,
    subscription,
)

NOW = datetime(2026, 11, 22, 8, 50, tzinfo=UTC)
DEPLOYMENT = "deployment-1"
USER = "user-1"
OTHER = "user-2"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        commerce_mode="demo",
        sarvam_voice_agent_api_key=SecretStr("voice-agent-key"),
        sarvam_voice_agent_org_id="org-1",
        sarvam_voice_agent_workspace_id="workspace-1",
        sarvam_inbound_agent_id="agent-1",
        sarvam_inbound_deployment_id=DEPLOYMENT,
        sarvam_inbound_phone_number="+919999999999",
        sarvam_demo_recipient_number="+919876543210",
        sarvam_call_monitor_enabled=True,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def interaction(n: int = 1, *, minutes_ago: float = 5, **overrides: Any) -> dict[str, Any]:
    started = NOW - timedelta(minutes=minutes_ago)
    variables = {
        "call_outcome": "route_confirmed",
        "delivery_guidance_summary": "Rider was guided to the gate. OTP 4821 not shared.",
        "delivery_provider": "zepto",
        "caller_authorized": "yes",
        "recipient_name": "Moin",
        "delivery_address": "12 Test Street, HSR Layout, Bengaluru 560102",
    }
    variables.update(overrides.pop("agent_variables", {}))
    values = {
        "interaction_id": f"20261122/call-{n}",
        "start_datetime": started.replace(tzinfo=None).isoformat(),
        "end_datetime": (started + timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
        "duration_in_seconds": 60.0,
        "user_contact": "+919876542590",
        "user_contact_masked": "+91******2590",
        "channel_direction": "inbound",
        "deployment_id": DEPLOYMENT,
        "app_version": 2,
        "is_debug_call": 0,
        "end_reason": "USER_ENDS",
        "failure_reason": "NO_FAILURE_REASON",
        "agent_variables": variables,
    }
    values.update(overrides)
    return values


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class Sarvam:
    """Scripted List interactions responses; records every request."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.responses: list[Callable[[httpx.Request], httpx.Response]] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)(request)
        return httpx.Response(
            200, json={"items": self.items, "total": len(self.items), "limit": 100, "offset": 0, "next_page_uri": "", "prev_page_uri": ""}
        )


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any, str | None]] = []

    async def send(self, user_id: str, payload: Any, *, dedupe_key: str | None = None) -> None:
        self.sent.append((user_id, payload, dedupe_key))


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'calls.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def store(factory: async_sessionmaker[AsyncSession]) -> InboundCallStore:
    return InboundCallStore(factory)


class World:
    def __init__(self, store: InboundCallStore, **settings_overrides: Any) -> None:
        self.store = store
        self.clock = Clock()
        self.sarvam = Sarvam()
        self.notifier: Any = RecordingNotifier()
        self.orders: list[tuple[str, datetime]] = []
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.sarvam.handler))
        self.monitor = RiderCallMonitor(
            settings(**settings_overrides),
            store,
            active_orders=lambda: list(self.orders),
            user_key=InboundDeliveryService._user_key,
            notifier=self.notifier,
            client=self.http,
            clock=self.clock,
        )
        self.monitor.notifier = self.notifier

    def use_notifier(self, notifier: Any) -> None:
        self.notifier = notifier
        self.monitor.notifier = notifier

    async def prepared(self, user_id: str = USER, cohort_id: str = "cohort-1", minutes_ago: float = 20) -> None:
        await self.store.record_context(user_id, cohort_id, NOW - timedelta(minutes=minutes_ago))

    async def tick_later(self, seconds: float = POLL_SECONDS) -> float:
        self.clock.advance(seconds)
        return await self.monitor.tick()

    async def aclose(self) -> None:
        await self.http.aclose()


@pytest_asyncio.fixture
async def world(store: InboundCallStore) -> AsyncIterator[World]:
    built = World(store)
    yield built
    await built.aclose()


# Gating ----------------------------------------------------------------------


async def test_no_request_while_nobody_waits_for_a_call(world: World) -> None:
    assert await world.monitor.tick() == POLL_SECONDS
    await world.prepared(minutes_ago=CONTEXT_WINDOW.total_seconds() / 60 + 1)  # older than 2 h
    assert await world.tick_later() == POLL_SECONDS
    assert world.sarvam.requests == []


async def test_no_request_when_the_monitor_is_off_or_unconfigured(store: InboundCallStore) -> None:
    for overrides in ({"sarvam_call_monitor_enabled": False}, {"sarvam_voice_agent_api_key": SecretStr("")}, {"sarvam_inbound_agent_id": ""}):
        world = World(store, **overrides)
        await world.prepared(cohort_id=f"cohort-{len(overrides)}-{next(iter(overrides))}")
        assert not world.monitor.configured
        await world.monitor.tick()
        assert world.sarvam.requests == []
        await world.aclose()


async def test_a_prepared_context_polls_the_documented_endpoint_read_only(world: World) -> None:
    await world.prepared()
    await world.monitor.tick()

    [request] = world.sarvam.requests
    assert request.method == "GET"
    assert request.url.copy_with(query=None) == httpx.URL(
        "https://apps.sarvam.ai/api/analytics/v1/org-1/workspace-1/agent-1/interactions"
    )
    assert request.headers["X-API-Key"] == "voice-agent-key"
    params = request.url.params
    assert params["start_datetime"] == (NOW - timedelta(minutes=21)).isoformat()
    assert params["end_datetime"] == (NOW + timedelta(minutes=1)).isoformat()
    assert "+00:00" in params["start_datetime"] and "%2B00%3A00" in str(request.url)
    assert json.loads(params["filter_conditions"]) == [
        {"field": "channel_direction", "operator": "equals", "value": "inbound"}
    ]
    assert "include_transcript" not in params


async def test_an_active_order_polls_and_a_finished_one_stops(world: World) -> None:
    world.orders = [(USER, NOW - timedelta(minutes=10))]
    await world.monitor.tick()
    assert len(world.sarvam.requests) == 1
    world.orders = []  # delivered or cancelled: no longer listed as active
    await world.tick_later()
    assert len(world.sarvam.requests) == 1
    world.orders = [(USER, NOW - timedelta(hours=7))]  # still "moving" after 6 h: stale
    await world.tick_later()
    assert len(world.sarvam.requests) == 1


def test_active_order_windows_skip_delivered_cancelled_and_stopped_orders(tmp_path: Path) -> None:
    tracking = OrderTrackingStore(tmp_path / "tracking")
    created = NOW - timedelta(minutes=15)

    def save(order_id: str, status: TrackingStatus, active: bool = True, user: str = USER) -> None:
        tracking.save(
            OrderWatch(
                id=str(uuid4()),
                user_id=user,
                provider=TrackingProvider.ZEPTO,
                order_id=order_id,
                status=status,
                active=active,
                created_at=created,
                updated_at=created,
            )
        )

    save("o-moving", TrackingStatus.OUT_FOR_DELIVERY)
    save("o-done", TrackingStatus.DELIVERED)
    save("o-cancelled", TrackingStatus.CANCELLED)
    save("o-stopped", TrackingStatus.PREPARING, active=False)
    save("o-other", TrackingStatus.CONFIRMED, user=OTHER)
    assert sorted(active_order_windows(tracking)) == sorted([(USER, created), (OTHER, created)])


async def test_at_most_one_request_per_30_seconds(world: World) -> None:
    await world.prepared()
    await world.monitor.tick()
    delay = await world.tick_later(10)
    assert len(world.sarvam.requests) == 1 and delay == pytest.approx(20)
    await world.tick_later(20)
    assert len(world.sarvam.requests) == 2


# Saving, dedupe and scoping -----------------------------------------------------


async def test_a_new_call_is_saved_redacted_and_notified_once(world: World) -> None:
    await world.prepared()
    world.sarvam.items = [interaction(1)]
    await world.monitor.tick()
    await world.tick_later()
    await world.tick_later()

    [call] = await world.store.list_calls(USER)
    assert call.provider == "zepto" and call.outcome == "route_confirmed"
    assert call.caller == "•••• 2590"
    assert call.summary == "Rider was guided to the gate. OTP [hidden] not shared."
    stored = call.model_dump_json()
    for secret in ("9876542590", "4821", "Test Street", "560102", "Moin"):
        assert secret not in stored
    assert len(world.sarvam.requests) == 3
    [(user, payload, key)] = world.notifier.sent
    assert user == USER and key == f"rider_called:inbound:{call.id}"
    assert payload.kind == "rider_called" and payload.title == "Zepto rider called"
    assert payload.body == "2:15 pm — Route confirmed."
    assert "2590" not in payload.model_dump_json() and "gate" not in payload.body


async def test_dedupe_survives_a_restart(world: World, store: InboundCallStore) -> None:
    await world.prepared()
    world.sarvam.items = [interaction(1)]
    await world.monitor.tick()
    again = World(store)
    again.sarvam.items = [interaction(1)]
    await again.monitor.tick()
    assert len(await store.list_calls(USER)) == 1
    assert again.notifier.sent == [] and len(world.notifier.sent) == 1
    await again.aclose()


async def test_only_inbound_calls_on_this_deployment_after_the_context(world: World) -> None:
    await world.prepared(minutes_ago=20)
    world.sarvam.items = [
        interaction(1, minutes_ago=30),  # before the context was prepared
        interaction(2, channel_direction="outbound"),
        interaction(3, deployment_id="other-deployment"),
        interaction(4, is_debug_call=1),
        interaction(5, minutes_ago=3),
    ]
    await world.monitor.tick()
    calls = await world.store.list_calls(USER)
    assert [call.started_at for call in calls] == [NOW - timedelta(minutes=3)]


async def test_calls_are_scoped_to_their_user(world: World) -> None:
    await world.prepared(USER, "cohort-1")
    await world.prepared(OTHER, "cohort-2")
    world.sarvam.items = [
        interaction(1, cohort_id="cohort-1"),
        interaction(2, cohort_id="cohort-2", agent_variables={"call_outcome": "owner_transfer_requested"}),
        interaction(3),  # unknown caller, two households waiting: nobody's
    ]
    await world.monitor.tick()
    mine = await world.store.list_calls(USER)
    theirs = await world.store.list_calls(OTHER)
    assert [call.outcome for call in mine] == ["route_confirmed"]
    assert [call.outcome for call in theirs] == ["owner_transfer_requested"]
    assert sorted(user for user, _, _ in world.notifier.sent) == [USER, OTHER]


async def test_a_call_waits_for_its_outcome_then_counts_as_no_decision(world: World) -> None:
    await world.prepared()
    world.sarvam.items = [interaction(1, minutes_ago=2, agent_variables={"call_outcome": None})]
    await world.monitor.tick()
    assert await world.store.list_calls(USER) == []
    await world.tick_later(11 * 60)
    [call] = await world.store.list_calls(USER)
    assert call.outcome == "no_decision" and len(world.notifier.sent) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        "route_confirmed",
        "owner_transfer_requested",
        "provider_contact_needed",
        "unauthorized_caller",
        "general_request_handled",
        "out_of_scope",
        "no_decision",
    ],
)
async def test_every_outcome_is_stored_and_notified(world: World, outcome: str) -> None:
    await world.prepared()
    world.sarvam.items = [interaction(1, agent_variables={"call_outcome": outcome})]
    await world.monitor.tick()
    [call] = await world.store.list_calls(USER)
    assert call.outcome == outcome
    [(_, payload, _)] = world.notifier.sent
    assert payload.body.startswith("2:15 pm — ")


async def test_real_notifier_sends_once_and_respects_the_opt_out(world: World, factory) -> None:
    sender = RecordingSender()
    push_store = PushStore(factory, TEST_ENCRYPTION_KEY)
    world.use_notifier(PushNotifier(push_store, sender, public_key=TEST_PUBLIC_KEY, clock=world.clock))
    await push_store.save_subscription(USER, SubscriptionIn.model_validate(subscription(1)))
    await world.prepared()
    world.sarvam.items = [interaction(1)]
    await world.monitor.tick()
    await world.tick_later()
    assert [payload["kind"] for payload in sender.payloads()] == ["rider_called"]

    await push_store.update_preferences(USER, kinds={"rider_called": False}, order_prompt_answered=None, at=NOW)
    world.sarvam.items = [interaction(1), interaction(2, minutes_ago=1)]
    await world.tick_later()
    assert len(sender.payloads()) == 1
    assert len(await world.store.list_calls(USER)) == 2


async def test_logs_carry_no_call_content(world: World, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="app")
    await world.prepared()
    world.sarvam.items = [interaction(1)]
    await world.monitor.tick()
    world.sarvam.responses = [lambda _: httpx.Response(500, json={"detail": "boom +919876542590"})]
    await world.tick_later()
    text = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith("app"))
    assert "event=rider_call_monitor result=ok" in text
    for secret in ("voice-agent-key", "2590", "gate", "Test Street", "20261122/call-1", "Moin"):
        assert secret not in text


# Context recording, API and deletion --------------------------------------------


class Profiles:
    async def read(self, user_id: str) -> Any:
        return type("Profile", (), {"first_name": "Moin"})()


async def test_sharing_starts_the_two_hour_window_and_clearing_ends_it(
    store: InboundCallStore, factory: async_sessionmaker[AsyncSession]
) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json={"cohort_id": f"cohort-{count}", "status": "processing"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        line = InboundDeliveryService(
            settings(sarvam_inbound_owner_user_id=USER),
            DeliveryLineStore(factory),
            DemoAddressBook(),
            Profiles(),
            client=client,
            contexts=store,
        )
        await line.save_directions(USER, DirectionsUpdate(provider="zepto", address_id="demo-home", landmark="Near the park"))
        await line.prepare(USER, PrepareRequest(provider="zepto", address_id="demo-home", confirmed=True))
        [context] = await store.contexts_since(datetime.now(UTC) - timedelta(minutes=1))
        assert (context.user_id, context.cohort_id) == (USER, "cohort-1")
        await line.revoke(USER, "user_cleared")
    # The clear record is not a context: it authorises nothing.
    assert await store.contexts_since(datetime.now(UTC) - timedelta(minutes=1)) == []


@pytest_asyncio.fixture
async def client(store: InboundCallStore) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_inbound_call_store] = lambda: store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_inbound_call_store, None)


async def test_calls_api_is_user_scoped_and_deletable(world: World, client: AsyncClient, factory) -> None:
    await world.prepared(USER, "cohort-1")
    await world.prepared(OTHER, "cohort-2")
    world.sarvam.items = [interaction(1, cohort_id="cohort-1"), interaction(2, cohort_id="cohort-2")]
    await world.monitor.tick()

    mine = (await client.get("/api/inbound-delivery/calls", headers={"X-Test-User-Id": USER})).json()["calls"]
    assert len(mine) == 1 and set(mine[0]) == {"id", "started_at", "provider", "outcome", "summary", "caller"}
    theirs = (await client.get("/api/inbound-delivery/calls", headers={"X-Test-User-Id": OTHER})).json()["calls"]
    # Someone else's call can't be deleted.
    response = await client.delete(f"/api/inbound-delivery/calls/{theirs[0]['id']}", headers={"X-Test-User-Id": USER})
    assert response.status_code == 404
    response = await client.delete(f"/api/inbound-delivery/calls/{mine[0]['id']}", headers={"X-Test-User-Id": USER})
    assert response.status_code == 204
    assert (await client.get("/api/inbound-delivery/calls", headers={"X-Test-User-Id": USER})).json()["calls"] == []

    assert (await client.delete("/api/inbound-delivery/calls", headers={"X-Test-User-Id": OTHER})).status_code == 204
    assert await world.store.list_calls(OTHER) == []
    assert all(context.user_id != OTHER for context in await world.store.contexts_since(NOW - timedelta(hours=3)))


async def test_forget_everything_removes_inbound_calls(world: World, factory) -> None:
    await world.prepared()
    world.sarvam.items = [interaction(1)]
    await world.monitor.tick()
    async with factory() as session, session.begin():
        await delete_user_inbound_calls(session, USER)
    assert await world.store.list_calls(USER) == []
    assert await world.store.contexts_since(NOW - timedelta(hours=3)) == []
