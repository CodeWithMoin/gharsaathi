"""Read-only reconciliation of Zepto checkouts whose outcome is unknown.

When the single order call times out or returns a response we cannot parse,
the attempt is ``outcome_unknown``: Zepto may or may not have placed the
order, and the order call is never repeated. This module answers the question
from Zepto's own read-only order history instead:

* ``list_order_history`` (first page), then ``get_order_detail`` for orders
  whose grand total equals the attempt's approved payable amount;
* an order matches only if it was placed between ``created_at - 2 min`` and
  ``completed_at + 10 min``, its grand total equals the payable amount, its
  item count fits the basket, and every basket line matches one ordered
  product (by provider id, or by normalised name) with the same quantity.

Exactly one match moves the attempt out of ``outcome_unknown`` (COD:
``confirmed``; prepaid: ``awaiting_payment``, so the existing single
``check_payment_status`` reads decide the payment). No match, several matches,
or an unreadable history keeps the attempt unknown; this module never guesses
and never calls a create/confirm tool. Logs carry only counts and outcomes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from app.commerce.zepto.history import ZeptoOrderDetail, ZeptoOrderHistory
from app.shopping.models import Basket, CheckoutAttempt, CheckoutStatus


logger = logging.getLogger(__name__)

WINDOW_BEFORE = timedelta(minutes=2)
WINDOW_AFTER = timedelta(minutes=10)
HISTORY_LIMIT = 10
MAX_DETAILS = 5
# A household's repeated checks within this interval reuse the last answer.
MIN_CHECK_INTERVAL = timedelta(seconds=15)
# Automatic follow-up after an unknown outcome: three reads over about two minutes.
FOLLOW_UP_DELAYS: tuple[float, ...] = (15.0, 45.0, 60.0)
RECONCILED_STATUS = "reconciled_from_history"
# Unknown attempts older than this are left to the maintenance command.
RECENT = timedelta(hours=24)

CheckResult = Literal["matched", "not_found", "ambiguous", "unavailable", "not_unknown"]


class OrderHistoryReader(Protocol):
    """The two read-only Zepto tools used here (``ZeptoService``)."""

    async def order_history(self, *, limit: int = 10, page_number: int = 1) -> ZeptoOrderHistory: ...

    async def order_detail(self, order_id: str) -> ZeptoOrderDetail: ...


class ReconcilableAttempts(Protocol):
    async def get(self, attempt_id: str) -> CheckoutAttempt | None: ...

    async def unknown_attempts(
        self, household_id: str | None = None, *, since: datetime | None = None
    ) -> list[CheckoutAttempt]: ...

    async def reconcile(
        self, attempt_id: str, *, provider_order_id: str, at: datetime
    ) -> CheckoutAttempt: ...


class BasketReader(Protocol):
    async def get(self, basket_id: str) -> Basket | None: ...


class TrackingStarter(Protocol):
    async def ensure(self, user_id: str, request: Any) -> Any: ...


@dataclass(frozen=True)
class Candidate:
    order_id: str
    order_code: str


@dataclass(frozen=True)
class ReconcileOutcome:
    """The result of one reconciliation check; safe to show and to log by field."""

    check: CheckResult
    attempt: CheckoutAttempt | None
    order_id: str | None = None
    order_code: str | None = None
    # Codes of the orders that could match, when there is more than one.
    candidates: tuple[str, ...] = ()
    # True only for the call that moved the attempt out of outcome_unknown.
    newly_reconciled: bool = False
    tracking: Any = field(default=None, compare=False)

    @property
    def placed(self) -> bool:
        return (
            self.attempt is not None
            and self.attempt.status == CheckoutStatus.CONFIRMED
            and bool(self.attempt.provider_order_id)
        )


def utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_placed_time(value: object) -> datetime | None:
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # A timezone-free time cannot be placed in the window with certainty.
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalise_name(value: str) -> str:
    return " ".join(_NON_WORD.sub(" ", value.casefold()).split())


def window(attempt: CheckoutAttempt) -> tuple[datetime, datetime]:
    start = _utc(attempt.created_at) - WINDOW_BEFORE
    end = _utc(attempt.completed_at or attempt.created_at) + WINDOW_AFTER
    return start, end


def detail_matches(attempt: CheckoutAttempt, basket: Basket, detail: ZeptoOrderDetail) -> bool:
    """Whether one order detail is exactly this attempt's order. Pure; no I/O."""

    placed = parse_placed_time(detail.placed_time)
    start, end = window(attempt)
    if placed is None or not start <= placed <= end:
        return False
    if detail.grand_total_paise != attempt.payable_paise:
        return False
    units = sum(line.quantity for line in basket.lines)
    if detail.item_quantity_count not in {units, len(basket.lines)}:
        return False
    products = [product for shipment in detail.shipments for product in shipment.products]
    if len(products) != len(basket.lines):
        return False
    unused = list(products)
    for line in basket.lines:
        ids = {value for value in (line.ref.product_id, line.ref.variant_id, line.ref.sku_id) if value}
        names = {normalise_name(line.name)}
        if line.pack_size:
            names.add(normalise_name(f"{line.name} {line.pack_size}"))
        match = next(
            (
                product
                for product in unused
                if product.quantity_ordered == line.quantity
                and (
                    product.id in ids
                    or normalise_name(product.product_name) in names
                    or normalise_name(f"{product.product_name} {product.pack_size}") in names
                    or normalise_name(f"{product.brand} {product.product_name}") in names
                )
            ),
            None,
        )
        if match is None:
            return False
        unused.remove(match)
    return True


async def find_matches(
    attempt: CheckoutAttempt, basket: Basket, history: OrderHistoryReader
) -> tuple[CheckResult, list[Candidate]]:
    """Read Zepto's history and return the orders matching this attempt.

    Read-only: only ``order_history`` and ``order_detail`` are called.
    """

    page = await history.order_history(limit=HISTORY_LIMIT, page_number=1)
    totals = [order for order in page.orders if order.grand_total_paise == attempt.payable_paise]
    if len(totals) > MAX_DETAILS:
        return "ambiguous", [Candidate(order.id, order.code) for order in totals]
    matches: list[Candidate] = []
    unreadable = 0
    for order in totals:
        try:
            detail = await history.order_detail(order.id)
        except Exception as exc:  # noqa: BLE001 - one unreadable candidate makes the answer uncertain
            logger.info("event=checkout_reconcile_detail_failed error=%s", type(exc).__name__)
            unreadable += 1
            continue
        if detail_matches(attempt, basket, detail):
            matches.append(Candidate(order.id, detail.code or order.code))
    if len(matches) > 1:
        return "ambiguous", matches
    if unreadable:
        # A candidate we could not read might also match: never settle on one.
        return "unavailable", matches
    if len(matches) == 1:
        return "matched", matches
    return "not_found", []


class CheckoutReconciler:
    """Settle ``outcome_unknown`` checkouts from Zepto's read-only order history."""

    def __init__(
        self,
        attempts: ReconcilableAttempts,
        baskets: BasketReader,
        history: OrderHistoryReader | None,
        *,
        tracking: TrackingStarter | None = None,
        clock: Callable[[], datetime] = utc_now,
        min_interval: timedelta = MIN_CHECK_INTERVAL,
        sleep: Callable[[float], Any] = asyncio.sleep,
        follow_up_delays: Sequence[float] = FOLLOW_UP_DELAYS,
    ) -> None:
        self.attempts = attempts
        self.baskets = baskets
        self.history = history
        self.tracking = tracking
        self.clock = clock
        self.min_interval = min_interval
        self.sleep = sleep
        self.follow_up_delays = tuple(follow_up_delays)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, tuple[datetime, ReconcileOutcome]] = {}
        self._checks: dict[str, int] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    def checks_made(self, attempt_id: str) -> int:
        return self._checks.get(attempt_id, 0)

    async def reconcile(
        self, attempt_id: str, *, household_id: str | None = None, apply: bool = True
    ) -> ReconcileOutcome:
        """One read-only check for one attempt. ``apply=False`` never writes."""

        lock = self._locks.setdefault(attempt_id, asyncio.Lock())
        async with lock:
            attempt = await self.attempts.get(attempt_id)
            if attempt is None or (household_id is not None and attempt.household_id != household_id):
                return ReconcileOutcome("not_unknown", None)
            if attempt.status != CheckoutStatus.OUTCOME_UNKNOWN:
                outcome = ReconcileOutcome(
                    "not_unknown", attempt, order_id=attempt.provider_order_id
                )
                return await self._with_tracking(outcome)
            now = self.clock()
            cached = self._last.get(attempt_id)
            if apply and cached is not None and _utc(now) - _utc(cached[0]) < self.min_interval:
                return cached[1]
            outcome = await self._check(attempt, apply=apply)
            if apply:
                self._last[attempt_id] = (now, outcome)
            return await self._with_tracking(outcome)

    async def _check(self, attempt: CheckoutAttempt, *, apply: bool) -> ReconcileOutcome:
        self._checks[attempt.id] = self._checks.get(attempt.id, 0) + 1
        if self.history is None:
            return ReconcileOutcome("unavailable", attempt)
        basket = await self.baskets.get(attempt.basket_id)
        if basket is None or not basket.lines:
            logger.warning("event=checkout_reconcile result=unavailable reason=basket_missing")
            return ReconcileOutcome("unavailable", attempt)
        try:
            result, matches = await find_matches(attempt, basket, self.history)
        except Exception as exc:  # noqa: BLE001 - a failed read never changes the attempt
            logger.info("event=checkout_reconcile result=unavailable error=%s", type(exc).__name__)
            return ReconcileOutcome("unavailable", attempt)
        logger.info(
            "event=checkout_reconcile result=%s candidates=%d method=%s",
            result,
            len(matches),
            attempt.payment_method,
        )
        if result != "matched":
            return ReconcileOutcome(
                result,
                attempt,
                candidates=tuple(candidate.order_code for candidate in matches)
                if result == "ambiguous"
                else (),
            )
        match = matches[0]
        if not apply:
            return ReconcileOutcome("matched", attempt, order_id=match.order_id, order_code=match.order_code)
        updated = await self.attempts.reconcile(
            attempt.id, provider_order_id=match.order_id, at=self.clock()
        )
        newly = updated.provider_order_id == match.order_id and updated.status != CheckoutStatus.OUTCOME_UNKNOWN
        return ReconcileOutcome(
            "matched",
            updated,
            order_id=updated.provider_order_id,
            order_code=match.order_code,
            newly_reconciled=newly,
        )

    async def _with_tracking(self, outcome: ReconcileOutcome) -> ReconcileOutcome:
        """Start (or find) the read-only status monitor for a placed order."""

        if not outcome.placed or self.tracking is None or outcome.attempt is None:
            return outcome
        from app.tracking.models import StartTrackingRequest, TrackingProvider

        try:
            tracking = await self.tracking.ensure(
                outcome.attempt.household_id,
                StartTrackingRequest(
                    provider=TrackingProvider.ZEPTO, order_id=outcome.attempt.provider_order_id or ""
                ),
            )
        except Exception as exc:  # noqa: BLE001 - the order stands; tracking is best effort
            logger.warning("event=checkout_reconcile_track_failed error=%s", type(exc).__name__)
            return outcome
        code = outcome.order_code
        snapshot = getattr(getattr(tracking, "order", None), "snapshot", None)
        if code is None and snapshot is not None:
            code = getattr(snapshot, "order_code", None)
        return ReconcileOutcome(
            outcome.check,
            outcome.attempt,
            order_id=outcome.order_id or outcome.attempt.provider_order_id,
            order_code=code,
            candidates=outcome.candidates,
            newly_reconciled=outcome.newly_reconciled,
            tracking=tracking,
        )

    async def pending(self, household_id: str) -> list[CheckoutAttempt]:
        """This household's recent unknown checkouts (database only, no provider call)."""

        since = _utc(self.clock()) - RECENT
        return await self.attempts.unknown_attempts(household_id, since=since)

    async def reconcile_household(self, household_id: str) -> list[ReconcileOutcome]:
        """One check for each of this household's recent unknown checkouts."""

        return [
            await self.reconcile(attempt.id, household_id=household_id)
            for attempt in await self.pending(household_id)
        ]

    def follow_up(
        self,
        attempt_id: str,
        on_final: Callable[[ReconcileOutcome], Any] | None = None,
    ) -> asyncio.Task[ReconcileOutcome | None]:
        """Background checks after an unknown outcome (bounded, read-only)."""

        task = asyncio.create_task(self._follow_up(attempt_id, on_final))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _follow_up(
        self, attempt_id: str, on_final: Callable[[ReconcileOutcome], Any] | None
    ) -> ReconcileOutcome | None:
        outcome: ReconcileOutcome | None = None
        try:
            for delay in self.follow_up_delays:
                await self.sleep(delay)
                outcome = await self.reconcile(attempt_id)
                if outcome.check in {"matched", "not_unknown", "ambiguous"}:
                    break
            if outcome is not None and on_final is not None:
                result = on_final(outcome)
                if asyncio.iscoroutine(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a background check must never crash the app
            logger.warning("event=checkout_reconcile_follow_up_failed error=%s", type(exc).__name__)
        return outcome
