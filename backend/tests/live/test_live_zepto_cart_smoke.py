"""Tier 2 — CART-MUTATING live Zepto quote smoke check (second explicit opt-in).

WARNING: this REPLACES the connected account's Zepto cart with one cheap item,
asks Zepto for an UNCONFIRMED cash-on-delivery preview
(``create_order(confirmOrder=False)``), parses it with the production
``quote_from_cod_preview`` path, and then removes the item (or restores the
original cart when ``GHARSAATHI_LIVE_SMOKE_CART_RESTORE=1``).

A second check asks each prepaid order tool (``create_online_payment_order``,
``create_wallet_order``, ``create_upi_reserve_pay_order``) for an UNCONFIRMED
preview only, records the response shapes, and restores the cart the same way.

It can never confirm, pay or check out: every call goes through
``GuardedToolClient``, which raises ``ForbiddenLiveCall`` for ``confirmOrder``
values other than ``False`` on every ``create_*`` tool, for
``check_payment_status`` and any ``poll`` flag, and for every other
payment/checkout tool.
"""

from __future__ import annotations

import os
import warnings
from datetime import UTC, datetime
from typing import Any

import pytest

from app.commerce.zepto.cart import ZeptoCart, parse_view_cart
from app.commerce.zepto.checkout import quote_from_preview
from app.commerce.zepto.quote_provider import ZeptoQuoteProvider, device_id_for
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Basket, BasketLine, ProductRef, rupees_to_paise
from tests.live.harness import CART_RESTORE_ENV, LiveSmokeHalted, guarded_for_tier

pytestmark = [
    pytest.mark.live,
    pytest.mark.live_cart,
    pytest.mark.asyncio(loop_scope="session"),
]

CHEAP_ITEM_QUERY = "milk"
BANNER = (
    "\n" + "!" * 72 + "\n"
    "!!  LIVE CART TIER: this REPLACES the connected Zepto cart with one item,\n"
    "!!  requests an UNCONFIRMED COD preview, then removes/restores the item.\n"
    "!!  No order is ever confirmed. Check the Zepto app cart afterwards.\n"
    + "!" * 72
)


def _one_line_basket(address_id: str, product: Any) -> Basket:
    now = datetime.now(UTC)
    return Basket(
        id="live-smoke-basket",
        household_id="live-smoke",
        provider="zepto",
        address_id=address_id,
        lines=[
            BasketLine(
                ref=ProductRef(
                    provider="zepto",
                    product_id=product.product_id,
                    variant_id=product.spin_id,
                    sku_id=product.sku_id,
                ),
                name=product.name,
                brand=product.brand,
                pack_size=product.pack_size,
                unit_price_paise=rupees_to_paise(product.price.offer_price),
                mrp_paise=rupees_to_paise(product.price.mrp),
                quantity=1,
            )
        ],
        created_at=now,
        updated_at=now,
    )


def _safe_failure(error: BaseException | None, item_name: str) -> dict[str, Any]:
    if error is None:
        return {"cause_type": None, "cause_message": None}
    # Our own parser messages are static strings; a few embed the item name,
    # which is scrubbed so the report stays content-free.
    return {
        "cause_type": type(error).__name__,
        "cause_message": str(error).replace(item_name, "<item>"),
    }


async def _cleanup(
    client: Any, basket: Basket, original: ZeptoCart, restore: bool
) -> dict[str, Any]:
    line = basket.lines[0]
    device_id = device_id_for(basket)
    if restore and not original.is_empty:
        items = [
            {
                "productVariantId": item.product_variant_id,
                "storeProductId": item.store_product_id,
                "quantity": item.quantity,
            }
            for item in original.items
        ]
        arguments = {"deviceId": device_id, "cartItems": items, "replaceCart": True}
        mode = "restore_original"
    else:
        arguments = {
            "deviceId": device_id,
            "cartItems": [
                {
                    "productVariantId": line.ref.variant_id,
                    "storeProductId": line.ref.sku_id,
                    "quantity": 0,
                }
            ],
            "replaceCart": False,
        }
        mode = "remove_seeded_item"
    try:
        await client.call_tool("update_cart", arguments, retry_on_auth=False)
        after = parse_view_cart(
            await client.call_tool("view_cart", {}, retry_on_auth=False)
        )
    except LiveSmokeHalted:
        return {"mode": mode, "succeeded": False, "error_type": "rate_limited"}
    except Exception as exc:
        return {"mode": mode, "succeeded": False, "error_type": type(exc).__name__}
    restored = after.total_items == original.total_items and (
        after.is_empty == original.is_empty
    )
    return {"mode": mode, "succeeded": restored, "error_type": None}


async def test_zepto_cod_preview_parses_with_production_quote_path(
    live_zepto: Any,
) -> None:
    print(BANNER)
    warnings.warn(BANNER, stacklevel=1)
    cart_client = guarded_for_tier(live_zepto.client, "cart")
    restore = os.environ.get(CART_RESTORE_ENV, "").strip() == "1"

    original = parse_view_cart(
        await cart_client.call_tool("view_cart", {}, retry_on_auth=False)
    )
    if not original.is_empty and not restore:
        pytest.skip(
            "The Zepto cart is not empty. Empty it in the Zepto app first, or set "
            f"{CART_RESTORE_ENV}=1 to replace it and restore it afterwards."
        )

    addresses = await live_zepto.service.addresses()
    if not addresses.addresses:
        pytest.skip("The connected Zepto account has no saved address.")
    address_id = addresses.resolution.default_address_id or addresses.addresses[0].id

    search = await live_zepto.service.search(CHEAP_ITEM_QUERY, address_id)
    candidates = [
        product
        for product in search.products
        if product.available and product.spin_id and product.sku_id
    ]
    if not candidates:
        pytest.skip(f"No available {CHEAP_ITEM_QUERY!r} item for the saved address.")
    product = min(candidates, key=lambda item: item.price.offer_price)
    basket = _one_line_basket(address_id, product)

    provider = ZeptoQuoteProvider(cart_client)
    quote = None
    failure: BaseException | None = None
    cleanup: dict[str, Any] = {"mode": None, "succeeded": True, "error_type": None}
    try:
        quote = await provider.quote_unlocked(basket)
    except (ProviderUnavailable, ApprovalRefused) as exc:
        failure = exc.__cause__ or exc
    finally:
        if "update_cart" in cart_client.calls:
            cleanup = await _cleanup(cart_client, basket, original, restore)

    live_zepto.recorder.record_check(
        "zepto",
        "cod_preview_quote",
        parsed=quote is not None,
        charge_count=len(quote.charges) if quote else None,
        offer_count=len(quote.offers) if quote else None,
        unavailable_count=len(quote.unavailable_refs) if quote else None,
        eta_present=bool(quote and quote.eta_text),
        cleanup=cleanup,
        original_cart_was_empty=original.is_empty,
        **_safe_failure(failure, product.name),
    )

    assert cleanup["succeeded"], (
        f"Zepto cart cleanup did not complete ({cleanup}). Open the Zepto app and "
        "remove the smoke-test item manually."
    )
    assert quote is not None, (
        "quote_from_cod_preview rejected the live preview: "
        f"{_safe_failure(failure, product.name)}. The preview's key paths/types are "
        "in backend/.live-smoke/latest.json under tool 'create_order'."
    )
    assert quote.payable_paise >= 0
    assert quote.basket_id == basket.id


PREPAID_PREVIEW_TOOLS = (
    "create_online_payment_order",
    "create_wallet_order",
    "create_upi_reserve_pay_order",
)


def _prepaid_preview_facts(basket: Basket, payload: object, cod_payable: int | None) -> dict[str, Any]:
    """Content-free facts: does this preview have the COD preview's bill shape?"""

    data = payload.get("data", payload) if isinstance(payload, dict) else None
    method = data.get("paymentMethod") if isinstance(data, dict) else None
    facts: dict[str, Any] = {
        "payment_method_present": isinstance(method, str) and bool(method.strip()),
        "payment_method_is_cod": method == "COD",
        "parses_with_cod_bill_rules": False,
        "payable_equals_cod_preview": None,
        "cause_type": None,
    }
    if not isinstance(method, str):
        return facts
    try:
        quote = quote_from_preview(
            basket, payload, datetime.now(UTC), payment_method_value=method  # type: ignore[arg-type]
        )
    except Exception as exc:  # noqa: BLE001 - recorded by type and step only
        facts["cause_type"] = type(exc).__name__
        facts["cause_step"] = getattr(exc, "step", None)
        return facts
    facts["parses_with_cod_bill_rules"] = True
    facts["payable_equals_cod_preview"] = (
        None if cod_payable is None else quote.payable_paise == cod_payable
    )
    return facts


async def test_zepto_prepaid_previews_record_shapes_only(live_zepto: Any) -> None:
    """Payment contract discovery: unconfirmed prepaid previews, then cart cleanup."""

    print(BANNER)
    warnings.warn(BANNER, stacklevel=1)
    cart_client = guarded_for_tier(live_zepto.client, "cart")
    restore = os.environ.get(CART_RESTORE_ENV, "").strip() == "1"

    original = parse_view_cart(
        await cart_client.call_tool("view_cart", {}, retry_on_auth=False)
    )
    if not original.is_empty and not restore:
        pytest.skip(
            "The Zepto cart is not empty. Empty it in the Zepto app first, or set "
            f"{CART_RESTORE_ENV}=1 to replace it and restore it afterwards."
        )

    addresses = await live_zepto.service.addresses()
    if not addresses.addresses:
        pytest.skip("The connected Zepto account has no saved address.")
    address_id = addresses.resolution.default_address_id or addresses.addresses[0].id

    search = await live_zepto.service.search(CHEAP_ITEM_QUERY, address_id)
    candidates = [
        product
        for product in search.products
        if product.available and product.spin_id and product.sku_id
    ]
    if not candidates:
        pytest.skip(f"No available {CHEAP_ITEM_QUERY!r} item for the saved address.")
    product = min(candidates, key=lambda item: item.price.offer_price)
    basket = _one_line_basket(address_id, product)

    provider = ZeptoQuoteProvider(cart_client)
    previews: dict[str, dict[str, Any]] = {}
    cleanup: dict[str, Any] = {"mode": None, "succeeded": True, "error_type": None}
    cod_payable: int | None = None
    try:
        try:
            # Seeds the cart with the one item, exactly as a real bill does.
            cod_payable = (await provider.quote_unlocked(basket)).payable_paise
        except (ProviderUnavailable, ApprovalRefused):
            cod_payable = None
        if "update_cart" in cart_client.calls:
            for tool in PREPAID_PREVIEW_TOOLS:
                try:
                    payload = await cart_client.call_tool(
                        tool,
                        {"confirmOrder": False, "userAddressId": address_id},
                        retry_on_auth=False,
                    )
                except LiveSmokeHalted:
                    raise
                except Exception as exc:  # noqa: BLE001 - recorded by type only
                    previews[tool] = {"error_type": type(exc).__name__}
                    continue
                previews[tool] = _prepaid_preview_facts(basket, payload, cod_payable)
    finally:
        if "update_cart" in cart_client.calls:
            cleanup = await _cleanup(cart_client, basket, original, restore)

    live_zepto.recorder.record_check(
        "zepto",
        "prepaid_preview_shapes",
        cod_preview_parsed=cod_payable is not None,
        previews=previews,
        cleanup=cleanup,
        original_cart_was_empty=original.is_empty,
    )
    assert cleanup["succeeded"], (
        f"Zepto cart cleanup did not complete ({cleanup}). Open the Zepto app and "
        "remove the smoke-test item manually."
    )
    assert "check_payment_status" not in cart_client.calls
