"""Read-only normalisation for Zepto's ``view_cart`` response.

Only the envelope and item fields confirmed by the live Zepto catalogue are
interpreted here.  This keeps a cart inspection useful without treating an
undocumented total or fee field as a bill that may be approved or charged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.commerce.zepto.errors import ZeptoSchemaError


@dataclass(frozen=True)
class ZeptoCartItem:
    """One provider-cart line, with every monetary value expressed in paise."""

    product_variant_id: str
    store_product_id: str
    quantity: int
    name: str | None
    label: str | None
    unit_price_paise: int | None
    mrp_paise: int | None
    image_url: str | None
    pack_size: str | None
    available_quantity: int | None
    line_total_paise: int | None


@dataclass(frozen=True)
class ZeptoCart:
    """A safe, display-only representation of a Zepto cart.

    ``item_total_paise`` is calculated solely from the returned item prices.
    It is ``None`` whenever even one line does not expose a price.  Zepto's
    current verified ``view_cart`` shape contains neither a bill total nor a
    fee breakdown, so callers must show ``warnings`` rather than infer either.
    """

    is_empty: bool
    total_items: int
    items: tuple[ZeptoCartItem, ...]
    item_total_paise: int | None
    warnings: tuple[str, ...]


_MISSING_TOTAL_WARNING = "Zepto did not provide a cart total."
_MISSING_FEES_WARNING = "Zepto did not provide a fee breakdown."
_MISSING_PRICE_WARNING = (
    "One or more cart items did not include a price; the item total could not be calculated."
)


def parse_view_cart(payload: dict[str, Any]) -> ZeptoCart:
    """Parse a ``view_cart`` response without making any provider mutation.

    The only confirmed response envelope is ``isEmpty``, ``totalItems`` and
    ``items``.  Any malformed field in that shape is rejected rather than
    silently guessed.  Optional item display fields are retained only when
    they match their documented types.
    """

    data = _cart_payload(payload)
    is_empty = _required_bool(data, "isEmpty")
    total_items = _required_non_negative_int(data, "totalItems")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ZeptoSchemaError("Zepto returned an invalid cart items list.")

    items = tuple(_parse_item(raw) for raw in raw_items)
    if is_empty and (total_items != 0 or items):
        raise ZeptoSchemaError("Zepto returned contradictory empty-cart data.")
    if not is_empty and total_items == 0:
        raise ZeptoSchemaError("Zepto returned a non-empty cart with zero items.")

    item_total = _item_total(items)
    warnings: list[str] = []
    if item_total is None:
        warnings.append(_MISSING_PRICE_WARNING)
    # These fields are deliberately warnings, not guesses: the live catalogue
    # confirms no bill-total or fee-breakdown response schema for view_cart.
    if not is_empty:
        warnings.extend((_MISSING_TOTAL_WARNING, _MISSING_FEES_WARNING))

    return ZeptoCart(
        is_empty=is_empty,
        total_items=total_items,
        items=items,
        item_total_paise=item_total,
        warnings=tuple(warnings),
    )


# ``parse_cart`` is a concise alias for callers which do not need to know the
# provider tool name.  It has exactly the same read-only behaviour.
parse_cart = parse_view_cart


def _cart_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ZeptoSchemaError("Zepto returned an invalid cart response.")
    if "data" not in payload:
        return payload
    data = payload["data"]
    if not isinstance(data, dict):
        raise ZeptoSchemaError("Zepto returned an invalid cart response envelope.")
    return data


def _parse_item(raw: object) -> ZeptoCartItem:
    if not isinstance(raw, dict):
        raise ZeptoSchemaError("Zepto returned an invalid cart item.")
    price = _optional_paise(raw, "price")
    quantity = _required_positive_int(raw, "quantity")
    return ZeptoCartItem(
        product_variant_id=_required_non_empty_str(raw, "productVariantId"),
        store_product_id=_required_non_empty_str(raw, "storeProductId"),
        quantity=quantity,
        name=_optional_str(raw, "name"),
        label=_optional_str(raw, "label"),
        unit_price_paise=price,
        mrp_paise=_optional_paise(raw, "mrp"),
        image_url=_optional_str(raw, "imageUrl"),
        pack_size=_optional_str(raw, "packSize"),
        available_quantity=_optional_non_negative_int(raw, "availableQuantity"),
        line_total_paise=price * quantity if price is not None else None,
    )


def _item_total(items: tuple[ZeptoCartItem, ...]) -> int | None:
    if any(item.line_total_paise is None for item in items):
        return None
    return sum(item.line_total_paise or 0 for item in items)


def _required_bool(value: dict[str, Any], field: str) -> bool:
    raw = value.get(field)
    if not isinstance(raw, bool):
        raise ZeptoSchemaError(f"Zepto cart is missing boolean {field}.")
    return raw


def _required_non_empty_str(value: dict[str, Any], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ZeptoSchemaError(f"Zepto cart is missing {field}.")
    return raw


def _optional_str(value: dict[str, Any], field: str) -> str | None:
    raw = value.get(field)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ZeptoSchemaError(f"Zepto cart has an invalid {field}.")
    return raw


def _required_non_negative_int(value: dict[str, Any], field: str) -> int:
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ZeptoSchemaError(f"Zepto cart has an invalid {field}.")
    return raw


def _required_positive_int(value: dict[str, Any], field: str) -> int:
    raw = _required_non_negative_int(value, field)
    if raw == 0:
        raise ZeptoSchemaError(f"Zepto cart has an invalid {field}.")
    return raw


def _optional_non_negative_int(value: dict[str, Any], field: str) -> int | None:
    raw = value.get(field)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ZeptoSchemaError(f"Zepto cart has an invalid {field}.")
    return raw


def _optional_paise(value: dict[str, Any], field: str) -> int | None:
    """Accept only non-negative integer paise; floats are not safe money."""

    raw = value.get(field)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ZeptoSchemaError(f"Zepto cart has an invalid {field} price.")
    return raw
