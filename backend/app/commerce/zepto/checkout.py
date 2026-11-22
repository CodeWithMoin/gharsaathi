"""Strict Zepto COD preview and confirmation parsing.

The MCP response is the source of truth for its bill.  The live catalogue
documents only ``create_order``'s *input* schema, so this module accepts the
response shape observed in the live Zepto catalogue exercise and rejects
ambiguities rather than making an order from a guessed total.

Tolerated, benign variations (each still subject to the safety checks):

* JSON numbers that are exactly integral (``1500.0``).  Zepto's own schemas
  type paise and quantities as JSON Schema ``number``; an integral float is
  the same exact value.  Fractional values and numeric strings are refused.
* Optional display/advisory fields that do not affect the bill: address
  ``label``/``addressLine``, item ``name``/``price``, ``totalItems``,
  ``freeDelivery``, ``otherFees``, ``offers`` and ``etaInMinutes`` may be
  absent or null.  When present they must still be valid and consistent.
* Absent/null ``packagingFee``, ``discount``, ``taxes`` and
  ``deliveryFeeDiscount`` count as zero; the bill must still reconcile to
  Zepto's own ``toPayAmount``, so a misread component cannot produce a total.

Every failure carries a stable ``step`` code (never provider data) so callers
can log a content-free diagnostic and tell the user which check failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.commerce.zepto.errors import ZeptoSchemaError
from app.shopping.errors import ApprovalRefused
from app.shopping.models import Basket, Charge, OfferOpportunity, Quote


PROVIDER = "zepto"
# Exact float-to-int conversion is only guaranteed below 2**53.
_MAX_EXACT_NUMBER = 2**53
# A subtotal this far from the locally priced basket means a unit mismatch
# (for example rupees instead of paise), not a price change.
_SUBTOTAL_SCALE_LIMIT = 20


class ZeptoPreviewCheckFailed(ZeptoSchemaError):
    """A preview could not be verified; ``step`` names the failed check."""

    def __init__(
        self, message: str, *, step: str, facts: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.step = step
        self.facts = facts or {}


class ZeptoPreviewRefused(ApprovalRefused):
    """A valid preview that does not match the approved basket or context."""

    def __init__(
        self, message: str, *, step: str, facts: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.step = step
        self.facts = facts or {}


@dataclass(frozen=True)
class ZeptoConfirmedOrder:
    """The minimal, confirmed result of a successful COD submission."""

    order_id: str
    payable_paise: int
    eta_minutes: int | None


def quote_from_cod_preview(basket: Basket, payload: dict[str, Any], quoted_at: datetime) -> Quote:
    """Validate an unconfirmed COD order preview for exactly ``basket``."""

    if basket.provider != PROVIDER:
        raise ApprovalRefused("This basket is not a Zepto basket.")
    if not basket.address_id:
        raise ApprovalRefused("Choose a delivery address first.")

    data = _data(payload)
    if _required_bool(data, "deliverable") is not True:
        raise ZeptoPreviewRefused(
            "The Zepto basket is not deliverable to this address.", step="deliverable"
        )
    if _required_bool(data, "isPreview") is not True:
        raise ZeptoPreviewCheckFailed("Zepto did not return an order preview.", step="isPreview")
    if _required_bool(data, "requiresConfirmation") is not True:
        raise ZeptoPreviewCheckFailed(
            "Zepto preview is missing its confirmation boundary.", step="requiresConfirmation"
        )
    if _required_str(data, "paymentMethod") != "COD":
        raise ZeptoPreviewRefused(
            "Zepto did not prepare a cash-on-delivery checkout.", step="paymentMethod"
        )

    delivery_address = data.get("deliveryAddress")
    if not isinstance(delivery_address, dict):
        raise ZeptoPreviewCheckFailed(
            "Zepto preview is missing the delivery address.", step="deliveryAddress"
        )
    _validate_delivery_address(delivery_address, basket.address_id)
    _validate_exact_items(basket, data.get("items"))

    subtotal = _money(data, "subTotal")
    delivery_fee = _money(data, "deliveryFee")
    delivery_fee_discount = _optional_money(data, "deliveryFeeDiscount")
    raw_pre_discount = data.get("deliveryFeePreDiscount")
    delivery_fee_before_discount = (
        None if raw_pre_discount is None else _money(data, "deliveryFeePreDiscount")
    )
    packaging_fee = _optional_money(data, "packagingFee")
    discount = _optional_money(data, "discount")
    taxes = _optional_money(data, "taxes")
    payable = _money(data, "toPayAmount")
    other_fees = _other_fees(data.get("otherFees"))
    eta_minutes = _nullable_count(data, "etaInMinutes")
    total_items = _nullable_count(data, "totalItems")

    local_total = sum(line.unit_price_paise * line.quantity for line in basket.lines)
    if local_total > 0 and (
        subtotal * _SUBTOTAL_SCALE_LIMIT < local_total
        or subtotal > local_total * _SUBTOTAL_SCALE_LIMIT
    ):
        raise ZeptoPreviewCheckFailed(
            "Zepto preview amounts are not in the expected unit.",
            step="subTotal.scale",
            facts={"subtotal_vs_local": "lower" if subtotal < local_total else "higher"},
        )
    if (
        delivery_fee_before_discount is not None
        and delivery_fee_before_discount - delivery_fee_discount != delivery_fee
    ):
        raise ZeptoPreviewCheckFailed(
            "Zepto delivery fees are inconsistent.", step="deliveryFee.consistency"
        )

    free_delivery = _free_delivery_offer(
        data.get("freeDelivery"), subtotal, delivery_fee, delivery_fee_discount
    )
    if free_delivery is not None and free_delivery.unlocked != (delivery_fee == 0):
        raise ZeptoPreviewCheckFailed(
            "Zepto free-delivery status is inconsistent.",
            step="freeDelivery.fee_state",
            facts={"unlocked": free_delivery.unlocked, "delivery_fee_zero": delivery_fee == 0},
        )
    offers = [
        *([free_delivery] if free_delivery is not None else []),
        *_offers(data.get("offers"), subtotal),
    ]

    basket_quantity = sum(line.quantity for line in basket.lines)
    # Zepto reports ``totalItems`` as the number of distinct products, while some
    # previews count units. Either is consistent; per-item quantities are checked
    # exactly by ``_validate_exact_items``.
    if total_items is not None and total_items not in {basket_quantity, len(basket.lines)}:
        raise ZeptoPreviewRefused(
            "Zepto preview has a different item quantity than the basket.",
            step="totalItems",
            facts={"total_items_vs_basket": "lower" if total_items < basket_quantity else "higher"},
        )

    charges = [
        Charge(label="Delivery fee", amount_paise=delivery_fee),
        Charge(label="Packaging fee", amount_paise=packaging_fee),
    ]
    if taxes:
        charges.append(Charge(label="Taxes", amount_paise=taxes))
    charges.extend(other_fees)
    if discount:
        charges.append(Charge(label="Discount", amount_paise=-discount))

    computed = subtotal + sum(charge.amount_paise for charge in charges)
    if computed != payable:
        raise ZeptoPreviewCheckFailed(
            "Zepto preview charges do not add up to the payable amount.",
            step="payable.reconciliation",
            facts=_reconciliation_facts(data, computed - payable),
        )

    return Quote(
        provider=PROVIDER,
        basket_id=basket.id,
        basket_revision=basket.revision,
        item_total_paise=subtotal,
        item_discount_paise=_item_discount(discount, data.get("offers")),
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
    eta_minutes = _nullable_count(data, "etaInMinutes")
    return ZeptoConfirmedOrder(
        order_id=order_id,
        payable_paise=payable,
        eta_minutes=eta_minutes,
    )


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ZeptoPreviewCheckFailed("Zepto returned an invalid checkout response.", step="envelope")
    if set(payload) == {"content_text"}:
        # The MCP client wraps successful non-JSON text in this envelope.
        raise ZeptoPreviewCheckFailed(
            "Zepto returned text instead of a structured checkout response.",
            step="envelope.text",
        )
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ZeptoPreviewCheckFailed("Zepto returned an invalid checkout response.", step="envelope")
    return data


def _validate_delivery_address(address: dict[str, Any], expected_id: str) -> None:
    """Require exactly one returned address id, matching the selected address.

    Provider versions differ in whether the identifier is called ``id`` or
    ``addressId``.  Display fields (``label``, ``addressLine``) are not needed
    for safety and are never retained or logged.
    """

    present = [
        key
        for key in ("id", "addressId")
        if isinstance(address.get(key), str) and address[key].strip()
    ]
    identifiers = {address[key] for key in present}
    if len(identifiers) != 1 or next(iter(identifiers)) != expected_id:
        raise ZeptoPreviewRefused(
            "Zepto preview is for a different delivery address.",
            step="deliveryAddress.id",
            facts={
                "identifier_keys": present,
                "matches_selected": expected_id in identifiers,
            },
        )


def _validate_exact_items(basket: Basket, raw_items: object) -> None:
    if not isinstance(raw_items, list):
        raise ZeptoPreviewCheckFailed("Zepto preview is missing its items list.", step="items")

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
            raise ZeptoPreviewCheckFailed("Zepto preview contains an invalid item.", step="items[]")
        product_variant_id = _required_str(raw, "productVariantId", "items[].productVariantId")
        store_product_id = _required_str(raw, "storeProductId", "items[].storeProductId")
        quantity = _count(raw.get("quantity"), "items[].quantity")
        if raw.get("price") is not None:
            _count(raw.get("price"), "items[].price")
        key = (product_variant_id, store_product_id)
        if key in actual:
            raise ZeptoPreviewCheckFailed(
                "Zepto preview contains duplicate item identifiers.", step="items.duplicate"
            )
        actual[key] = quantity

    if actual != expected:
        expected_variants = {variant for variant, _ in expected}
        expected_store_products = {store_product for _, store_product in expected}
        unmatched = [key for key in actual if key not in expected]
        raise ZeptoPreviewRefused(
            "Zepto preview does not exactly match the basket.",
            step="items.match",
            facts={
                "expected_lines": len(expected),
                "returned_lines": len(actual),
                "matched_identifiers": sum(1 for key in actual if key in expected),
                "quantity_mismatches": sum(
                    1 for key, quantity in actual.items()
                    if key in expected and expected[key] != quantity
                ),
                "variant_only_matches": sum(
                    1 for variant, store_product in unmatched
                    if variant in expected_variants and store_product not in expected_store_products
                ),
                "store_product_only_matches": sum(
                    1 for variant, store_product in unmatched
                    if store_product in expected_store_products and variant not in expected_variants
                ),
            },
        )


def _other_fees(raw: object) -> list[Charge]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ZeptoPreviewCheckFailed("Zepto preview has invalid other fees.", step="otherFees")
    charges: list[Charge] = []
    labels: set[str] = set()
    for value in raw:
        if not isinstance(value, dict):
            raise ZeptoPreviewCheckFailed(
                "Zepto preview has an invalid other fee.", step="otherFees[]"
            )
        label = _required_str(value, "label", "otherFees[].label")
        if label in labels:
            raise ZeptoPreviewCheckFailed(
                "Zepto preview has duplicate other fee labels.", step="otherFees.duplicate"
            )
        labels.add(label)
        charges.append(
            Charge(label=label, amount_paise=_count(value.get("amount"), "otherFees[].amount"))
        )
    return charges


def _free_delivery_offer(
    raw: object,
    subtotal: int,
    delivery_fee: int,
    delivery_fee_discount: int,
) -> OfferOpportunity | None:
    """Normalise Zepto's free-delivery progress object.

    Zepto explicitly provides both the threshold and amount remaining.  We
    reject a contradictory progress calculation because this drives the user
    facing "add ₹X" recommendation.  The potential saving is the currently
    charged *net* delivery fee only while the offer is locked; a discount
    already present in the preview must not be counted a second time. After
    unlock, the applied delivery discount is the only confirmed saving.

    An absent or null object means Zepto sent no free-delivery progress; no
    offer is shown.  A present but malformed object is still refused.
    """

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ZeptoPreviewCheckFailed("Zepto preview has invalid freeDelivery.", step="freeDelivery")
    unlocked = _required_bool(raw, "isUnlocked", "freeDelivery.isUnlocked")
    message = raw.get("message")
    title = message.strip() if isinstance(message, str) and message.strip() else "Free delivery"
    minimum_order = _count(raw.get("minimumOrderValue"), "freeDelivery.minimumOrderValue")
    remaining = _count(raw.get("remainingAmountToUnlock"), "freeDelivery.remainingAmountToUnlock")
    expected_remaining = max(0, minimum_order - subtotal)
    if remaining != expected_remaining:
        raise ZeptoPreviewCheckFailed(
            "Zepto free-delivery progress is inconsistent.",
            step="freeDelivery.progress",
            facts={
                "subtotal_vs_minimum": _compare(subtotal, minimum_order),
                "remaining_vs_expected": _compare(remaining, expected_remaining),
            },
        )
    if unlocked != (remaining == 0):
        raise ZeptoPreviewCheckFailed(
            "Zepto free-delivery unlock state is inconsistent.",
            step="freeDelivery.unlock_state",
            facts={"unlocked": unlocked, "remaining_zero": remaining == 0},
        )

    return OfferOpportunity(
        title=title,
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


def _item_discount(discount: int, raw_offers: object) -> int:
    """The part of Zepto's ``discount`` that brings ``subTotal`` (at MRP) down to
    the items' selling prices: the total minus every applied offer card.

    If an applied card's amount is unreadable the split is unknown, and the
    whole discount is returned; the item-total check then reports a mismatch.
    """

    applied = 0
    for raw in raw_offers if isinstance(raw_offers, list) else []:
        if not isinstance(raw, dict) or raw.get("isApplied") is not True:
            continue
        amount = _exact_count(raw.get("discountAmount"))
        if amount is None:
            return discount
        applied += amount
    return max(0, discount - applied)


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
    minimum_order = _exact_count(raw.get("minimumOrderValue"))
    discount = _exact_count(raw.get("discountAmount"))
    unlocked = raw.get("isUnlocked")
    applied = raw.get("isApplied")
    auto_applicable = raw.get("isAutoApplicable")
    if (
        not isinstance(title, str)
        or not title.strip()
        or not isinstance(offer_type, str)
        or not offer_type.strip()
        or minimum_order is None
        or discount is None
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


def _reconciliation_facts(data: dict[str, Any], difference: int) -> dict[str, Any]:
    """Which top-level amount fields equal the gap, by name only."""

    gap = abs(difference)
    matches = sorted(
        key
        for key, value in data.items()
        if isinstance(key, str) and _exact_count(value) == gap and gap > 0
    )
    return {
        "computed_vs_payable": "higher" if difference > 0 else "lower",
        "gap_equals_fields": matches,
        "gap_equals_twice_field": sorted(
            key
            for key, value in data.items()
            if isinstance(key, str) and gap > 0 and _exact_count(value) == gap / 2
        ),
    }


def _compare(left: int, right: int) -> str:
    return "equal" if left == right else ("lower" if left < right else "higher")


def _required_bool(value: dict[str, Any], key: str, step: str | None = None) -> bool:
    raw = value.get(key)
    if not isinstance(raw, bool):
        raise ZeptoPreviewCheckFailed(f"Zepto preview has invalid {key}.", step=step or key)
    return raw


def _required_str(value: dict[str, Any], key: str, step: str | None = None) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ZeptoPreviewCheckFailed(f"Zepto preview has invalid {key}.", step=step or key)
    return raw


def _exact_count(raw: object) -> int | None:
    """A non-negative integer, or an exactly integral JSON number; else None."""

    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float) and raw.is_integer() and 0 <= raw < _MAX_EXACT_NUMBER:
        return int(raw)
    return None


def _count(raw: object, step: str) -> int:
    value = _exact_count(raw)
    if value is None:
        label = step.rsplit(".", 1)[-1]
        raise ZeptoPreviewCheckFailed(f"Zepto preview has invalid {label}.", step=step)
    return value


def _money(value: dict[str, Any], key: str) -> int:
    return _count(value.get(key), key)


def _optional_money(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    return 0 if raw is None else _count(raw, key)


def _nullable_count(value: dict[str, Any], key: str) -> int | None:
    raw = value.get(key)
    return None if raw is None else _count(raw, key)
