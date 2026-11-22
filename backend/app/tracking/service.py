from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.tracking.errors import TrackingConflict, TrackingNotFound, TrackingProviderError
from app.tracking.models import (
    TERMINAL_STATUSES,
    OrderWatch,
    StartTrackingRequest,
    TrackingEvent,
    TrackingNotification,
    TrackingProvider,
    TrackingRead,
    TrackingSnapshot,
    TrackingStatus,
)
from app.tracking.notifications import meaningful_update, notification_message
from app.tracking.ports import OrderTrackingProvider
from app.tracking.store import OrderTrackingStore


MIN_POLL_INTERVAL_SECONDS = 10
MAX_TRANSIENT_BACKOFF_SECONDS = 30
MAX_NOTIFICATIONS = 20
STORE_LABELS = {TrackingProvider.SWIGGY: "Swiggy Instamart", TrackingProvider.ZEPTO: "Zepto"}


def utc_now() -> datetime:
    return datetime.now(UTC)


class OrderTrackingService:
    """Persisted safe polling policy for read-only delivery status calls."""

    def __init__(
        self,
        store: OrderTrackingStore,
        providers: dict[TrackingProvider, OrderTrackingProvider],
        *,
        now: Callable[[], datetime] = utc_now,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.store = store
        self.providers = providers
        self.now = now
        self.jitter = jitter
        self._locks: dict[tuple[str, TrackingProvider, str], asyncio.Lock] = {}

    def _lock_for(
        self, user_id: str, provider: TrackingProvider, order_id: str
    ) -> asyncio.Lock:
        key = (user_id, provider, order_id)
        return self._locks.setdefault(key, asyncio.Lock())

    async def start(self, user_id: str, request: StartTrackingRequest) -> TrackingRead:
        now = self.now()
        try:
            watch = self.store.get(user_id, request.provider, request.order_id)
            if watch.active:
                raise TrackingConflict("This order is already being tracked.")
            watch.active = True
            watch.address_id = request.address_id or watch.address_id
            watch.stop_reason = None
            watch.stopped_at = None
            watch.consecutive_failures = 0
            watch.last_error = None
            watch.next_check_at = now
            watch.updated_at = now
        except TrackingNotFound:
            watch = OrderWatch(
                id=str(uuid4()),
                user_id=user_id,
                provider=request.provider,
                order_id=request.order_id,
                address_id=request.address_id,
                next_check_at=now,
                created_at=now,
                updated_at=now,
            )
        self.store.save(watch)
        # An immediate read is safe: this boundary exposes read-only operations only.
        return await self.refresh(user_id, watch.id)

    async def ensure(self, user_id: str, request: StartTrackingRequest) -> TrackingRead:
        """Start tracking an order once; a second call returns the existing watch.

        Used after a provider-confirmed order: replaying the confirmation, a
        retried request or two tabs never create a second watch, and an existing
        watch (active, finished or stopped by the user) is left as it is.
        """

        async with self._lock_for(user_id, request.provider, request.order_id):
            try:
                watch = self.store.get(user_id, request.provider, request.order_id)
            except TrackingNotFound:
                watch = None
            if watch is None:
                now = self.now()
                watch = OrderWatch(
                    id=str(uuid4()),
                    user_id=user_id,
                    provider=request.provider,
                    order_id=request.order_id,
                    address_id=request.address_id,
                    next_check_at=now,
                    created_at=now,
                    updated_at=now,
                )
                self.store.save(watch)
                created = True
            else:
                created = False
        if created:
            return await self.refresh(user_id, watch.id)
        return TrackingRead(
            order=watch, checked_at=watch.checked_at, next_check_at=watch.next_check_at
        )

    def find(self, user_id: str, order_ref: str) -> OrderWatch | None:
        """This household's watch for an order id or store order code ("#Z123"), or
        its most recently created watch for "latest"."""

        watches = self.store.list(user_id)
        if not watches:
            return None
        reference = order_ref.strip().lstrip("#").strip().casefold()
        if reference in {"", "latest"}:
            return max(watches, key=lambda item: item.created_at)
        for watch in watches:
            code = watch.snapshot.order_code if watch.snapshot else None
            if watch.order_id.casefold() == reference or (code and code.casefold() == reference):
                return watch
        return None

    def list(self, user_id: str, *, active_only: bool = False) -> list[OrderWatch]:
        return self.store.list(user_id, active_only=active_only)

    def get(self, user_id: str, monitor_id: str) -> TrackingRead:
        watch = self.store.get_by_id(user_id, monitor_id)
        return TrackingRead(
            order=watch, checked_at=watch.checked_at, next_check_at=watch.next_check_at
        )

    async def refresh(self, user_id: str, monitor_id: str) -> TrackingRead:
        watch = self.store.get_by_id(user_id, monitor_id)
        async with self._lock_for(user_id, watch.provider, watch.order_id):
            # Reload inside the lock so concurrent manual refreshes coalesce.
            watch = self.store.get_by_id(user_id, monitor_id)
            if not watch.active:
                return TrackingRead(
                    order=watch,
                    checked_at=watch.checked_at,
                    next_check_at=watch.next_check_at,
                )
            now = self.now()
            # Manual requests never defeat provider cadence.  This is both a
            # rate-limit guard and a coalescing rule for several browser tabs.
            if watch.next_check_at is not None and watch.next_check_at > now:
                return TrackingRead(
                    order=watch,
                    checked_at=watch.checked_at,
                    next_check_at=watch.next_check_at,
                )
            adapter = self.providers.get(watch.provider)
            if adapter is None:
                raise TrackingProviderError(
                    "Live tracking is not configured for this provider.", transient=False
                )
            try:
                snapshot = await adapter.fetch_delivery_status(
                    order_id=watch.order_id, address_id=watch.address_id
                )
            except TrackingProviderError as exc:
                return self._record_provider_failure(watch, exc, now)

            changed = _meaningfully_changed(watch.snapshot, snapshot)
            kind = meaningful_update(watch.snapshot, snapshot)
            if kind is not None:
                watch.notifications = (
                    watch.notifications
                    + [
                        TrackingNotification(
                            id=str(uuid4()),
                            kind=kind,
                            occurred_at=now,
                            message=notification_message(kind, STORE_LABELS[watch.provider]),
                            eta_at=snapshot.eta_at,
                        )
                    ]
                )[-MAX_NOTIFICATIONS:]
            watch.snapshot = snapshot
            watch.status = _effective_status(snapshot)
            watch.checked_at = now
            watch.status_at = now
            watch.updated_at = now
            watch.last_error = None
            watch.consecutive_failures = 0
            if changed:
                watch.last_fingerprint = snapshot.fingerprint()
                watch.events = (watch.events + [TrackingEvent(occurred_at=now, snapshot=snapshot)])[-100:]

            if snapshot.disabled:
                return self._stop(watch, now, "provider_tracking_disabled", changed)
            if snapshot.terminal:
                return self._stop(watch, now, f"terminal_{watch.status.value}", changed)

            watch.next_check_at = now + timedelta(
                seconds=_poll_interval_seconds(snapshot.poll_interval_seconds)
            )
            self.store.save(watch)
            return TrackingRead(
                order=watch,
                changed=changed,
                checked_at=watch.checked_at,
                next_check_at=watch.next_check_at,
            )

    def stop(self, user_id: str, monitor_id: str) -> TrackingRead:
        watch = self.store.get_by_id(user_id, monitor_id)
        now = self.now()
        return self._stop(watch, now, "stopped_by_user", changed=False)

    async def poll_due(self, user_id: str | None = None) -> list[TrackingRead]:
        """Refresh only due watches. Used by the app-owned background loop."""

        now = self.now()
        users = [user_id] if user_id else self.store.list_users()
        due: list[OrderWatch] = []
        for current_user in users:
            due.extend(
                watch
                for watch in self.store.list(current_user, active_only=True)
                if watch.next_check_at is None or watch.next_check_at <= now
            )
        return [
            await self.refresh(watch.user_id, watch.id)
            for watch in due
        ]

    def _record_provider_failure(
        self, watch: OrderWatch, error: TrackingProviderError, now: datetime
    ) -> TrackingRead:
        watch.checked_at = now
        watch.updated_at = now
        watch.last_error = _safe_error(error)
        if error.reconnect_required:
            watch.status = TrackingStatus.PAUSED
            return self._stop(watch, now, "provider_reconnect_required", changed=False)
        if not error.transient:
            watch.status = TrackingStatus.UNAVAILABLE
            return self._stop(watch, now, "provider_unavailable", changed=False)

        watch.consecutive_failures += 1
        # Provider Retry-After wins: do not stack speculative exponential delay on it.
        seconds = (
            error.retry_after_seconds
            if error.retry_after_seconds is not None
            else _transient_backoff_seconds(watch.consecutive_failures, self.jitter)
        )
        watch.next_check_at = now + timedelta(seconds=max(1, seconds))
        self.store.save(watch)
        return TrackingRead(
            order=watch,
            changed=False,
            checked_at=watch.checked_at,
            next_check_at=watch.next_check_at,
        )

    def _stop(
        self, watch: OrderWatch, now: datetime, reason: str, changed: bool
    ) -> TrackingRead:
        watch.active = False
        watch.stopped_at = now
        watch.stop_reason = reason
        watch.next_check_at = None
        watch.updated_at = now
        self.store.save(watch)
        return TrackingRead(
            order=watch,
            changed=changed,
            checked_at=watch.checked_at,
            next_check_at=None,
        )


class OrderTrackingPoller:
    """App-owned periodic loop. It only invokes due read-only provider checks."""

    def __init__(
        self,
        service: OrderTrackingService,
        *,
        sleep: Callable[[float], object] = asyncio.sleep,
        idle_seconds: float = 5.0,
    ) -> None:
        self.service = service
        self.sleep = sleep
        self.idle_seconds = idle_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="order-tracking-poller")

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        consecutive_cycle_failures = 0
        while not self._stop.is_set():
            try:
                await self.service.poll_due()
                consecutive_cycle_failures = 0
            except Exception:
                # A bad local cache or unanticipated adapter parsing issue must
                # not silently end all active monitors.  Individual provider
                # failures are handled and persisted by the service; this is a
                # final process-safety net that never logs raw provider text.
                consecutive_cycle_failures += 1
            delay = min(
                30.0,
                self.idle_seconds * (2 ** max(consecutive_cycle_failures - 1, 0)),
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass


def _effective_status(snapshot: TrackingSnapshot) -> TrackingStatus:
    if snapshot.delivered:
        return TrackingStatus.DELIVERED
    if snapshot.cancelled:
        return TrackingStatus.CANCELLED
    return snapshot.status


def _meaningfully_changed(
    previous: TrackingSnapshot | None, current: TrackingSnapshot
) -> bool:
    """Avoid noisy events for tiny ETA drift while preserving material changes."""

    if previous is None:
        return True
    if (
        previous.status != current.status
        or previous.provider_status != current.provider_status
        or previous.message != current.message
        or previous.delivered != current.delivered
        or previous.cancelled != current.cancelled
        or previous.disabled != current.disabled
    ):
        return True
    if previous.eta_at is None or current.eta_at is None:
        return previous.eta_at != current.eta_at
    return abs((current.eta_at - previous.eta_at).total_seconds()) >= 300


def _poll_interval_seconds(provider_seconds: int | None) -> int:
    if provider_seconds is None:
        return 60
    # A provider-specified interval is a quota/safety contract.  Never cap it
    # downward: that would poll sooner than the provider explicitly requested.
    return max(MIN_POLL_INTERVAL_SECONDS, provider_seconds)


def _transient_backoff_seconds(
    failures: int, jitter: Callable[[float, float], float]
) -> int:
    base = min(MAX_TRANSIENT_BACKOFF_SECONDS, 5 * (2 ** max(failures - 1, 0)))
    return max(1, min(MAX_TRANSIENT_BACKOFF_SECONDS, round(jitter(base * 0.8, base * 1.2))))


def _safe_error(error: TrackingProviderError) -> str:
    # Provider/transport text may contain headers, callback URLs, or an order
    # identifier. Persist a stable local message only; details belong in secure
    # server logs when we add structured observability.
    if error.reconnect_required:
        return "Reconnect the provider to resume order tracking."
    if error.retry_after_seconds is not None:
        return "The provider is temporarily rate-limiting delivery updates."
    if not error.transient:
        return "Live order tracking is unavailable for this provider."
    return "The provider could not refresh this order right now."
