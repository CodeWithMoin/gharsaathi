"""Strict Zepto COD preview and confirmation parsing.

The MCP response is the source of truth for its bill.  This module accepts
only the response shape observed in the live Zepto catalogue exercise and
rejects ambiguities rather than making an order from a guessed total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.commerce.zepto.errors import ZeptoSchemaError
from app.shopping.errors import ApprovalRefused
from app.shopping.models import Basket, Charge, Quote


PROVIDER = "zepto"


@dataclass(frozen=True)
class ZeptoConfirmedOrder:
    """The minimal, confirmed result of a successful COD submission."""

    order_id: str
    payable_paise: int
    eta_minutes: int | None


def quote_from_cod_preview(basket: Basket, payload: dict[str, Any], quoted_at: datetime) -> Quote:
    """Validate an unconfirmed COD order preview for exactly ``basket``.

    All currency fields are integer paise.  In particular, JSON floats are
    refused even when their value looks integral: accepting them would make
    money arithmetic depend on an undocumented provider representation.
    """

    if basket.provider != PROVIDER:
        raise ApprovalRefused("This basket is not a Zepto basket.")
    if not basket.address_id:
        raise ApprovalRefused("Choose a delivery address first.")

    data = _data(payload)
    if _required_bool(data, "deliverable") is not True:
        raise ApprovalRefused("The Zepto basket is not deliverable to this address.")
    if _required_bool(data, "isPreview") is not True:
        raise ZeptoSchemaError("Zepto did not return an order preview.")
    if _required_bool(data, "requiresConfirmation") is not True:
        raise ZeptoSchemaError("Zepto preview is missing its confirmation boundary.")
    if _required_str(data, "paymentMethod") != "COD":
        raise ApprovalRefused("Zepto did not prepare a cash-on-delivery checkout.")

    delivery_address = data.get("deliveryAddress")
    if not isinstance(delivery_address, dict):
        raise ZeptoSchemaError("Zepto preview is missing the delivery address.")
    _validate_delivery_address(delivery_address, basket.address_id)
    _validate_exact_items(basket, data.get("items"))

    subtotal = _money(data, "subTotal")
    delivery_fee = _money(data, "deliveryFee")
    delivery_fee_discount = _money(data, "deliveryFeeDiscount")
    delivery_fee_before_discount = _money(data, "deliveryFeePreDiscount")
    packaging_fee = _money(data, "packagingFee")
    discount = _money(data, "discount")
    taxes = _nullable_money(data, "taxes")
    payable = _money(data, "toPayAmount")
    other_fees = _other_fees(data.get("otherFees"))
    eta_minutes = _nullable_nonnegative_int(data, "etaInMinutes")
    total_items = _nonnegative_int(data.get("totalItems"), "totalItems")
    free_delivery = _free_delivery_unlocked(data.get("freeDelivery"))

    if delivery_fee_before_discount - delivery_fee_discount != delivery_fee:
        raise ZeptoSchemaError("Zepto delivery fees are inconsistent.")
    if free_delivery != (delivery_fee == 0):
        raise ZeptoSchemaError("Zepto free-delivery status is inconsistent.")
    if total_items != sum(line.quantity for line in basket.lines):
        raise ApprovalRefused("Zepto preview has a different item quantity than the basket.")

    charges = [
        Charge(label="Delivery fee", amount_paise=delivery_fee),
        Charge(label="Packaging fee", amount_paise=packaging_fee),
    ]
    if taxes:
        charges.append(Charge(label="Taxes", amount_paise=taxes))
    charges.extend(other_fees)
    if discount:
        charges.append(Charge(label="Discount", amount_paise=-discount))

    if subtotal + sum(charge.amount_paise for charge in charges) != payable:
        raise ZeptoSchemaError("Zepto preview charges do not add up to the payable amount.")

    return Quote(
        provider=PROVIDER,
        basket_id=basket.id,
        basket_revision=basket.revision,
        item_total_paise=subtotal,
        charges=charges,
        payable_paise=payable,
        eta_text=f"{eta_minutes} min" if eta_minutes is not None else None,
        quoted_at=quoted_at,
    )


def parse_confirmed_cod_order(payload: dict[str, Any]) -> ZeptoConfirmedOrder:
    """Parse a completed COD order and refuse a preview or vague response."""

    data = _data(payload)
    if _required_bool(data, "isPreview") is not False:
        raise ZeptoSchemaError("Zepto returned a preview instead of a confirmed order.")
    if _required_str(data, "paymentMethod") != "COD":
        raise ZeptoSchemaError("Zepto confirmed an unexpected payment method.")
    order_id = _required_str(data, "orderId")
    payable = _money(data, "toPayAmount")
    eta_minutes = _nullable_nonnegative_int(data, "etaInMinutes")
    return ZeptoConfirmedOrder(
        order_id=order_id,
        payable_paise=payable,
        eta_minutes=eta_minutes,
    )


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ZeptoSchemaError("Zepto returned an invalid checkout response.")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ZeptoSchemaError("Zepto returned an invalid checkout response.")
    return data


def _validate_delivery_address(address: dict[str, Any], expected_id: str) -> None:
    """Require an explicit returned address id when the provider includes one.

    The observed preview has an address object, but provider versions differ in
    whether its identifier is called ``id`` or ``addressId``.  Both are
    unambiguous, provided exactly one is a non-empty string and matches the
    selected local address.
    """

    identifiers = [
        value
        for key in ("id", "addressId")
        if isinstance((value := address.get(key)), str) and value.strip()
    ]
    if len(identifiers) != 1 or identifiers[0] != expected_id:
        raise ApprovalRefused("Zepto preview is for a different delivery address.")
    # Validate the observed shape without retaining either display field in the
    # quote or logs. They are provider-returned, potentially sensitive text.
    _required_str(address, "label")
    _required_str(address, "addressLine")


def _validate_exact_items(basket: Basket, raw_items: object) -> None:
    if not isinstance(raw_items, list):
        raise ZeptoSchemaError("Zepto preview is missing its items list.")
    if len(raw_items) != len(basket.lines):
        raise ApprovalRefused("Zepto preview does not match the basket items.")

    expected: dict[tuple[str, str], int] = {}
    for line in basket.lines:
        if line.ref.provider != PROVIDER:
            raise ApprovalRefused(f"{line.name} is not a Zepto item.")
        if not line.ref.sku_id:
            raise ApprovalRefused(f"{line.name} is missing its Zepto store product identifier.")
        key = (line.ref.variant_id, line.ref.sku_id)
        if key in expected:
            raise ApprovalRefused(f"{line.name} appears more than once in the basket.")
        expected[key] = line.quantity

    actual: dict[tuple[str, str], int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ZeptoSchemaError("Zepto preview contains an invalid item.")
        product_variant_id = _required_str(raw, "productVariantId")
        store_product_id = _required_str(raw, "storeProductId")
        quantity = _nonnegative_int(raw.get("quantity"), "item quantity")
        _money(raw, "price")
        _required_str(raw, "name")
        key = (product_variant_id, store_product_id)
        if key in actual:
            raise ZeptoSchemaError("Zepto preview contains duplicate item identifiers.")
        actual[key] = quantity

    if actual != expected:
        raise ApprovalRefused("Zepto preview does not exactly match the basket.")


def _other_fees(raw: object) -> list[Charge]:
    if not isinstance(raw, list):
        raise ZeptoSchemaError("Zepto preview has invalid other fees.")
    charges: list[Charge] = []
    labels: set[str] = set()
    for value in raw:
        if not isinstance(value, dict):
            raise ZeptoSchemaError("Zepto preview has an invalid other fee.")
        label = _required_str(value, "label")
        if label in labels:
            raise ZeptoSchemaError("Zepto preview has duplicate other fee labels.")
        labels.add(label)
        charges.append(Charge(label=label, amount_paise=_money(value, "amount")))
    return charges


def _free_delivery_unlocked(raw: object) -> bool:
    """Validate the observed Zepto free-delivery progress object."""

    if not isinstance(raw, dict):
        raise ZeptoSchemaError("Zepto preview has invalid freeDelivery.")
    unlocked = _required_bool(raw, "isUnlocked")
    _required_str(raw, "message")
    _money(raw, "minimumOrderValue")
    _money(raw, "remainingAmountToUnlock")
    return unlocked


def _required_bool(value: dict[str, Any], key: str) -> bool:
    raw = value.get(key)
    if not isinstance(raw, bool):
        raise ZeptoSchemaError(f"Zepto preview has invalid {key}.")
    return raw


def _required_str(value: dict[str, Any], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ZeptoSchemaError(f"Zepto preview has invalid {key}.")
    return raw


def _nonnegative_int(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ZeptoSchemaError(f"Zepto preview has invalid {label}.")
    return raw


def _money(value: dict[str, Any], key: str) -> int:
    return _nonnegative_int(value.get(key), key)


def _nullable_money(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    return 0 if raw is None else _nonnegative_int(raw, key)


def _nullable_nonnegative_int(value: dict[str, Any], key: str) -> int | None:
    raw = value.get(key)
    return None if raw is None else _nonnegative_int(raw, key)
