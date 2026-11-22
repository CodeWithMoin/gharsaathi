"""Browser notifications end to end on SQLite with a recording push sender.

Subscriptions (user-scoped, encrypted at rest, cleaned up on 404/410), dedupe,
the per-user rate limit, opt-outs, every trigger emitting once, and silence
for events that aren't meaningful. No network: the sender is a fake.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import WebPushSubscriptionRecord
from app.dependencies import get_push_notifier
from app.main import app
from app.notifications import messages
from app.notifications.models import SubscriptionIn
from app.notifications.service import RATE_LIMIT, PushEvents, PushNotifier
from app.notifications.store import PushStore
from app.shopping.models import CheckoutAttempt, CheckoutStatus
from app.tracking.models import StartTrackingRequest, TrackingProvider, TrackingSnapshot, TrackingStatus
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore
from tests.push_fakes import TEST_ENCRYPTION_KEY, TEST_PUBLIC_KEY, RecordingSender, subscription

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: float) -> None:
        self.value += timedelta(**delta)


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'push.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def store(factory) -> PushStore:
    return PushStore(factory, TEST_ENCRYPTION_KEY)


@pytest.fixture
def notifier(store, sender, clock) -> PushNotifier:
    return PushNotifier(store, sender, public_key=TEST_PUBLIC_KEY, clock=clock)


async def subscribe(store: PushStore, user: str, n: int = 1) -> str:
    return await store.save_subscription(user, SubscriptionIn.model_validate(subscription(n)))


def attempt(status: CheckoutStatus, *, method: str = "cod", order_id: str | None = "ORD1", user: str = "alice") -> CheckoutAttempt:
    return CheckoutAttempt(
        id="attempt-1",
        approval_id="approval-1",
        basket_id="basket-1",
        household_id=user,
        idempotency_key_hash="a" * 64,
        payable_paise=21250,
        status=status,
        provider_order_id=order_id,
        created_at=NOW,
        payment_method=method,  # type: ignore[arg-type]
    )


# Subscriptions ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriptions_are_user_scoped(store) -> None:
    await subscribe(store, "alice", 1)
    await subscribe(store, "alice", 2)
    await subscribe(store, "bob", 3)
    assert [item.endpoint for item in await store.subscriptions("alice")] == [
        subscription(1)["endpoint"],
        subscription(2)["endpoint"],
    ]
    # Bob cannot remove Alice's browser.
    assert not await store.delete_subscription("bob", str(subscription(1)["endpoint"]))
    assert await store.device_count("alice") == 2
    assert await store.delete_subscription("alice", str(subscription(1)["endpoint"]))
    assert await store.device_count("alice") == 1
    assert await store.device_count("bob") == 1


@pytest.mark.asyncio
async def test_resubscribing_is_idempotent_and_a_new_user_takes_the_browser_over(store) -> None:
    first = await subscribe(store, "alice", 1)
    assert await subscribe(store, "alice", 1) == first
    # Alice signed out; Bob signed in on the same browser and turned notifications on.
    assert await subscribe(store, "bob", 1) == first
    assert await store.subscriptions("alice") == []
    assert len(await store.subscriptions("bob")) == 1


@pytest.mark.asyncio
async def test_endpoint_and_keys_are_encrypted_at_rest(store, factory) -> None:
    await subscribe(store, "alice", 1)
    async with factory() as session:
        record = (await session.execute(select(WebPushSubscriptionRecord))).scalar_one()
    raw = f"{record.endpoint_hash} {record.encrypted_subscription}"
    info = subscription(1)
    for secret in (str(info["endpoint"]), "device-token-0001", info["keys"]["p256dh"], info["keys"]["auth"]):  # type: ignore[index]
        assert secret not in raw
    # A different key cannot read it (and it is skipped, not crashed on).
    from cryptography.fernet import Fernet

    other = PushStore(factory, Fernet.generate_key().decode())
    assert await other.subscriptions("alice") == []


@pytest.mark.asyncio
async def test_unknown_push_hosts_are_refused(store) -> None:
    from app.notifications.store import InvalidSubscription

    with pytest.raises(InvalidSubscription):
        await store.save_subscription("alice", SubscriptionIn.model_validate(subscription(1, host="evil.example")))


# Sending ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sends_to_each_of_the_users_browsers_only(store, notifier, sender) -> None:
    await subscribe(store, "alice", 1)
    await subscribe(store, "alice", 2)
    await subscribe(store, "bob", 3)
    result = await notifier.notify("alice", "order_placed", "Order placed", "Zepto confirmed your order.", "/?order=1")
    assert (result.outcome, result.delivered) == ("sent", 2)
    assert {endpoint for endpoint, _ in sender.sent} == {subscription(1)["endpoint"], subscription(2)["endpoint"]}
    assert sender.payloads()[0] == {
        "kind": "order_placed",
        "title": "Order placed",
        "body": "Zepto confirmed your order.",
        "url": "/?order=1",
        "tag": None,
        "suppress_when_visible": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_gone_subscriptions_are_removed(store, notifier, sender, status) -> None:
    await subscribe(store, "alice", 1)
    await subscribe(store, "alice", 2)
    sender.statuses = [status, 201]
    result = await notifier.notify("alice", "delivered", "Delivered", "Delivered.")
    assert (result.outcome, result.delivered, result.removed) == ("sent", 1, 1)
    assert [item.endpoint for item in await store.subscriptions("alice")] == [subscription(2)["endpoint"]]


@pytest.mark.asyncio
async def test_push_service_down_keeps_the_subscription(store, notifier, sender) -> None:
    await subscribe(store, "alice", 1)
    sender.raises = True
    assert (await notifier.notify("alice", "delivered", "Delivered", "Delivered.")).outcome == "failed"
    sender.raises = False
    sender.statuses = [500]
    assert (await notifier.notify("alice", "delivered", "Delivered", "Delivered.")).outcome == "failed"
    assert len(await store.subscriptions("alice")) == 1


@pytest.mark.asyncio
async def test_dedupe_per_event_key(store, notifier, sender) -> None:
    await subscribe(store, "alice", 1)
    await subscribe(store, "bob", 2)
    for _ in range(3):
        await notifier.notify("alice", "delivered", "Delivered", "Delivered.", dedupe_key="track:w1:delivered")
    assert len(sender.sent) == 1
    # The same key for another user is a different event.
    assert (await notifier.notify("bob", "delivered", "Delivered", "Delivered.", dedupe_key="track:w1:delivered")).outcome == "sent"


@pytest.mark.asyncio
async def test_rate_limit_per_user(store, notifier, sender, clock) -> None:
    await subscribe(store, "alice", 1)
    await subscribe(store, "bob", 2)
    outcomes = [
        (await notifier.notify("alice", "delayed", "Running late", "Later.", dedupe_key=f"k{n}")).outcome
        for n in range(RATE_LIMIT + 2)
    ]
    assert outcomes == ["sent"] * RATE_LIMIT + ["rate_limited"] * 2
    assert (await notifier.notify("bob", "delayed", "Running late", "Later.")).outcome == "sent"
    clock.advance(minutes=11)
    assert (await notifier.notify("alice", "delayed", "Running late", "Later.", dedupe_key="later")).outcome == "sent"


@pytest.mark.asyncio
async def test_test_notifications_have_their_own_small_limit(store, notifier) -> None:
    await subscribe(store, "alice", 1)
    outcomes = [(await notifier.send("alice", messages.test_message())).outcome for _ in range(4)]
    assert outcomes == ["sent", "sent", "sent", "rate_limited"]
    # Order updates are not blocked by tests.
    assert (await notifier.notify("alice", "delivered", "Delivered", "Delivered.")).outcome == "sent"


@pytest.mark.asyncio
async def test_kinds_the_user_switched_off_are_not_sent(store, notifier, sender, clock) -> None:
    await subscribe(store, "alice", 1)
    await store.update_preferences("alice", kinds={"delayed": False}, order_prompt_answered=None, at=clock())
    assert (await notifier.notify("alice", "delayed", "Running late", "Later.")).outcome == "opted_out"
    assert (await notifier.notify("alice", "delivered", "Delivered", "Delivered.")).outcome == "sent"
    await store.update_preferences("alice", kinds={"delayed": True}, order_prompt_answered=None, at=clock())
    assert (await notifier.notify("alice", "delayed", "Running late", "Later.")).outcome == "sent"


@pytest.mark.asyncio
async def test_without_vapid_keys_nothing_is_sent(sender) -> None:
    disabled = PushNotifier(None, None)
    assert not disabled.enabled
    assert (await disabled.notify("alice", "delivered", "Delivered", "Delivered.")).outcome == "disabled"
    assert sender.sent == []


@pytest.mark.asyncio
async def test_no_subscription_means_no_event_is_recorded(store, notifier) -> None:
    assert (await notifier.notify("alice", "delivered", "D", "D.", dedupe_key="k")).outcome == "no_subscription"
    await subscribe(store, "alice", 1)
    # Subscribing later still gets the next event with that key.
    assert (await notifier.notify("alice", "delivered", "D", "D.", dedupe_key="k")).outcome == "sent"


@pytest.mark.asyncio
async def test_logs_are_content_free(store, notifier, caplog) -> None:
    await subscribe(store, "alice", 1)
    caplog.set_level(logging.DEBUG, logger="app")
    await notifier.notify("alice", "order_placed", "Order placed", "Zepto confirmed Amul Taaza.", "/?order=SECRET1")
    text = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith("app."))
    assert "event=web_push result=sent kind=order_placed" in text
    for secret in ("Amul", "SECRET1", "device-token", "alice", "authSecret"):
        assert secret not in text


# Triggers ---------------------------------------------------------------------


class Baskets:
    async def get(self, basket_id: str) -> Any:
        names = ["Amul Taaza 1 L", "Bread", "Eggs", "Butter"]
        return SimpleNamespace(lines=[SimpleNamespace(name=name) for name in names])


def events_for(notifier: PushNotifier, clock: Clock, *, online: bool = False, sleeps: list[float] | None = None) -> PushEvents:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        clock.advance(seconds=seconds)

    return PushEvents(notifier, baskets=Baskets(), online_payments_enabled=online, clock=clock, sleep=sleep)


@pytest.mark.asyncio
async def test_order_placed_emits_once_with_at_most_two_names(store, notifier, sender, clock) -> None:
    await subscribe(store, "alice", 1)
    events = events_for(notifier, clock)
    placed = attempt(CheckoutStatus.CONFIRMED)
    events.checkout_outcome(placed)
    events.checkout_outcome(placed)  # a replayed tap
    await events.drain()
    events.checkout_outcome(placed, earlier=True)  # reconciliation of the same order
    await events.drain()
    assert sender.payloads() == [
        {
            "kind": "order_placed",
            "title": "Order placed",
            "body": "Zepto confirmed your order: Amul Taaza 1 L, Bread and 2 more. Total ₹212.50.",
            "url": "/?order=ORD1",
            "tag": "order:ORD1",
            "suppress_when_visible": True,
        }
    ]


@pytest.mark.asyncio
async def test_reconciled_order_says_earlier_order(store, notifier, sender, clock) -> None:
    await subscribe(store, "alice", 1)
    events = events_for(notifier, clock)
    events.checkout_outcome(attempt(CheckoutStatus.CONFIRMED, order_id="ORD9"), earlier=True)
    await events.drain()
    assert sender.payloads()[0]["body"] == "Zepto confirmed your earlier order. Total ₹212.50."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [CheckoutStatus.DISPATCHING, CheckoutStatus.OUTCOME_UNKNOWN, CheckoutStatus.AWAITING_PAYMENT, CheckoutStatus.PAYMENT_FAILED],
)
async def test_cash_on_delivery_non_final_states_are_silent(store, notifier, sender, clock, status) -> None:
    await subscribe(store, "alice", 1)
    events = events_for(notifier, clock, online=True)
    events.checkout_outcome(attempt(status, order_id=None))
    await events.drain()
    assert sender.sent == []


@pytest.mark.asyncio
async def test_payment_notifications_only_with_online_payments_on(store, notifier, sender, clock) -> None:
    await subscribe(store, "alice", 1)
    off = events_for(notifier, clock, online=False)
    off.checkout_outcome(attempt(CheckoutStatus.PAYMENT_FAILED, method="upi_reserve"))
    await off.drain()
    assert sender.sent == []
    on = events_for(notifier, clock, online=True)
    for status in (
        CheckoutStatus.AWAITING_PAYMENT,
        CheckoutStatus.AWAITING_PAYMENT,  # the card polls; told once
        CheckoutStatus.PAYMENT_UNKNOWN,
        CheckoutStatus.PAYMENT_FAILED,
    ):
        on.checkout_outcome(attempt(status, method="upi_reserve"))
        await on.drain()
    assert [payload["title"] for payload in sender.payloads()] == [
        "Finish your payment",
        "Check your payment",
        "Payment failed",
    ]
    assert all(payload["kind"] == "payment" for payload in sender.payloads())


@pytest.mark.asyncio
async def test_still_unknown_emits_once(store, notifier, sender, clock) -> None:
    await subscribe(store, "alice", 1)
    events = events_for(notifier, clock)
    unknown = attempt(CheckoutStatus.OUTCOME_UNKNOWN, order_id=None)
    events.order_still_unknown(unknown)
    events.order_still_unknown(unknown)
    await events.drain()
    assert [payload["title"] for payload in sender.payloads()] == ["Order not confirmed yet"]


def card(state: str = "open") -> SimpleNamespace:
    return SimpleNamespace(
        household_id="alice",
        store="Zepto",
        basket_id="basket-1",
        basket_revision=3,
        expires_at=NOW + timedelta(minutes=5),
        state=state,
    )


@pytest.mark.asyncio
async def test_card_about_to_expire_reminds_once_a_minute_before(store, notifier, sender, clock) -> None:
    await subscribe(store, "alice", 1)
    sleeps: list[float] = []
    events = events_for(notifier, clock, sleeps=sleeps)
    open_card = card()
    events.card_prepared(open_card)
    events.card_prepared(open_card)  # e.g. a payment-method change on the same bill
    await events.drain()
    assert sleeps[0] == 240.0
    assert [payload["kind"] for payload in sender.payloads()] == ["checkout_expiring"]
    assert sender.payloads()[0]["suppress_when_visible"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["used", "cancelled", "superseded", "expired"])
async def test_closed_cards_never_remind(store, notifier, sender, clock, state) -> None:
    await subscribe(store, "alice", 1)
    events = events_for(notifier, clock)
    closed = card()
    events.card_prepared(closed)
    closed.state = state  # the user tapped, cancelled or got a newer card meanwhile
    await events.drain()
    assert sender.sent == []


@pytest.mark.asyncio
async def test_nothing_is_scheduled_when_the_feature_is_off(clock) -> None:
    events = events_for(PushNotifier(None, None), clock)
    events.card_prepared(card())
    events.checkout_outcome(attempt(CheckoutStatus.CONFIRMED))
    assert not events._tasks


class ScriptedStatus:
    provider = TrackingProvider.ZEPTO

    def __init__(self, snapshots: list[TrackingSnapshot]) -> None:
        self.snapshots = snapshots

    async def fetch_delivery_status(self, *, order_id: str, address_id: str | None) -> TrackingSnapshot:
        return self.snapshots.pop(0)


@pytest.mark.asyncio
async def test_tracking_changes_emit_once_each_and_noise_stays_silent(store, notifier, sender, clock, tmp_path) -> None:
    await subscribe(store, "alice", 1)
    eta = NOW + timedelta(minutes=12)
    delivered_at = datetime(2026, 11, 22, 12, 40, tzinfo=UTC)
    snapshots = [
        TrackingSnapshot(status=TrackingStatus.CONFIRMED, eta_at=eta),  # first reading: silent
        TrackingSnapshot(status=TrackingStatus.PREPARING, eta_at=eta),  # packing: silent
        TrackingSnapshot(status=TrackingStatus.PREPARING, eta_at=eta + timedelta(minutes=3)),  # small drift: silent
        TrackingSnapshot(status=TrackingStatus.PREPARING, eta_at=eta + timedelta(minutes=15)),  # delayed
        TrackingSnapshot(status=TrackingStatus.OUT_FOR_DELIVERY, eta_at=eta + timedelta(minutes=15)),
        TrackingSnapshot(status=TrackingStatus.OUT_FOR_DELIVERY, eta_at=eta + timedelta(minutes=15), message="Rider nearby"),
        TrackingSnapshot(status=TrackingStatus.DELIVERED, delivered=True, delivered_at=delivered_at),
    ]
    events = events_for(notifier, clock)
    service = OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.ZEPTO: ScriptedStatus(snapshots)},
        now=clock,
        push=events,
    )
    read = await service.ensure("alice", StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id="ORD1"))
    for _ in range(6):
        clock.advance(minutes=2)
        await service.refresh("alice", read.monitor_id)
    await events.drain()
    assert [(payload["kind"], payload["body"]) for payload in sender.payloads()] == [
        ("delayed", "Zepto moved your delivery to about 5:57 pm."),
        ("out_for_delivery", "Your Zepto order is on the way. Arriving by 5:57 pm."),
        ("delivered", "Your Zepto order was delivered at 6:10 pm."),
    ]
    assert all(payload["url"] == "/?order=ORD1" for payload in sender.payloads())


@pytest.mark.asyncio
async def test_cancelled_by_store_emits_once(store, notifier, sender, clock, tmp_path) -> None:
    await subscribe(store, "alice", 1)
    events = events_for(notifier, clock)
    service = OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {
            TrackingProvider.ZEPTO: ScriptedStatus(
                [TrackingSnapshot(status=TrackingStatus.CONFIRMED), TrackingSnapshot(status=TrackingStatus.CANCELLED, cancelled=True)]
            )
        },
        now=clock,
        push=events,
    )
    read = await service.ensure("alice", StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id="ORD1"))
    clock.advance(minutes=2)
    await service.refresh("alice", read.monitor_id)
    clock.advance(minutes=2)
    await service.refresh("alice", read.monitor_id)  # stopped: no second read
    await events.drain()
    assert [payload["title"] for payload in sender.payloads()] == ["Order cancelled"]


# API --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(notifier) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_push_notifier] = lambda: notifier
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_push_notifier, None)


def as_user(user_id: str) -> dict[str, str]:
    return {"X-Test-User-Id": user_id}


@pytest.mark.asyncio
async def test_api_subscribe_settings_test_and_unsubscribe(client, sender) -> None:
    key = await client.get("/api/notifications/vapid-public-key", headers=as_user("alice"))
    assert key.json() == {"available": True, "public_key": TEST_PUBLIC_KEY}
    created = await client.post("/api/notifications/subscriptions", json=subscription(1), headers=as_user("alice"))
    assert created.status_code == 201
    await client.post("/api/notifications/subscriptions", json=subscription(2), headers=as_user("bob"))

    settings = (await client.get("/api/notifications/settings", headers=as_user("alice"))).json()
    assert settings["available"] and settings["devices"] == 1 and not settings["order_prompt_answered"]
    assert all(kind["enabled"] for kind in settings["kinds"])

    updated = await client.put(
        "/api/notifications/settings",
        json={"kinds": {"delayed": False}, "order_prompt_answered": True},
        headers=as_user("alice"),
    )
    body = updated.json()
    assert body["order_prompt_answered"]
    assert {kind["id"]: kind["enabled"] for kind in body["kinds"]}["delayed"] is False

    test = await client.post("/api/notifications/test", headers=as_user("alice"))
    assert test.status_code == 200 and test.json() == {"delivered": 1, "removed": 0}
    assert [endpoint for endpoint, _ in sender.sent] == [subscription(1)["endpoint"]]
    assert sender.payloads()[0]["suppress_when_visible"] is False

    # Bob's DELETE of Alice's endpoint changes nothing.
    await client.request("DELETE", "/api/notifications/subscriptions", json={"endpoint": subscription(1)["endpoint"]}, headers=as_user("bob"))
    assert (await client.get("/api/notifications/settings", headers=as_user("alice"))).json()["devices"] == 1
    gone = await client.request("DELETE", "/api/notifications/subscriptions", json={"endpoint": subscription(1)["endpoint"]}, headers=as_user("alice"))
    assert gone.status_code == 204
    no_device = await client.post("/api/notifications/test", headers=as_user("alice"))
    assert no_device.status_code == 409


@pytest.mark.asyncio
async def test_api_rejects_unknown_push_hosts_and_bad_keys(client) -> None:
    bad_host = await client.post("/api/notifications/subscriptions", json=subscription(1, host="evil.example"), headers=as_user("alice"))
    assert bad_host.status_code == 422
    bad_keys = subscription(1)
    bad_keys["keys"] = {"p256dh": "<script>", "auth": "x"}
    assert (await client.post("/api/notifications/subscriptions", json=bad_keys, headers=as_user("alice"))).status_code == 422
    unknown_kind = await client.put("/api/notifications/settings", json={"kinds": {"usual_suggestions": True}}, headers=as_user("alice"))
    assert unknown_kind.status_code == 422


@pytest.mark.asyncio
async def test_api_without_vapid_keys_hides_the_feature() -> None:
    app.dependency_overrides[get_push_notifier] = lambda: PushNotifier(None, None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.get("/api/notifications/vapid-public-key")).json() == {"available": False, "public_key": None}
            settings = (await http.get("/api/notifications/settings")).json()
            assert settings["available"] is False and settings["kinds"] == []
            refused = await http.post("/api/notifications/subscriptions", json=subscription(1))
            assert refused.status_code == 503 and "make vapid-keys" in refused.json()["detail"]
            assert (await http.post("/api/notifications/test")).status_code == 503
    finally:
        app.dependency_overrides.pop(get_push_notifier, None)


@pytest.mark.asyncio
async def test_api_test_reports_push_service_failure(client, sender) -> None:
    await client.post("/api/notifications/subscriptions", json=subscription(1), headers=as_user("alice"))
    sender.raises = True
    failed = await client.post("/api/notifications/test", headers=as_user("alice"))
    assert failed.status_code == 502
    assert "push service" in failed.json()["detail"]


# The real sender ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_sender_encrypts_for_the_browser_and_signs_with_vapid(monkeypatch) -> None:
    """pywebpush's request is captured (no network) and decrypted with the browser's key."""

    import base64

    import http_ece
    import pywebpush
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.notifications.sender import VapidConfig, WebPushSender
    from app.notifications.vapid import generate

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    browser_key = ec.generate_private_key(ec.SECP256R1())
    browser_public = browser_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    auth = b"0123456789abcdef"
    captured: dict[str, Any] = {}

    class Response:
        status_code = 201
        reason = "Created"
        text = ""
        headers: dict[str, str] = {}

    def post(endpoint: str, **kwargs: Any) -> Response:
        captured.update(endpoint=endpoint, **kwargs)
        return Response()

    monkeypatch.setattr(pywebpush.requests, "post", post)
    public, private = generate()
    sender = WebPushSender(VapidConfig(public, private, "mailto:owner@example.com"))
    payload = messages.delivered("Zepto", "ORD1", NOW).model_dump_json()
    result = await sender.send(
        {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": b64(browser_public), "auth": b64(auth)}},
        payload,
        ttl=3600,
        urgency="high",
    )

    assert result.ok
    headers = captured["headers"]
    assert captured["endpoint"] == "https://fcm.googleapis.com/fcm/send/abc"
    assert headers["content-encoding"] == "aes128gcm" and headers["ttl"] == "3600" and headers["Urgency"] == "high"
    assert headers["Authorization"].startswith("vapid t=") and f"k={public}" in headers["Authorization"]
    assert payload.encode() not in captured["data"]
    plain = http_ece.decrypt(captured["data"], private_key=browser_key, auth_secret=auth, version="aes128gcm")
    assert plain.decode() == payload


@pytest.mark.asyncio
async def test_real_sender_reports_status_codes_without_raising(monkeypatch) -> None:
    import pywebpush

    from app.notifications.sender import VapidConfig, WebPushSender
    from app.notifications.vapid import generate

    class Gone:
        status_code = 410
        reason = "Gone"
        text = "https://fcm.googleapis.com/secret-endpoint expired"
        headers: dict[str, str] = {}

    monkeypatch.setattr(pywebpush.requests, "post", lambda endpoint, **kwargs: Gone())
    public, private = generate()
    sender = WebPushSender(VapidConfig(public, private, "mailto:owner@example.com"))
    info = subscription(1)
    info["keys"] = {"p256dh": generate()[0], "auth": "MDEyMzQ1Njc4OWFiY2RlZg"}
    assert (await sender.send(info, "{}", ttl=60, urgency="normal")).gone

    def down(endpoint: str, **kwargs: Any) -> None:
        raise ConnectionError("unreachable")

    monkeypatch.setattr(pywebpush.requests, "post", down)
    assert (await sender.send(info, "{}", ttl=60, urgency="normal")).status == 0
