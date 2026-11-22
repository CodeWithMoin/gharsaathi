"""Zepto COD quote and confirmation gateways.

The quote flow deliberately replaces the remote cart with the exact local
basket, then obtains an unconfirmed cash-on-delivery preview.  Submitting an
order is a distinct capability so approval services can bind the reviewed
quote before they call it.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.commerce.zepto.checkout import (
    PROVIDER,
    ZeptoConfirmedOrder,
    parse_confirmed_cod_order,
    ZeptoPreviewRefused,
    quote_from_preview,
)
from app.commerce.zepto.diagnostics import log_preview_failure
from app.commerce.zepto.errors import ZeptoSchemaError
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Basket, BasketLine, Quote


# Zepto's order tools. Each takes ``confirmOrder``; false returns a preview.
PREVIEW_TOOLS = frozenset(
    {
        "create_order",
        "create_online_payment_order",
        "create_wallet_order",
        "create_upi_reserve_pay_order",
    }
)


class ZeptoToolClient(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]: ...


class ZeptoQuoteProvider:
    """Prepare a strict COD quote; never confirms an order."""

    name = PROVIDER

    def __init__(
        self,
        client: ZeptoToolClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        device_id_factory: Callable[[Basket], str] | None = None,
        checkout_lock: asyncio.Lock | None = None,
    ) -> None:
        self.client = client
        self.clock = clock
        self.device_id_factory = device_id_factory or device_id_for
        self.checkout_lock = checkout_lock or asyncio.Lock()

    async def quote(self, basket: Basket) -> Quote:
        async with self.checkout_lock:
            return await self.quote_unlocked(basket)

    async def quote_unlocked(
        self,
        basket: Basket,
        *,
        preview_tool: str = "create_order",
        payment_method_value: str = "COD",
    ) -> Quote:
        """Prepare a quote while the shared checkout coordinator is held.

        ``preview_tool`` is one of Zepto's ``create_*`` order tools; it is only
        ever called with ``confirmOrder: false`` here.
        """

        if preview_tool not in PREVIEW_TOOLS:
            raise ApprovalRefused("This payment method cannot be previewed.")

        _require_zepto_basket(basket)
        correlation_id = uuid.uuid4().hex
        stage = "select_saved_address"
        preview: object | None = None
        try:
            await self.client.call_tool(
                "select_saved_address",
                {"addressId": basket.address_id},
                retry_on_auth=False,
            )
            stage = "update_cart"
            await self.client.call_tool(
                "update_cart",
                {
                    "deviceId": self.device_id_factory(basket),
                    "cartItems": cart_items_for(basket.lines),
                    "replaceCart": True,
                },
                retry_on_auth=False,
            )
            stage = preview_tool
            preview = await self.client.call_tool(
                preview_tool,
                {"confirmOrder": False, "userAddressId": basket.address_id},
                retry_on_auth=False,
            )
            stage = "parse_preview"
            return quote_from_preview(
                basket, preview, self.clock(), payment_method_value=payment_method_value
            )
        except ApprovalRefused as exc:
            if isinstance(exc, ZeptoPreviewRefused):
                log_preview_failure(
                    correlation_id=correlation_id,
                    stage=stage,
                    step=exc.step,
                    outcome="refused",
                    payload=preview,
                    facts=exc.facts,
                )
            raise
        except ZeptoSchemaError as exc:
            step = getattr(exc, "step", "response")
            log_preview_failure(
                correlation_id=correlation_id,
                stage=stage,
                step=step,
                outcome="invalid",
                payload=preview,
                facts=getattr(exc, "facts", None),
            )
            raise ProviderUnavailable(
                preview_failure_message(stage, step, correlation_id)
            ) from exc
        except Exception as exc:
            log_preview_failure(
                correlation_id=correlation_id,
                stage=stage,
                step="provider_error",
                outcome=type(exc).__name__,
                payload=None,
            )
            raise ProviderUnavailable(
                "Zepto could not prepare the cash-on-delivery bill. Please try again."
            ) from exc


_STEP_LABELS: tuple[tuple[str, str], ...] = (
    ("envelope.text", "Zepto sent the preview as text instead of structured data"),
    ("envelope", "the preview format was not recognised"),
    ("isPreview", "it did not come back as an unconfirmed preview"),
    ("requiresConfirmation", "it did not come back as an unconfirmed preview"),
    ("deliverable", "delivery availability was missing"),
    ("paymentMethod", "the payment method was missing"),
    ("deliveryAddress", "the delivery address details were incomplete"),
    ("items", "the item details were invalid"),
    ("totalItems", "the item count was invalid"),
    ("freeDelivery", "the free-delivery details were inconsistent"),
    ("deliveryFee", "the delivery fee details were inconsistent"),
    ("otherFees", "the extra fees were invalid"),
    ("payable.reconciliation", "the charges did not add up to Zepto's total"),
    ("subTotal.scale", "the amounts were not in the expected unit"),
)


def preview_failure_message(stage: str, step: str, correlation_id: str) -> str:
    """User-facing reason naming the failed check, never provider data."""

    reference = correlation_id[:8]
    if stage != "parse_preview":
        step_name = stage.replace("_", " ")
        return (
            f"Zepto could not prepare the bill (the {step_name} step failed). "
            f"Nothing was ordered. Please try again. Ref {reference}."
        )
    label = next(
        (text for prefix, text in _STEP_LABELS if step == prefix or step.startswith(prefix)),
        "the bill amounts were invalid",
    )
    return (
        f"Zepto's bill preview could not be verified because {label}. "
        f"Nothing was ordered. Ref {reference}."
    )


class ZeptoCodCheckoutGateway:
    """Confirm a reviewed Zepto COD quote.

    The caller must enforce its exact local approval before invoking this
    gateway.  It must not retry because a transport failure after dispatch can
    leave the order status unknown.
    """

    def __init__(
        self, client: ZeptoToolClient, checkout_lock: asyncio.Lock | None = None
    ) -> None:
        self.client = client
        self.checkout_lock = checkout_lock or asyncio.Lock()

    async def confirm_cod(self, basket: Basket) -> ZeptoConfirmedOrder:
        async with self.checkout_lock:
            return await self.confirm_cod_unlocked(basket)

    async def confirm_cod_unlocked(self, basket: Basket) -> ZeptoConfirmedOrder:
        """Confirm while the exact-preview coordinator remains held."""

        _require_zepto_basket(basket)
        try:
            payload = await self.client.call_tool(
                "create_order",
                {"confirmOrder": True, "userAddressId": basket.address_id},
                retry_on_auth=False,
            )
            return parse_confirmed_cod_order(payload)
        except ZeptoSchemaError as exc:
            raise ProviderUnavailable(
                "Zepto returned an ambiguous order confirmation. Check order history before retrying."
            ) from exc
        except Exception as exc:
            raise ProviderUnavailable(
                "Zepto could not confirm the order. Check order history before retrying."
            ) from exc


def device_id_for(basket: Basket) -> str:
    """A stable opaque cart key scoped to one household, never a raw user id."""

    material = f"gharsaathi:zepto:{basket.household_id}".encode("utf-8")
    return "gharsaathi-" + hashlib.sha256(material).hexdigest()[:32]


def cart_items_for(lines: list[BasketLine]) -> list[dict[str, Any]]:
    """Produce the minimal live-schema cart representation for exact local lines."""

    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line in lines:
        if line.ref.provider != PROVIDER:
            raise ApprovalRefused(f"{line.name} is not a Zepto item.")
        if not line.ref.sku_id:
            raise ApprovalRefused(f"{line.name} is missing its Zepto store product identifier.")
        key = (line.ref.variant_id, line.ref.sku_id)
        if key in seen:
            raise ApprovalRefused(f"{line.name} appears more than once in the basket.")
        seen.add(key)
        items.append(
            {
                "productVariantId": line.ref.variant_id,
                "storeProductId": line.ref.sku_id,
                "quantity": line.quantity,
            }
        )
    if not items:
        raise ApprovalRefused("Add an item before requesting a Zepto checkout quote.")
    return items


def _require_zepto_basket(basket: Basket) -> None:
    if basket.provider != PROVIDER:
        raise ApprovalRefused("This basket is not a Zepto basket.")
    if not basket.address_id:
        raise ApprovalRefused("Choose a delivery address first.")
    if not basket.lines:
        raise ApprovalRefused("Add an item before requesting a Zepto checkout quote.")
