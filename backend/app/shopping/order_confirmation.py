"""One-tap order confirmations: the only way voice or typed chat leads to an order.

The assistant may *prepare* an order (``OrderConfirmations.prepare``). That
binds a short-lived, single-use confirmation to the exact basket revision, the
open approval, its quote and payable amount, and the delivery address, and the
browser shows it as a card with a "Place order" button. Nothing is approved or
ordered until the signed-in user taps that button, which calls
``OrderConfirmations.place``. ``place`` re-checks every binding, then runs the
existing approval and Zepto COD checkout path (same locks, durable one-shot
claim and outcome-unknown handling). Cancel invalidates the confirmation.

Confirmations live in process memory: a backend restart drops them and the
user asks for a fresh card. The order itself stays one-shot across processes
because the checkout claim is unique per approval in PostgreSQL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.shopping.approval import ApprovalService
from app.shopping.basket import Clock, utc_now
from app.shopping.money import format_rupees
from app.shopping.checkout import CheckoutService
from app.shopping.errors import ApprovalRefused, NotFound, ShoppingError
from app.shopping.models import (
    Approval,
    ApprovalStatus,
    Basket,
    BasketStatus,
    CheckoutStatus,
    ProviderName,
    Quote,
)
from app.shopping.ports import BasketRepository


logger = logging.getLogger(__name__)

CONFIRMATION_TTL = timedelta(minutes=5)
# Confirmations are kept this long after they close, so a late tap gets a clear
# "already used/cancelled" answer instead of "not found".
RETENTION = timedelta(hours=1)
MAX_CONFIRMATIONS = 2000
ORDERABLE_PROVIDERS: frozenset[ProviderName] = frozenset({"zepto"})

ConfirmationState = Literal["open", "cancelled", "used", "superseded", "expired"]


@dataclass(frozen=True)
class OrderResult:
    """What happened to one confirmation, as told to the assistant and the UI."""

    confirmation_id: str
    status: Literal["confirmed", "outcome_unknown", "not_placed", "cancelled"]
    store: str
    payable_paise: int
    provider_order_id: str | None = None
    reason: str | None = None

    def assistant_note(self) -> str:
        """Application-authored text for the assistant; never user text."""

        amount = format_rupees(self.payable_paise)
        if self.status == "confirmed":
            return (
                f"{self.store} confirmed the order (order id {self.provider_order_id}), "
                f"{amount}, cash on delivery. Tell the user in one short line that the "
                "order is placed."
            )
        if self.status == "outcome_unknown":
            return (
                f"{self.store} did not confirm whether the order went through. Do not say it "
                "was placed and do not prepare it again; ask the user to check order "
                "tracking first."
            )
        if self.status == "cancelled":
            return "The user cancelled the order confirmation. Nothing was ordered."
        return f"The order was not placed: {self.reason} Nothing was ordered."


Notifier = Callable[[OrderResult], Awaitable[None]]


@dataclass
class OrderConfirmation:
    id: str
    household_id: str
    provider: ProviderName
    store: str
    basket_id: str
    basket_revision: int
    address_id: str
    approval_id: str
    fingerprint: str
    quote_digest: str
    payable_paise: int
    created_at: datetime
    expires_at: datetime
    state: ConfirmationState = "open"
    closed_at: datetime | None = None
    notifier: Notifier | None = field(default=None, repr=False)


def quote_digest(quote: Quote | None) -> str:
    """A stable digest of the priced bill the user saw."""

    if quote is None:
        return ""
    payload = {
        "basket_id": quote.basket_id,
        "basket_revision": quote.basket_revision,
        "item_total_paise": quote.item_total_paise,
        "item_discount_paise": quote.item_discount_paise,
        "charges": [[charge.label, charge.amount_paise] for charge in quote.charges],
        "payable_paise": quote.payable_paise,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OrderConfirmations:
    def __init__(
        self,
        baskets: BasketRepository,
        approvals: ApprovalService,
        clock: Clock = utc_now,
        ttl: timedelta = CONFIRMATION_TTL,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.clock = clock
        self.ttl = ttl
        self._items: dict[str, OrderConfirmation] = {}
        self._lock = asyncio.Lock()

    async def prepare(
        self,
        household_id: str,
        basket: Basket,
        approval: Approval,
        *,
        store: str,
        notifier: Notifier | None = None,
    ) -> OrderConfirmation:
        """Bind a confirmation to this exact bill. No approval or order happens here."""

        if not household_id or basket.household_id != household_id:
            raise NotFound("Basket not found.")
        if basket.provider not in ORDERABLE_PROVIDERS:
            raise ApprovalRefused("Only Zepto cash-on-delivery orders can be placed here.")
        if (
            approval.basket_id != basket.id
            or approval.basket_revision != basket.revision
            or approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
            or approval.quote is None
            or basket.address_id is None
        ):
            raise ApprovalRefused("There is no current bill for this basket. Get the final bill first.")
        now = self.clock()
        expires_at = min(_utc(now) + self.ttl, _utc(approval.expires_at))
        if expires_at <= _utc(now):
            raise ApprovalRefused("This bill expired. Get the final bill again.")
        confirmation = OrderConfirmation(
            id=secrets.token_urlsafe(18),
            household_id=household_id,
            provider=basket.provider,
            store=store,
            basket_id=basket.id,
            basket_revision=basket.revision,
            address_id=basket.address_id,
            approval_id=approval.id,
            fingerprint=approval.fingerprint,
            quote_digest=quote_digest(approval.quote),
            payable_paise=approval.payable_paise,
            created_at=now,
            expires_at=expires_at,
            notifier=notifier,
        )
        async with self._lock:
            self._prune(now)
            # Only the newest card for a store can place its order.
            for other in self._items.values():
                if (
                    other.state == "open"
                    and other.household_id == household_id
                    and other.provider == basket.provider
                ):
                    other.state = "superseded"
                    other.closed_at = now
            self._items[confirmation.id] = confirmation
        return confirmation

    async def cancel(self, household_id: str, confirmation_id: str) -> OrderConfirmation:
        async with self._lock:
            confirmation = self._owned(household_id, confirmation_id)
            if confirmation.state != "open":
                raise ApprovalRefused(self._closed_reason(confirmation))
            confirmation.state = "cancelled"
            confirmation.closed_at = self.clock()
        await self._notify(
            confirmation,
            OrderResult(
                confirmation.id, "cancelled", confirmation.store, confirmation.payable_paise
            ),
        )
        return confirmation

    async def place(
        self, household_id: str, confirmation_id: str, checkout: CheckoutService
    ) -> OrderResult:
        """The user's tap: verify every binding, then approve and place the COD order once."""

        async with self._lock:
            confirmation = self._owned(household_id, confirmation_id)
            if confirmation.state != "open":
                raise ApprovalRefused(self._closed_reason(confirmation))
            now = self.clock()
            if _utc(now) >= _utc(confirmation.expires_at):
                confirmation.state = "expired"
                confirmation.closed_at = now
                raise ApprovalRefused(self._closed_reason(confirmation))
            # Single use: consumed before any check or provider call, so a second
            # tap (or a retry) can never reach checkout through this confirmation.
            confirmation.state = "used"
            confirmation.closed_at = now
        try:
            result = await self._place(confirmation, checkout)
        except ShoppingError as exc:
            await self._notify(
                confirmation,
                OrderResult(
                    confirmation.id,
                    "not_placed",
                    confirmation.store,
                    confirmation.payable_paise,
                    reason=str(exc),
                ),
            )
            raise
        await self._notify(confirmation, result)
        return result

    async def _place(self, confirmation: OrderConfirmation, checkout: CheckoutService) -> OrderResult:
        if confirmation.provider not in ORDERABLE_PROVIDERS:
            raise ApprovalRefused("Only Zepto cash-on-delivery orders can be placed here.")
        basket = await self.baskets.get(confirmation.basket_id)
        if basket is None or basket.household_id != confirmation.household_id:
            raise NotFound("Basket not found.")
        if (
            basket.revision != confirmation.basket_revision
            or basket.address_id != confirmation.address_id
            or basket.status not in {BasketStatus.AWAITING_APPROVAL, BasketStatus.APPROVED}
        ):
            raise ApprovalRefused(
                "The basket changed after this order card was shown. Get the final bill again."
            )
        approval = await self.approvals.approvals.get(confirmation.approval_id)
        if (
            approval is None
            or approval.basket_id != basket.id
            or approval.basket_revision != confirmation.basket_revision
            or approval.fingerprint != confirmation.fingerprint
            or approval.payable_paise != confirmation.payable_paise
            or quote_digest(approval.quote) != confirmation.quote_digest
        ):
            raise ApprovalRefused(
                "The bill changed after this order card was shown. Get the final bill again."
            )
        if approval.status == ApprovalStatus.PENDING:
            await self.approvals.approve(
                approval.id, confirmation.fingerprint, confirmation.household_id
            )
        elif approval.status != ApprovalStatus.APPROVED:
            raise ApprovalRefused(
                f"This bill is {approval.status.value}. Get the final bill again."
            )
        attempt = await checkout.checkout_cod(
            approval.id,
            confirmation.household_id,
            # Derived from the single-use confirmation: a retried tap can never
            # become a second order, and the claim stays one-shot per approval.
            f"order-confirmation:{confirmation.id}",
            expected_fingerprint=confirmation.fingerprint,
        )
        if attempt.status == CheckoutStatus.CONFIRMED and attempt.provider_order_id:
            return OrderResult(
                confirmation.id,
                "confirmed",
                confirmation.store,
                attempt.payable_paise,
                provider_order_id=attempt.provider_order_id,
            )
        return OrderResult(
            confirmation.id, "outcome_unknown", confirmation.store, attempt.payable_paise
        )

    def _owned(self, household_id: str, confirmation_id: str) -> OrderConfirmation:
        confirmation = self._items.get(confirmation_id)
        if confirmation is None or not household_id or confirmation.household_id != household_id:
            # Do not reveal another household's confirmation.
            raise NotFound("Order confirmation not found. Ask for the order again.")
        return confirmation

    @staticmethod
    def _closed_reason(confirmation: OrderConfirmation) -> str:
        return {
            "cancelled": "This order card was cancelled. Ask for the order again.",
            "used": "This order card was already used. It cannot place another order.",
            "superseded": "A newer order card replaced this one. Use the latest card.",
            "expired": "This order card expired. Ask for the order again.",
        }.get(confirmation.state, "This order card is no longer valid.")

    async def _notify(self, confirmation: OrderConfirmation, result: OrderResult) -> None:
        if confirmation.notifier is None:
            return
        try:
            await confirmation.notifier(result)
        except Exception as exc:  # noqa: BLE001 - a closed voice session must not fail the tap
            logger.warning("event=order_result_notify_failed error=%s", type(exc).__name__)

    def _prune(self, now: datetime) -> None:
        cutoff = _utc(now) - RETENTION
        for key in [
            key
            for key, item in self._items.items()
            if _utc(item.expires_at) < cutoff or (item.closed_at and _utc(item.closed_at) < cutoff)
        ]:
            self._items.pop(key, None)
        while len(self._items) >= MAX_CONFIRMATIONS:
            self._items.pop(next(iter(self._items)))
