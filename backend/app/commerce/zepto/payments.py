"""Zepto prepaid orders: which tool each payment method uses, and how to read it.

The Zepto MCP catalogue documents only the *input* schemas of
``get_payment_methods``, ``create_online_payment_order``,
``create_wallet_order``, ``create_upi_reserve_pay_order`` and
``check_payment_status``. Their responses are unknown, so this module does not
guess field names. Every response is read through a ``ZeptoPaymentContract``
that names the exact key paths and values, filled in only after the owner's
tier-1/tier-2 live smoke runs (docs/LIVE_SMOKE.md) recorded the real shapes and
a reviewer checked them. Until then ``VERIFIED_CONTRACT`` is ``None`` and only
cash on delivery is offered, whatever the feature flag says.

Anything a contract does not clearly match is ``payment_unknown``: no money
claim, no payment link, no retry. Failures are logged as content-free shapes
(``diagnostics.payload_shape``), never values.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.commerce.zepto.diagnostics import payload_shape
from app.commerce.zepto.quote_provider import ZeptoQuoteProvider, ZeptoToolClient
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Basket, PaymentMethod, Quote
from app.shopping.payments import (
    PAYMENT_METHODS,
    PREPAID_METHODS,
    PaymentStart,
    PaymentStatusReport,
    safe_payment_url,
)


logger = logging.getLogger(__name__)

PAYMENT_DIAGNOSTIC_EVENT = "zepto_payment_response_unrecognised"
# Fixed by the live catalogue (tests/fixtures/zepto_tools_catalogue_2026-11-21.json).
ORDER_TOOLS: dict[PaymentMethod, str] = {
    "cod": "create_order",
    "upi_reserve": "create_upi_reserve_pay_order",
    "wallet": "create_wallet_order",
    "online": "create_online_payment_order",
}
PAYMENT_METHODS_TOOL = "get_payment_methods"
PAYMENT_STATUS_TOOL = "check_payment_status"
METHODS_CACHE_SECONDS = 120.0
_MISSING = object()


class PaymentShapeUnknown(Exception):
    """A payment response did not match the verified contract; ``step`` names where."""

    def __init__(self, step: str) -> None:
        super().__init__(f"Zepto payment response not recognised at {step}.")
        self.step = step


def read_path(payload: object, path: str) -> object:
    """The value at a dotted key path (``data.order.id``), or ``_MISSING``."""

    node = payload
    for key in path.split("."):
        if not isinstance(node, Mapping) or key not in node:
            return _MISSING
        node = node[key]
    return node


@dataclass(frozen=True)
class MethodsContract:
    """Where ``get_payment_methods`` lists the methods and how each is named."""

    list_path: str
    id_key: str
    # Optional boolean key; when set, a listed method must have it true.
    enabled_key: str | None
    # Provider identifier(s) per method, e.g. {"online": frozenset({"<value>"})}.
    identifiers: Mapping[PaymentMethod, frozenset[str]]


@dataclass(frozen=True)
class MethodContract:
    """How one prepaid ``create_*`` tool answers ``confirmOrder: true``."""

    # ``paymentMethod`` in its confirmOrder=false preview, which must otherwise
    # have the verified COD preview shape (app/commerce/zepto/checkout.py).
    preview_payment_method: str
    order_id_path: str
    payable_path: str | None = None
    payment_url_path: str | None = None
    # When set: the order is already paid and placed if this path holds one of
    # ``placed_values`` (for example a wallet debit).
    placed_path: str | None = None
    placed_values: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StatusContract:
    """How ``check_payment_status`` (poll=false) reports payment and order."""

    payment_status_path: str
    succeeded: frozenset[str]
    failed: frozenset[str]
    pending: frozenset[str]
    order_status_path: str | None = None
    order_confirmed: frozenset[str] = frozenset()
    order_failed: frozenset[str] = frozenset()
    # Boolean path set when the response explicitly instructs poll=true next.
    poll_instruction_path: str | None = None


@dataclass(frozen=True)
class ZeptoPaymentContract:
    methods: MethodsContract
    per_method: Mapping[PaymentMethod, MethodContract]
    status: StatusContract


# Fill in from the reviewed live smoke shape reports; see docs/LIVE_SMOKE.md.
# Leaving it None keeps every prepaid method off, even with the flag on.
VERIFIED_CONTRACT: ZeptoPaymentContract | None = None


def _data(payload: object) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) == {"content_text"}:
        raise PaymentShapeUnknown("envelope")
    data = payload.get("data", payload)
    if not isinstance(data, Mapping):
        raise PaymentShapeUnknown("envelope")
    return data


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _exact_paise(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and 0 <= value < 2**53:
        return int(value)
    return None


def parse_payment_methods(
    payload: object, contract: MethodsContract
) -> frozenset[PaymentMethod]:
    """Prepaid methods Zepto lists as available. Unknown entries are ignored."""

    raw = read_path(_data(payload), contract.list_path)
    if not isinstance(raw, list):
        raise PaymentShapeUnknown("methods.list")
    found: set[PaymentMethod] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        identifier = _text(entry.get(contract.id_key))
        if identifier is None:
            continue
        if contract.enabled_key is not None and entry.get(contract.enabled_key) is not True:
            continue
        for method, names in contract.identifiers.items():
            if method in PREPAID_METHODS and identifier in names:
                found.add(method)
    return frozenset(found)


def parse_payment_start(
    payload: object, contract: MethodContract, expected_payable_paise: int
) -> PaymentStart:
    """Read a confirmed prepaid order request; refuse anything ambiguous."""

    data = _data(payload)
    order_id = _text(read_path(data, contract.order_id_path))
    if order_id is None or len(order_id) > 256:
        raise PaymentShapeUnknown("order_id")
    payable: int | None = None
    if contract.payable_path is not None:
        payable = _exact_paise(read_path(data, contract.payable_path))
        if payable is None:
            raise PaymentShapeUnknown("payable")
        if payable != expected_payable_paise:
            # Never send the user to pay an amount they did not approve.
            raise PaymentShapeUnknown("payable.mismatch")
    if contract.placed_path is not None:
        placed = _text(read_path(data, contract.placed_path))
        if placed is not None and placed in contract.placed_values:
            return PaymentStart(order_id, placed=True, payable_paise=payable)
    url: str | None = None
    if contract.payment_url_path is not None:
        url = safe_payment_url(read_path(data, contract.payment_url_path))
        if url is None:
            raise PaymentShapeUnknown("payment_url")
    return PaymentStart(order_id, placed=False, payment_url=url, payable_paise=payable)


def parse_payment_status(payload: object, contract: StatusContract) -> PaymentStatusReport:
    """Read one status response. Unrecognised values become ``unknown``."""

    data = _data(payload)
    payment_value = _text(read_path(data, contract.payment_status_path))
    if payment_value in contract.succeeded:
        payment = "succeeded"
    elif payment_value in contract.failed:
        payment = "failed"
    elif payment_value in contract.pending:
        payment = "pending"
    else:
        payment = "unknown"
    order = "unknown"
    if contract.order_status_path is not None:
        order_value = _text(read_path(data, contract.order_status_path))
        if order_value in contract.order_confirmed:
            order = "confirmed"
        elif order_value in contract.order_failed:
            order = "failed"
        elif order_value is not None:
            order = "pending"
    poll = (
        contract.poll_instruction_path is not None
        and read_path(data, contract.poll_instruction_path) is True
    )
    return PaymentStatusReport(payment=payment, order=order, poll_instructed=poll)  # type: ignore[arg-type]


def log_payment_diagnostic(
    *, correlation_id: str, tool: str, step: str, payload: object | None
) -> None:
    """One content-free record: key paths and JSON types only."""

    logger.warning(
        "event=%s correlation_id=%s tool=%s step=%s shape=%s",
        PAYMENT_DIAGNOSTIC_EVENT,
        correlation_id,
        tool,
        step,
        json.dumps(
            payload_shape(payload) if payload is not None else {},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def parse_allowed_methods(raw: str) -> frozenset[PaymentMethod]:
    """``ZEPTO_PAYMENT_METHODS_ALLOWED``: comma-separated method ids."""

    names = {part.strip() for part in raw.split(",") if part.strip()}
    return frozenset(method for method in PREPAID_METHODS if method in names)


@dataclass
class ZeptoPaymentGateway:
    """Zepto's prepaid order tools behind the feature flag and verified contract.

    ``available_methods`` is the only place that decides what the order card
    may offer; ``start_unlocked`` makes the single consequential call, never
    retried; ``check_status`` makes one ``check_payment_status`` call with
    ``poll: false`` unless the previous response explicitly asked for true.
    """

    client: ZeptoToolClient
    quotes: ZeptoQuoteProvider
    enabled: bool
    allowed: frozenset[PaymentMethod]
    contract: ZeptoPaymentContract | None = None
    checkout_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    monotonic: Callable[[], float] = time.monotonic
    _methods_cache: tuple[float, frozenset[PaymentMethod]] | None = field(
        default=None, init=False, repr=False
    )

    def offered(self) -> frozenset[PaymentMethod]:
        """Prepaid methods this deployment may offer at all (no provider call)."""

        if not self.enabled or self.contract is None:
            return frozenset()
        return frozenset(
            method
            for method in self.allowed
            if method in self.contract.per_method
            and method in self.contract.methods.identifiers
        )

    async def available_methods(self, household_id: str) -> tuple[PaymentMethod, ...]:
        """Cash on delivery plus the prepaid methods Zepto lists right now."""

        offered = self.offered()
        if not offered or self.contract is None:
            return ("cod",)
        now = self.monotonic()
        cached = self._methods_cache
        if cached is not None and now - cached[0] < METHODS_CACHE_SECONDS:
            listed = cached[1]
        else:
            correlation_id = uuid.uuid4().hex
            payload: object | None = None
            try:
                payload = await self.client.call_tool(PAYMENT_METHODS_TOOL, {})
                listed = parse_payment_methods(payload, self.contract.methods)
            except PaymentShapeUnknown as exc:
                log_payment_diagnostic(
                    correlation_id=correlation_id,
                    tool=PAYMENT_METHODS_TOOL,
                    step=exc.step,
                    payload=payload,
                )
                listed = frozenset()
            except Exception as exc:  # noqa: BLE001 - a read failure only hides prepaid methods
                logger.warning(
                    "event=zepto_payment_methods_unavailable error=%s", type(exc).__name__
                )
                return ("cod",)
            self._methods_cache = (now, listed)
        return tuple(m for m in PAYMENT_METHODS if m == "cod" or (m in offered and m in listed))

    def _method(self, method: PaymentMethod) -> MethodContract:
        if method not in self.offered() or self.contract is None:
            raise ApprovalRefused("This payment method is not available. Nothing was ordered.")
        return self.contract.per_method[method]

    async def quote(self, basket: Basket, method: PaymentMethod) -> Quote:
        async with self.checkout_lock:
            return await self.quote_unlocked(basket, method)

    async def quote_unlocked(self, basket: Basket, method: PaymentMethod) -> Quote:
        contract = self._method(method)
        return await self.quotes.quote_unlocked(
            basket,
            preview_tool=ORDER_TOOLS[method],
            payment_method_value=contract.preview_payment_method,
        )

    async def start(self, basket: Basket, method: PaymentMethod, payable_paise: int) -> PaymentStart:
        async with self.checkout_lock:
            return await self.start_unlocked(basket, method, payable_paise)

    async def start_unlocked(
        self, basket: Basket, method: PaymentMethod, payable_paise: int
    ) -> PaymentStart:
        """Ask Zepto to create the prepaid order (confirmOrder: true), once."""

        contract = self._method(method)
        if basket.provider != "zepto" or not basket.address_id:
            raise ApprovalRefused("This basket cannot be paid for here.")
        tool = ORDER_TOOLS[method]
        payload = await self.client.call_tool(
            tool,
            {"confirmOrder": True, "userAddressId": basket.address_id},
            retry_on_auth=False,
        )
        try:
            return parse_payment_start(payload, contract, payable_paise)
        except PaymentShapeUnknown as exc:
            log_payment_diagnostic(
                correlation_id=uuid.uuid4().hex, tool=tool, step=exc.step, payload=payload
            )
            raise

    async def check_status(self, provider_order_id: str, *, poll: bool) -> PaymentStatusReport:
        if self.contract is None or not self.enabled:
            raise ProviderUnavailable("Online payment is turned off. Check the Zepto app.")
        arguments = {"orderId": provider_order_id, "poll": bool(poll)}
        try:
            payload = await self.client.call_tool(
                PAYMENT_STATUS_TOOL, arguments, retry_on_auth=False
            )
        except Exception as exc:
            raise ProviderUnavailable(
                "Zepto could not check the payment right now. Nothing changed; try again in a moment."
            ) from exc
        try:
            return parse_payment_status(payload, self.contract.status)
        except PaymentShapeUnknown as exc:
            log_payment_diagnostic(
                correlation_id=uuid.uuid4().hex,
                tool=PAYMENT_STATUS_TOOL,
                step=exc.step,
                payload=payload,
            )
            return PaymentStatusReport(payment="unknown", order="unknown")
