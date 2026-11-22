"""One-tap order confirmations: the only way voice or typed chat leads to an order.

The assistant may *prepare* an order (``OrderConfirmations.prepare``). That
binds a short-lived, single-use confirmation to the exact basket revision, the
open approval, its quote and payable amount, and the delivery address, and the
browser shows it as a card with a "Place order" button. Nothing is approved or
ordered until the signed-in user taps that button, which calls
``OrderConfirmations.place``. ``place`` re-checks every binding, then runs the
existing approval and Zepto checkout path (same locks, durable one-shot claim
and outcome-unknown handling). Cancel invalidates the confirmation.

The payment method is part of the binding. Cash on delivery is always offered;
prepaid methods only when ``PaymentOptions`` lists them. Choosing another
method replaces the card with a fresh one. A prepaid tap ends in
``awaiting_payment``: the user pays in a provider-authorised flow, and
``check_payment`` reads the result once per tap. Only a provider-confirmed
payment *and* order is reported as placed.

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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

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
    CheckoutAttempt,
    CheckoutStatus,
    PaymentMethod,
    ProviderName,
    Quote,
)
from app.shopping.payments import PAYMENT_LABELS, PREPAID_METHODS, payment_label
from app.shopping.ports import BasketRepository
from app.shopping.reconciliation import RECONCILED_STATUS, CheckoutReconciler, ReconcileOutcome


logger = logging.getLogger(__name__)

CONFIRMATION_TTL = timedelta(minutes=5)
# Confirmations are kept this long after they close, so a late tap gets a clear
# "already used/cancelled" answer instead of "not found".
RETENTION = timedelta(hours=1)
MAX_CONFIRMATIONS = 2000
ORDERABLE_PROVIDERS: frozenset[ProviderName] = frozenset({"zepto"})

ConfirmationState = Literal["open", "cancelled", "used", "superseded", "expired"]


OrderStatus = Literal[
    "confirmed",
    "outcome_unknown",
    "not_placed",
    "cancelled",
    "awaiting_payment",
    "payment_succeeded",
    "payment_failed",
    "paid_order_failed",
    "payment_unknown",
]

_STATUS_FROM_CHECKOUT: dict[CheckoutStatus, OrderStatus] = {
    CheckoutStatus.CONFIRMED: "confirmed",
    CheckoutStatus.OUTCOME_UNKNOWN: "outcome_unknown",
    CheckoutStatus.DISPATCHING: "outcome_unknown",
    CheckoutStatus.AWAITING_PAYMENT: "awaiting_payment",
    CheckoutStatus.PAYMENT_SUCCEEDED: "payment_succeeded",
    CheckoutStatus.PAYMENT_FAILED: "payment_failed",
    CheckoutStatus.PAID_ORDER_FAILED: "paid_order_failed",
    CheckoutStatus.PAYMENT_UNKNOWN: "payment_unknown",
}


class PaymentOptions(Protocol):
    async def available_methods(self, household_id: str) -> tuple[PaymentMethod, ...]: ...


@dataclass(frozen=True)
class OrderResult:
    """What happened to one confirmation, as told to the assistant and the UI."""

    confirmation_id: str
    status: OrderStatus
    store: str
    payable_paise: int
    provider_order_id: str | None = None
    reason: str | None = None
    payment_method: PaymentMethod = "cod"
    checkout_id: str | None = None
    # Provider-issued link to finish payment; only while awaiting payment.
    payment_url: str | None = None
    # The store's order code ("#UIIMJHTCC04881"), when known.
    order_code: str | None = None
    # "order_history" when the outcome came from a read-only reconciliation.
    source: Literal["checkout", "order_history"] = "checkout"
    # The last reconciliation check for an unknown outcome, if any.
    check: Literal["matched", "not_found", "ambiguous", "unavailable", "not_unknown"] | None = None
    candidates: tuple[str, ...] = ()

    @classmethod
    def from_attempt(
        cls, confirmation_id: str, store: str, attempt: CheckoutAttempt
    ) -> "OrderResult":
        status = _STATUS_FROM_CHECKOUT.get(attempt.status, "outcome_unknown")
        order_id = attempt.provider_order_id
        if status == "confirmed" and not order_id:
            status = "outcome_unknown"
        return cls(
            confirmation_id,
            status,
            store,
            attempt.payable_paise,
            provider_order_id=order_id,
            payment_method=attempt.payment_method,
            checkout_id=attempt.id,
            payment_url=attempt.payment_url if status == "awaiting_payment" else None,
        )

    def assistant_note(self) -> str:
        """Application-authored text for the assistant; never user text."""

        amount = format_rupees(self.payable_paise)
        method = payment_label(self.payment_method)
        never = (
            "Never ask for a UPI PIN, card number, CVV or OTP, and never say it is paid "
            "until a later result says so."
        )
        if self.status == "confirmed" and self.source == "order_history":
            paid = "cash on delivery" if self.payment_method == "cod" else f"paid by {method}"
            reference = self.order_code or self.provider_order_id
            return (
                f"{self.store}'s order history shows this order was placed (order code "
                f"{reference}), {amount}, {paid}. The earlier result was unconfirmed; now tell "
                f"the user in one short line that the order is placed, with code #{reference}."
            )
        if self.status == "confirmed":
            paid = "cash on delivery" if self.payment_method == "cod" else f"paid by {method}"
            return (
                f"{self.store} confirmed the order (order id {self.provider_order_id}), "
                f"{amount}, {paid}. Tell the user in one short line that the "
                "order is placed."
            )
        if self.status == "outcome_unknown" and self.check == "not_found":
            return (
                f"{self.store}'s order history shows no matching order yet, so this order is "
                "still not confirmed. Do not say it was placed or paid and do not prepare it "
                "again. Say exactly that it is not confirmed and ask the user to check the "
                "Zepto app."
            )
        if self.status == "outcome_unknown" and self.check == "ambiguous":
            codes = ", ".join(f"#{code}" for code in self.candidates[:3])
            return (
                f"Several {self.store} orders could match this one ({codes}); GharSaathi will "
                "not guess which. Do not say it was placed. Ask the user to check the Zepto "
                "app; do not prepare it again."
            )
        if self.status == "outcome_unknown":
            return (
                f"{self.store} did not confirm whether the order went through. Do not say it "
                "was placed or paid and do not prepare it again. Say it is not confirmed yet "
                "and offer 'Check with Zepto' on the order card, or the Zepto app."
            )
        if self.status == "awaiting_payment":
            return (
                f"{self.store} is waiting for payment of {amount} by {method}. Nothing is paid "
                "or placed yet. In one short line ask the user to finish the payment in their "
                "UPI app or on Zepto's payment page opened from the card, then tap "
                f"\"I've paid, check status\". {never}"
            )
        if self.status == "payment_succeeded":
            return (
                f"{self.store} confirmed the payment of {amount} but has not confirmed the order "
                "yet. Say that plainly and ask the user to check the status again in a moment. "
                "Do not say the order is placed."
            )
        if self.status == "payment_failed":
            return (
                f"{self.store} says the payment of {amount} did not go through, so the order is "
                "not placed. Say that plainly; do not prepare it again. Ask the user to check "
                "their UPI app and the Zepto app before trying anything else."
            )
        if self.status == "paid_order_failed":
            return (
                f"{self.store} says the payment of {amount} went through but the order was not "
                "created. Say that plainly, ask the user not to pay again, and to contact Zepto "
                "support in the Zepto app about this payment. Do not promise a refund."
            )
        if self.status == "payment_unknown":
            return (
                f"{self.store} did not clearly confirm the payment. Do not say it was paid or "
                "placed and do not prepare it again; ask the user to tap \"I've paid, check "
                f"status\" on the card or check their UPI app. {never}"
            )
        if self.status == "cancelled":
            return "The user cancelled the order confirmation. Nothing was ordered."
        return f"The order was not placed: {self.reason} Nothing was ordered."


def result_from_reconciliation(
    confirmation_id: str, store: str, outcome: ReconcileOutcome
) -> OrderResult:
    """An order result from a read-only order-history check."""

    assert outcome.attempt is not None
    result = OrderResult.from_attempt(confirmation_id, store, outcome.attempt)
    return replace(
        result,
        order_code=outcome.order_code,
        source=(
            "order_history"
            if outcome.check == "matched"
            or outcome.attempt.provider_status == RECONCILED_STATUS
            else "checkout"
        ),
        check=outcome.check,
        candidates=outcome.candidates,
    )


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
    payment_method: PaymentMethod = "cod"
    # Methods offered when the card was made, in display order.
    payment_methods: tuple[PaymentMethod, ...] = ("cod",)
    checkout_id: str | None = None
    last_status: OrderStatus | None = None
    # The last reconciliation answer told to the session, so it is told once.
    last_note: str | None = None


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
        payments: PaymentOptions | None = None,
        reconciler: CheckoutReconciler | None = None,
        push: Any = None,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.clock = clock
        self.ttl = ttl
        self.payments = payments
        # Read-only order-history checks for unknown outcomes (app/shopping/reconciliation.py).
        self.reconciler = reconciler
        # Browser notifications: one reminder before an untapped card expires.
        self.push = push
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
        payment_method: PaymentMethod = "cod",
    ) -> OrderConfirmation:
        """Bind a confirmation to this exact bill and payment method.

        No approval or order happens here.
        """

        if not household_id or basket.household_id != household_id:
            raise NotFound("Basket not found.")
        if basket.provider not in ORDERABLE_PROVIDERS:
            raise ApprovalRefused("Only Zepto orders can be placed here.")
        if payment_method not in PAYMENT_LABELS:
            raise ApprovalRefused("Unknown payment method.")
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
        methods = await self.available_methods(household_id)
        if payment_method not in methods:
            raise ApprovalRefused(
                f"{payment_label(payment_method)} is not available for this order right now. "
                "Choose another payment method."
            )
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
            payment_method=payment_method,
            payment_methods=methods,
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
        if self.push is not None:
            self.push.card_prepared(confirmation)
        return confirmation

    async def available_methods(self, household_id: str) -> tuple[PaymentMethod, ...]:
        """Cash on delivery, plus prepaid methods the provider lists and the flag allows."""

        if self.payments is None:
            return ("cod",)
        try:
            listed = await self.payments.available_methods(household_id)
        except Exception as exc:  # noqa: BLE001 - payment discovery never blocks COD
            logger.warning("event=payment_methods_failed error=%s", type(exc).__name__)
            return ("cod",)
        extra = tuple(method for method in listed if method in PREPAID_METHODS)
        return ("cod", *dict.fromkeys(extra))

    async def change_payment_method(
        self, household_id: str, confirmation_id: str, payment_method: PaymentMethod
    ) -> OrderConfirmation:
        """Replace an open card with a fresh one bound to ``payment_method``."""

        async with self._lock:
            old = self._owned(household_id, confirmation_id)
            if old.state != "open":
                raise ApprovalRefused(self._closed_reason(old))
            if _utc(self.clock()) >= _utc(old.expires_at):
                old.state = "expired"
                old.closed_at = self.clock()
                raise ApprovalRefused(self._closed_reason(old))
        basket = await self.baskets.get(old.basket_id)
        approval = await self.approvals.approvals.get(old.approval_id)
        if (
            basket is None
            or approval is None
            or basket.revision != old.basket_revision
            or basket.address_id != old.address_id
            or approval.fingerprint != old.fingerprint
            or quote_digest(approval.quote) != old.quote_digest
        ):
            raise ApprovalRefused(
                "The bill changed after this order card was shown. Get the final bill again."
            )
        # prepare() re-checks the bill and supersedes the old card.
        return await self.prepare(
            household_id,
            basket,
            approval,
            store=old.store,
            notifier=old.notifier,
            payment_method=payment_method,
        )

    async def check_payment(
        self, household_id: str, checkout_id: str, checkout: CheckoutService
    ) -> OrderResult:
        """The user's "I've paid, check status" tap: one status read, never an order call."""

        attempt = await checkout.check_payment(checkout_id, household_id)
        confirmation = next(
            (item for item in self._items.values() if item.checkout_id == checkout_id), None
        )
        store = confirmation.store if confirmation is not None else "Zepto"
        result = OrderResult.from_attempt(
            confirmation.id if confirmation is not None else "", store, attempt
        )
        if confirmation is not None and confirmation.last_status != result.status:
            confirmation.last_status = result.status
            await self._notify(confirmation, result)
        return result

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
        """The user's tap: verify every binding, then approve and place the order once."""

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
        if result.status == "outcome_unknown" and result.checkout_id and self.reconciler is not None:
            self.reconciler.follow_up(
                result.checkout_id,
                on_final=lambda outcome: self._after_check(confirmation, outcome),
            )
        return result

    async def check_order(self, household_id: str, checkout_id: str) -> OrderResult:
        """The user's "Check with Zepto" tap: one read-only order-history check.

        Never calls an order tool. Repeated taps within the reconciler's interval
        reuse the last answer.
        """

        if self.reconciler is None:
            raise NotFound("Order checks are not available right now. Check the Zepto app.")
        outcome = await self.reconciler.reconcile(checkout_id, household_id=household_id)
        if outcome.attempt is None:
            raise NotFound("Checkout not found.")
        confirmation = next(
            (item for item in self._items.values() if item.checkout_id == checkout_id), None
        )
        result = result_from_reconciliation(
            confirmation.id if confirmation else "",
            confirmation.store if confirmation else "Zepto",
            outcome,
        )
        if confirmation is not None:
            await self._after_check(confirmation, outcome)
        return result

    async def notify_reconciled(self, outcome: ReconcileOutcome, *, announce: bool = True) -> None:
        """Tell the session that prepared this order about a reconciled outcome.

        ``announce=False`` only records it as told (the caller's own tool result
        already carries it), so a later background check does not repeat it.
        """

        if outcome.attempt is None:
            return
        confirmation = next(
            (item for item in self._items.values() if item.checkout_id == outcome.attempt.id), None
        )
        if confirmation is not None:
            await self._after_check(confirmation, outcome, announce=announce)

    async def _after_check(
        self, confirmation: OrderConfirmation, outcome: ReconcileOutcome, *, announce: bool = True
    ) -> None:
        """Notify once per new answer; an unreadable history adds nothing new."""

        result = result_from_reconciliation(confirmation.id, confirmation.store, outcome)
        if result.status == "outcome_unknown" and result.check not in {"not_found", "ambiguous"}:
            return
        key = f"{result.status}:{result.check if result.status == 'outcome_unknown' else ''}"
        if confirmation.last_note == key or (
            result.status != "outcome_unknown" and confirmation.last_status == result.status
        ):
            return
        confirmation.last_note = key
        confirmation.last_status = result.status
        if announce:
            await self._notify(confirmation, result)

    async def _place(self, confirmation: OrderConfirmation, checkout: CheckoutService) -> OrderResult:
        if confirmation.provider not in ORDERABLE_PROVIDERS:
            raise ApprovalRefused("Only Zepto orders can be placed here.")
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
        attempt = await checkout.checkout(
            approval.id,
            confirmation.household_id,
            # Derived from the single-use confirmation: a retried tap can never
            # become a second order, and the claim stays one-shot per approval.
            f"order-confirmation:{confirmation.id}",
            expected_fingerprint=confirmation.fingerprint,
            payment_method=confirmation.payment_method,
        )
        confirmation.checkout_id = attempt.id
        result = OrderResult.from_attempt(confirmation.id, confirmation.store, attempt)
        confirmation.last_status = result.status
        return result

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
