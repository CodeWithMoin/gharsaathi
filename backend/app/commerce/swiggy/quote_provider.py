"""User-triggered Swiggy cart synchronisation and final-bill quoting.

This module deliberately exposes no payment or checkout operation.  It is used
only after a user asks to review the final bill: the selected local basket is
sent to Swiggy with ``update_cart`` and the bill is immediately re-read from
``get_cart``.  The MCP client validates both calls against the authenticated
live tool catalogue before they leave this process.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Protocol

from app.commerce.swiggy.adapter import _data, _optional_str, _required_str
from app.commerce.swiggy.errors import SwiggySchemaError
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Basket, BasketLine, Charge, ProductRef, Quote, rupees_to_paise


PROVIDER = "swiggy_instamart"
_ITEM_TOTAL_LABEL = re.compile(r"item\s*total", re.IGNORECASE)
_DISCOUNT_LABEL = re.compile(r"discount|saving|coupon|offer", re.IGNORECASE)
_MONEY_TEXT = re.compile(r"(-)?\s*(?:₹|rs\.?|inr)?\s*(-)?\s*(\d+(?:\.\d{1,2})?)", re.I)


class SwiggyToolClient(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]: ...


class SwiggyQuoteProvider:
    """Quote capability only; it cannot place an order."""

    name = PROVIDER

    def __init__(
        self,
        client: SwiggyToolClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.clock = clock

    async def quote(self, basket: Basket) -> Quote:
        if basket.provider != PROVIDER:
            raise ApprovalRefused("This basket is not a Swiggy Instamart basket.")
        if not basket.address_id:
            raise ApprovalRefused("Choose a delivery address first.")
        items = cart_items_for(basket.lines)
        try:
            # The user initiated this operation by requesting a final bill.
            # update_cart replaces the provider cart with these exact lines.
            await self.client.call_tool(
                "update_cart",
                {"selectedAddressId": basket.address_id, "items": items},
                # update_cart changes the user's external cart. A session-auth
                # failure after dispatch is ambiguous, so it must never be replayed.
                retry_on_auth=False,
            )
            payload = await self.client.call_tool("get_cart", {})
        except (ApprovalRefused, ProviderUnavailable):
            raise
        except Exception as exc:
            # Never leak upstream payloads/tokens.  The detailed exception is chained
            # for server diagnostics only.
            raise ProviderUnavailable(
                "Swiggy could not prepare the final bill. Please try again."
            ) from exc
        return quote_from_cart(basket, payload, self.clock())


def cart_items_for(lines: list[BasketLine]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        ref = line.ref
        if ref.provider != PROVIDER:
            raise ApprovalRefused(f"{line.name} is not a Swiggy Instamart item.")
        if not ref.sku_id:
            raise ApprovalRefused(f"{line.name} is missing its Swiggy product identifier.")
        if ref.variant_id in seen:
            raise ApprovalRefused(f"{line.name} appears more than once in the basket.")
        seen.add(ref.variant_id)
        items.append({"spinId": ref.variant_id, "skuId": ref.sku_id, "quantity": line.quantity})
    return items


def quote_from_cart(basket: Basket, payload: dict[str, Any], quoted_at: datetime) -> Quote:
    """Parse and reconcile the documented ``get_cart`` response."""

    try:
        data = _data(payload)
        if data.get("cartAbsent") is True:
            reason = _optional_str(data.get("cartAbsentReason"))
            suffix = f": {reason}" if reason else ""
            raise ApprovalRefused(f"The Swiggy cart is empty{suffix}")
        if not isinstance(data.get("cartTotalAmount"), str):
            raise SwiggySchemaError("Swiggy cart is missing cartTotalAmount.")
        address = data.get("selectedAddressDetails")
        address_id = _optional_str(address.get("id")) if isinstance(address, dict) else None
        if address_id and address_id != basket.address_id:
            raise ApprovalRefused("The Swiggy cart is set to a different delivery address.")

        items = _cart_items(data.get("items"))
        unserviceable = {item["spin_id"] for item in _cart_items(data.get("unserviceableItems", []))}
        wanted = {line.ref.variant_id for line in basket.lines}
        extras = [item["name"] for item in items if item["spin_id"] not in wanted]
        if extras:
            raise ApprovalRefused("The Swiggy cart has items not in the basket: " + ", ".join(extras))

        quantities: dict[str, int] = {}
        availability: dict[str, bool] = {}
        for item in items:
            spin_id = item["spin_id"]
            quantities[spin_id] = quantities.get(spin_id, 0) + item["quantity"]
            availability[spin_id] = availability.get(spin_id, True) and item["available"]
        unavailable: list[ProductRef] = []
        for line in basket.lines:
            actual_quantity = quantities.get(line.ref.variant_id, 0)
            if actual_quantity > line.quantity:
                raise ApprovalRefused(f"Swiggy has more {line.name} than your basket.")
            if (
                actual_quantity < line.quantity
                or not availability.get(line.ref.variant_id, False)
                or line.ref.variant_id in unserviceable
            ):
                unavailable.append(line.ref)

        bill = data.get("billBreakdown")
        if not isinstance(bill, dict):
            raise SwiggySchemaError("Swiggy cart is missing billBreakdown.")
        raw_lines = bill.get("lineItems")
        to_pay = bill.get("toPay")
        if not isinstance(raw_lines, list) or not isinstance(to_pay, dict):
            raise SwiggySchemaError("Swiggy returned an invalid bill breakdown.")
        bill_lines = [
            (_required_str(raw, "label"), money_text_to_paise(raw.get("value")))
            for raw in raw_lines
            if isinstance(raw, dict)
        ]
        if len(bill_lines) != len(raw_lines):
            raise SwiggySchemaError("Swiggy returned an invalid bill line.")
        payable = money_text_to_paise(to_pay.get("value"))
        item_total, charges = reconcile_bill(bill_lines, items, payable)
    except ApprovalRefused:
        raise
    except SwiggySchemaError as exc:
        raise ProviderUnavailable("Swiggy returned an invalid final bill. Please try again.") from exc

    return Quote(
        provider=PROVIDER,
        basket_id=basket.id,
        basket_revision=basket.revision,
        item_total_paise=item_total,
        charges=charges,
        payable_paise=payable,
        unavailable_refs=unavailable,
        eta_text=None,
        quoted_at=quoted_at,
    )


def _cart_items(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise SwiggySchemaError("Swiggy returned an invalid cart items list.")
    parsed: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise SwiggySchemaError("Swiggy returned an invalid cart item.")
        quantity = item.get("quantity")
        available = item.get("isInStockAndAvailable")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise SwiggySchemaError("A Swiggy cart item is missing quantity.")
        if not isinstance(available, bool):
            raise SwiggySchemaError("A Swiggy cart item is missing availability.")
        parsed.append(
            {
                "spin_id": _required_str(item, "spinId"),
                "name": _required_str(item, "itemName"),
                "quantity": quantity,
                "available": available,
                "unit_price_paise": _rupees(item.get("discountedFinalPrice")),
            }
        )
    return parsed


def reconcile_bill(
    bill_lines: list[tuple[str, int]], cart_items: list[dict[str, Any]], payable: int
) -> tuple[int, list[Charge]]:
    item_lines = [line for line in bill_lines if _ITEM_TOTAL_LABEL.search(line[0])]
    if len(item_lines) > 1:
        raise SwiggySchemaError("Swiggy bill has more than one item total.")
    item_total = (
        item_lines[0][1]
        if item_lines
        else sum(item["unit_price_paise"] * item["quantity"] for item in cart_items)
    )
    other_lines = [line for line in bill_lines if line not in item_lines]
    as_given = [Charge(label=label, amount_paise=amount) for label, amount in other_lines]
    normalised_discounts = [
        Charge(
            label=label,
            amount_paise=-abs(amount) if _DISCOUNT_LABEL.search(label) else amount,
        )
        for label, amount in other_lines
    ]
    for charges in (as_given, normalised_discounts):
        if item_total + sum(charge.amount_paise for charge in charges) == payable:
            return item_total, charges
    raise SwiggySchemaError("Swiggy bill lines do not add up to the payable amount.")


def money_text_to_paise(value: object) -> int:
    if isinstance(value, bool):
        raise SwiggySchemaError("Swiggy returned an unreadable amount.")
    if isinstance(value, (int, float, Decimal)):
        return rupees_to_paise(value)
    if not isinstance(value, str):
        raise SwiggySchemaError("Swiggy returned a bill value that is not text.")
    text = value.strip().replace("−", "-").replace(",", "")
    if text.lower() == "free":
        return 0
    match = _MONEY_TEXT.fullmatch(text)
    if match is None or (match.group(1) and match.group(2)):
        raise SwiggySchemaError("Swiggy returned an unreadable amount.")
    try:
        paise = rupees_to_paise(Decimal(match.group(3)))
    except (InvalidOperation, ValueError) as exc:
        raise SwiggySchemaError("Swiggy returned an unreadable amount.") from exc
    return -paise if match.group(1) or match.group(2) else paise


def _rupees(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise SwiggySchemaError("Swiggy cart is missing numeric item pricing.")
    return rupees_to_paise(value)
