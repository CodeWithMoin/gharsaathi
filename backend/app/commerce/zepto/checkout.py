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
from app.shopping.models import Basket, Charge, OfferOpportunity, Quote


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
    free_delivery = _free_delivery_offer(
        data.get("freeDelivery"),
        subtotal,
        delivery_fee,
        delivery_fee_discount,
        delivery_fee_before_discount,
    )
    offers = [free_delivery, *_offers(data.get("offers"), subtotal)]

    if delivery_fee_before_discount - delivery_fee_discount != delivery_fee:
        raise ZeptoSchemaError("Zepto delivery fees are inconsistent.")
    if free_delivery.unlocked != (delivery_fee == 0):
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
        offers=offers,
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


def _free_delivery_offer(
    raw: object,
    subtotal: int,
    delivery_fee: int,
    delivery_fee_discount: int,
    delivery_fee_before_discount: int,
) -> OfferOpportunity:
    """Normalise Zepto's free-delivery progress object.

    Zepto explicitly provides both the threshold and amount remaining.  We
    reject a contradictory progress calculation because this drives the user
    facing "add ₹X" recommendation.  The potential saving is the currently
    charged *net* delivery fee only while the offer is locked; a discount
    already present in the preview must not be counted a second time. After
    unlock, the applied delivery discount is the only confirmed saving.
    """

    if not isinstance(raw, dict):
        raise ZeptoSchemaError("Zepto preview has invalid freeDelivery.")
    unlocked = _required_bool(raw, "isUnlocked")
    message = _required_str(raw, "message")
    minimum_order = _money(raw, "minimumOrderValue")
    remaining = _money(raw, "remainingAmountToUnlock")
    expected_remaining = max(0, minimum_order - subtotal)
    if remaining != expected_remaining:
        raise ZeptoSchemaError("Zepto free-delivery progress is inconsistent.")
    if unlocked != (remaining == 0):
        raise ZeptoSchemaError("Zepto free-delivery unlock state is inconsistent.")

    return OfferOpportunity(
        title=message,
        kind="free_delivery",
        minimum_order_paise=minimum_order,
        remaining_paise=remaining,
        potential_saving_paise=(
            delivery_fee if not unlocked and delivery_fee > 0 else None
        ),
        net_extra_paise=(
            max(0, remaining - delivery_fee)
            if not unlocked and delivery_fee > 0
            else None
        ),
        unlocked=unlocked,
        applied=unlocked and delivery_fee_discount > 0,
        provider_confirmed=True,
    )


def _offers(raw: object, subtotal: int) -> list[OfferOpportunity]:
    """Parse only unambiguous provider offers without affecting checkout.

    Offer cards are advisory metadata, not part of the price reconciliation.
    A malformed card is therefore omitted rather than guessed at or allowed to
    make a valid, exact checkout quote unavailable.  All accepted monetary
    amounts remain integer paise.
    """

    if raw is None:
        return []
    if not isinstance(raw, list):
        return []

    parsed: list[OfferOpportunity] = []
    for value in raw:
        offer = _offer(value, subtotal)
        if offer is not None:
            parsed.append(offer)
    return parsed


def _offer(raw: object, subtotal: int) -> OfferOpportunity | None:
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    offer_type = raw.get("type")
    minimum_order = raw.get("minimumOrderValue")
    discount = raw.get("discountAmount")
    unlocked = raw.get("isUnlocked")
    applied = raw.get("isApplied")
    auto_applicable = raw.get("isAutoApplicable")
    if (
        not isinstance(title, str)
        or not title.strip()
        or not isinstance(offer_type, str)
        or not offer_type.strip()
        or not _is_nonnegative_int(minimum_order)
        or not _is_nonnegative_int(discount)
        or not isinstance(unlocked, bool)
        or not isinstance(applied, bool)
        or not isinstance(auto_applicable, bool)
    ):
        return None

    remaining = 0 if unlocked else max(0, minimum_order - subtotal)
    # A provider-supplied positive discount is a potential, not a promise: a
    # non-auto offer may still require a particular card or payment method.
    # Bank offers are not actionable during a COD preview.  Do not turn a
    # provider card into a savings claim until the selected context supports
    # it (Zepto marks that with isAutoApplicable).
    potential = discount if not applied and auto_applicable and discount > 0 else None
    return OfferOpportunity(
        title=title.strip(),
        kind=_offer_kind(offer_type),
        minimum_order_paise=minimum_order,
        remaining_paise=remaining,
        potential_saving_paise=potential,
        net_extra_paise=(max(0, remaining - potential) if potential is not None else None),
        unlocked=unlocked,
        applied=applied,
        provider_confirmed=auto_applicable and (applied or potential is not None),
    )


def _offer_kind(raw: str) -> str:
    normalized = raw.strip().casefold().replace("-", "_").replace(" ", "_")
    if "bank" in normalized or "card" in normalized:
        return "bank_offer"
    if "coupon" in normalized or "promo" in normalized:
        return "promo"
    return "other"


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
    if not _is_nonnegative_int(raw):
        raise ZeptoSchemaError(f"Zepto preview has invalid {label}.")
    return raw


def _is_nonnegative_int(raw: object) -> bool:
    return not isinstance(raw, bool) and isinstance(raw, int) and raw >= 0


def _money(value: dict[str, Any], key: str) -> int:
    return _nonnegative_int(value.get(key), key)


def _nullable_money(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    return 0 if raw is None else _nonnegative_int(raw, key)


def _nullable_nonnegative_int(value: dict[str, Any], key: str) -> int | None:
    raw = value.get(key)
    return None if raw is None else _nonnegative_int(raw, key)
