"""Send browser notifications for meaningful order updates, once each.

``PushNotifier.notify`` is the one entry point (also for future features):
it checks the feature is configured, the user's per-kind setting, dedupes per
event key, rate-limits per user, sends to each of the user's browsers and
deletes subscriptions the push service reports gone (404/410). Logs carry the
kind and counts only, never titles, bodies, endpoints or keys.

``PushEvents`` maps app events (checkout outcomes, reconciliation, tracking
changes, an order card about to expire) to messages and runs them in the
background so a slow push service never delays an order or a status read.

Double notifications: the service worker shows nothing while a GharSaathi tab
is visible and focused (``suppress_when_visible``); that tab already shows the
in-app toast or card. See docs/ARCHITECTURE.md, "Browser notifications".
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from app.notifications import messages
from app.notifications.models import PushPayload, SendKind
from app.notifications.sender import PushSender
from app.notifications.store import PushStore

logger = logging.getLogger(__name__)

# Per user: at most this many order notifications in the window. A normal
# order produces about four (placed, out for delivery, delivered, maybe late).
RATE_LIMIT = 8
RATE_WINDOW = timedelta(minutes=10)
TEST_RATE_LIMIT = 3
TEST_RATE_WINDOW = timedelta(minutes=1)
EVENT_RETENTION = timedelta(days=30)
TTL_SECONDS = 60 * 60  # an order update older than an hour is not worth showing
EXPIRY_WARNING = timedelta(seconds=60)

NotifyOutcome = Literal[
    "sent", "disabled", "opted_out", "no_subscription", "duplicate", "rate_limited", "failed"
]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class NotifyResult:
    outcome: NotifyOutcome
    delivered: int = 0
    removed: int = 0


class PushNotifier:
    def __init__(
        self,
        store: PushStore | None,
        sender: PushSender | None,
        *,
        public_key: str | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.sender = sender
        self.public_key = public_key
        self.clock = clock
        self._pruned_at: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.store is not None and self.sender is not None and bool(self.public_key)

    async def notify(
        self,
        user_id: str,
        kind: SendKind,
        title: str,
        body: str,
        url: str = "/",
        *,
        dedupe_key: str | None = None,
        tag: str | None = None,
        suppress_when_visible: bool = True,
    ) -> NotifyResult:
        """Send one notification to every browser this user turned notifications on in."""

        payload = PushPayload(
            kind=kind, title=title, body=body, url=url, tag=tag, suppress_when_visible=suppress_when_visible
        )
        return await self.send(user_id, payload, dedupe_key=dedupe_key)

    async def send(self, user_id: str, payload: PushPayload, *, dedupe_key: str | None = None) -> NotifyResult:
        if not self.enabled or not user_id:
            return NotifyResult("disabled")
        assert self.store is not None and self.sender is not None
        store, sender = self.store, self.sender
        kind = payload.kind
        if not (await store.preferences(user_id)).allows(kind):
            return self._log(kind, NotifyResult("opted_out"))
        subscriptions = await store.subscriptions(user_id)
        if not subscriptions:
            return self._log(kind, NotifyResult("no_subscription"))
        now = self.clock()
        test = kind == "test"
        key = (dedupe_key or f"{kind}:{uuid.uuid4().hex}")[:200]
        event_id = await store.claim_event(user_id, key, kind, now)
        if event_id is None:
            return self._log(kind, NotifyResult("duplicate"))
        limit, window = (TEST_RATE_LIMIT, TEST_RATE_WINDOW) if test else (RATE_LIMIT, RATE_WINDOW)
        # The claimed row is itself pending, so it counts towards the limit.
        if await store.count_sent(user_id, since=now - window, test=test) > limit:
            await store.finish_event(event_id, "rate_limited")
            return self._log(kind, NotifyResult("rate_limited"))
        data = payload.model_dump_json()
        urgency = "normal" if kind in {"order_check"} else "high"
        delivered = removed = 0
        for subscription in subscriptions:
            try:
                result = await sender.send(subscription.info(), data, ttl=TTL_SECONDS, urgency=urgency)
            except Exception:  # noqa: BLE001 - one browser must not stop the others
                result = None
            if result is not None and result.ok:
                delivered += 1
                await store.record_result(subscription.id, ok=True, at=now)
            elif result is not None and result.gone:
                removed += 1
                await store.remove_by_id(subscription.id)
            else:
                await store.record_result(subscription.id, ok=False, at=now)
        await store.finish_event(event_id, "sent" if delivered else "failed")
        if self._pruned_at is None or now - self._pruned_at > timedelta(hours=1):
            self._pruned_at = now
            await store.prune_events(now - EVENT_RETENTION)
        return self._log(kind, NotifyResult("sent" if delivered else "failed", delivered, removed))

    @staticmethod
    def _log(kind: str, result: NotifyResult) -> NotifyResult:
        logger.info(
            "event=web_push result=%s kind=%s delivered=%d removed=%d",
            result.outcome,
            kind,
            result.delivered,
            result.removed,
        )
        return result


class BasketNames(Protocol):
    async def get(self, basket_id: str) -> Any: ...


class PushEvents:
    """App events → at most one notification each, sent in the background."""

    def __init__(
        self,
        notifier: PushNotifier,
        *,
        baskets: BasketNames | None = None,
        online_payments_enabled: bool = False,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.notifier = notifier
        self.baskets = baskets
        self.online_payments_enabled = online_payments_enabled
        self.clock = clock
        self.sleep = sleep
        self._tasks: set[asyncio.Task[Any]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def _spawn(self, work: Callable[[], Awaitable[Any]], user_id: str | None = None) -> None:
        """Run ``work`` in the background. Work for one user runs in order (FIFO
        lock), so "delivered" can never arrive before "out for delivery"."""

        if not self.notifier.enabled:
            return
        lock = self._locks.setdefault(user_id, asyncio.Lock()) if user_id else None

        async def run() -> None:
            try:
                if lock is None:
                    await work()
                else:
                    async with lock:
                        await work()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a notification never breaks the app
                logger.warning("event=web_push_event_failed error=%s", type(exc).__name__)

        try:
            task = asyncio.get_running_loop().create_task(run())
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for queued notifications (tests, shutdown)."""

        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # Checkout ---------------------------------------------------------------

    def checkout_outcome(self, attempt: Any, *, store: str = "Zepto", earlier: bool = False) -> None:
        """A checkout attempt reached a state worth telling the user about."""

        status = getattr(getattr(attempt, "status", None), "value", getattr(attempt, "status", None))
        user_id = attempt.household_id
        if status == "confirmed" and attempt.provider_order_id:
            order_id = attempt.provider_order_id

            async def placed() -> None:
                names = await self._item_names(attempt.basket_id) if not earlier else []
                await self.notifier.send(
                    user_id,
                    messages.order_placed(store, order_id, attempt.payable_paise, names, earlier=earlier),
                    dedupe_key=f"order_placed:zepto:{order_id}",
                )

            self._spawn(placed, user_id)
            return
        if not self.online_payments_enabled or attempt.payment_method == "cod":
            return
        payload: PushPayload | None = {
            "awaiting_payment": messages.payment_needed(store, attempt.payable_paise),
            "payment_unknown": messages.payment_unclear(store),
            "payment_failed": messages.payment_failed(store),
            "paid_order_failed": messages.paid_order_failed(store),
        }.get(str(status))
        if payload is None:
            return
        key = f"payment:{attempt.id}:{status}"
        self._spawn(lambda: self.notifier.send(user_id, payload, dedupe_key=key), user_id)

    def order_still_unknown(self, attempt: Any, *, store: str = "Zepto") -> None:
        """The bounded follow-up checks ended and the store still hasn't confirmed."""

        payload = messages.order_still_unknown(store, attempt.payable_paise)
        key = f"order_check:{attempt.id}:unknown"
        self._spawn(lambda: self.notifier.send(attempt.household_id, payload, dedupe_key=key), attempt.household_id)

    async def _item_names(self, basket_id: str) -> Sequence[str]:
        if self.baskets is None:
            return []
        try:
            basket = await self.baskets.get(basket_id)
        except Exception:  # noqa: BLE001 - names are optional
            return []
        return [line.name for line in getattr(basket, "lines", [])] if basket is not None else []

    # Tracking ---------------------------------------------------------------

    def tracking_update(self, watch: Any, kind: str, snapshot: Any, *, store: str) -> None:
        order_id = watch.order_id
        if kind == "out_for_delivery":
            payload = messages.out_for_delivery(store, order_id, snapshot.eta_at)
            key = f"track:{watch.id}:out_for_delivery"
        elif kind == "delivered":
            payload = messages.delivered(store, order_id, snapshot.delivered_at)
            key = f"track:{watch.id}:delivered"
        elif kind == "cancelled":
            payload = messages.cancelled(store, order_id)
            key = f"track:{watch.id}:cancelled"
        elif kind == "delayed":
            payload = messages.delayed(store, order_id, snapshot.eta_at)
            eta = snapshot.eta_at.isoformat() if snapshot.eta_at else "none"
            key = f"track:{watch.id}:delayed:{eta}"
        else:
            return
        self._spawn(lambda: self.notifier.send(watch.user_id, payload, dedupe_key=key), watch.user_id)

    # Order card -------------------------------------------------------------

    def card_prepared(self, confirmation: Any) -> None:
        """Remind once, a minute before an untapped order card expires.

        The service worker drops it while the app is visible, so it reaches
        only someone who left mid-checkout.
        """

        async def remind() -> None:
            wait = (_utc(confirmation.expires_at) - _utc(self.clock()) - EXPIRY_WARNING).total_seconds()
            if wait <= 0:
                return
            await self.sleep(wait)
            if confirmation.state != "open" or _utc(self.clock()) >= _utc(confirmation.expires_at):
                return
            await self.notifier.send(
                confirmation.household_id,
                messages.checkout_expiring(confirmation.store),
                dedupe_key=f"checkout_expiring:{confirmation.basket_id}:{confirmation.basket_revision}",
            )

        self._spawn(remind)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
