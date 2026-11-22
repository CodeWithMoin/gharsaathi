"""Payment methods for a tap-confirmed order, and the payment state machine.

Every method goes through the same order card, tap, approval and one-shot
checkout claim. Cash on delivery places the order at once. The other methods
create a provider order that waits for payment; the user pays only in a
provider-authorised flow (their UPI app or the store's own payment page, opened
from a provider-issued link). GharSaathi never asks for or handles a UPI PIN,
card number, CVV or OTP.

An order counts as placed only when the provider confirms both the payment and
the order. Anything the provider did not clearly confirm stays an explicit,
non-placed state; the consequential call is never retried.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

from app.shopping.models import Basket, CheckoutStatus, PaymentMethod, Quote


# Display order on the order card.
PAYMENT_METHODS: tuple[PaymentMethod, ...] = ("cod", "upi_reserve", "wallet", "online")
PREPAID_METHODS: frozenset[PaymentMethod] = frozenset({"upi_reserve", "wallet", "online"})
PAYMENT_LABELS: dict[PaymentMethod, str] = {
    "cod": "Cash on delivery",
    "upi_reserve": "UPI",
    "wallet": "Zepto Cash",
    "online": "Pay online",
}

# Payment states that can still change after a single status check.
CHECKABLE_STATUSES = frozenset(
    {
        CheckoutStatus.AWAITING_PAYMENT,
        CheckoutStatus.PAYMENT_SUCCEEDED,
        CheckoutStatus.PAYMENT_UNKNOWN,
    }
)
TERMINAL_STATUSES = frozenset(
    {
        CheckoutStatus.CONFIRMED,
        CheckoutStatus.OUTCOME_UNKNOWN,
        CheckoutStatus.PAYMENT_FAILED,
        CheckoutStatus.PAID_ORDER_FAILED,
    }
)

MAX_PAYMENT_URL_LENGTH = 2048
_ALLOWED_URL_SCHEMES = frozenset({"https", "upi"})

PaymentReading = Literal["succeeded", "failed", "pending", "unknown"]
OrderReading = Literal["confirmed", "failed", "pending", "unknown"]


def is_payment_method(value: object) -> bool:
    return isinstance(value, str) and value in PAYMENT_LABELS


def payment_label(method: PaymentMethod) -> str:
    return PAYMENT_LABELS[method]


@dataclass(frozen=True)
class PaymentStart:
    """What the provider returned for a confirmed prepaid order request."""

    provider_order_id: str
    # The order is already confirmed and paid (for example from a wallet).
    placed: bool
    # Provider-issued https/upi link the user opens to pay; None when the
    # request goes straight to the user's UPI app.
    payment_url: str | None = None
    payable_paise: int | None = None


@dataclass(frozen=True)
class PaymentStatusReport:
    """One parsed ``check_payment_status`` response."""

    payment: PaymentReading
    order: OrderReading
    # The previous response explicitly asked for ``poll: true`` next time.
    poll_instructed: bool = False


def next_checkout_status(
    current: CheckoutStatus, report: PaymentStatusReport
) -> CheckoutStatus:
    """Deterministic transition after one status check.

    Terminal states never change. Placed requires the provider to confirm both
    the payment and the order. Contradictions (a confirmed payment later read
    as pending or failed) become ``payment_unknown``, never a money claim.
    """

    if current not in CHECKABLE_STATUSES:
        return current
    payment, order = report.payment, report.order
    if payment == "succeeded":
        if order == "confirmed":
            return CheckoutStatus.CONFIRMED
        if order == "failed":
            return CheckoutStatus.PAID_ORDER_FAILED
        return CheckoutStatus.PAYMENT_SUCCEEDED
    if current == CheckoutStatus.PAYMENT_SUCCEEDED:
        # The provider already confirmed the payment; a weaker reading now is a
        # contradiction, not a failure.
        return CheckoutStatus.PAYMENT_UNKNOWN
    if payment == "failed":
        return CheckoutStatus.PAYMENT_FAILED
    if payment == "pending" and order != "failed":
        return CheckoutStatus.AWAITING_PAYMENT
    return CheckoutStatus.PAYMENT_UNKNOWN


def safe_payment_url(raw: object) -> str | None:
    """A provider payment link the browser may open, or None.

    Only ``https://host/...`` pages and ``upi://`` intents are accepted; no
    whitespace, control characters or credentials in the URL.
    """

    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > MAX_PAYMENT_URL_LENGTH:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    scheme = parts.scheme.casefold()
    if scheme not in _ALLOWED_URL_SCHEMES:
        return None
    if scheme == "https" and (not parts.hostname or parts.username or parts.password):
        return None
    if scheme == "upi" and not (parts.netloc or parts.path):
        return None
    return value


class PrepaidGateway(Protocol):
    """The provider capability for prepaid orders (app/commerce/zepto/payments.py)."""

    def offered(self) -> frozenset[PaymentMethod]:
        """Prepaid methods this deployment may offer at all; no provider call."""

    async def available_methods(self, household_id: str) -> tuple[PaymentMethod, ...]:
        """Cash on delivery plus the prepaid methods the provider lists now."""

    async def quote(self, basket: Basket, method: PaymentMethod) -> Quote: ...

    async def start(
        self, basket: Basket, method: PaymentMethod, payable_paise: int
    ) -> PaymentStart: ...

    async def check_status(
        self, provider_order_id: str, *, poll: bool
    ) -> PaymentStatusReport: ...
