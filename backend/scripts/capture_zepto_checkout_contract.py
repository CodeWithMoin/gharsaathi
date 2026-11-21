"""Capture a privacy-redacted Zepto checkout response contract.

This diagnostic never submits an order.  The preview action always sends
``confirmOrder=False`` and consequential calls are never replayed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Any

from app.commerce.zepto.adapter import parse_addresses, parse_products
from app.dependencies import close_cached_services, get_live_zepto_service


SENSITIVE_KEY_PARTS = (
    "address",
    "email",
    "latitude",
    "longitude",
    "mobile",
    "name",
    "phone",
    "url",
)
IDENTIFIER_KEY_PARTS = ("id", "token", "code", "reference")


def _masked_string(value: str, key: str) -> str:
    lowered = key.casefold()
    if key in {"paymentMethod", "type"} and value.isascii() and len(value) <= 32:
        return value
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if any(part in lowered for part in IDENTIFIER_KEY_PARTS):
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"<id:{digest}>"
    return f"<string:{len(value)}>"


def redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, str):
        return _masked_string(value, key)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


async def capture(include_preview: bool) -> None:
    service = get_live_zepto_service()
    try:
        status = await service.connection_status()
        cart = await service.cart()
        summary: dict[str, Any] = {
            "connected": status.connected,
            "cart": {
                "is_empty": cart.is_empty,
                "total_items": cart.total_items,
                "line_count": len(cart.items),
            },
        }
        if include_preview:
            if cart.is_empty:
                raise SystemExit(
                    "Zepto cart is empty. Add one test item in Zepto, then run again."
                )
            payment_methods = await service.client.call_tool(
                "get_payment_methods", {}, retry_on_auth=False
            )
            preview = await service.client.call_tool(
                "create_order",
                {"confirmOrder": False},
                retry_on_auth=False,
            )
            summary["payment_methods"] = redact(payment_methods)
            summary["order_preview"] = redact(preview)
        print(json.dumps(summary, indent=2, sort_keys=True))
    finally:
        await close_cached_services()


async def capture_with_test_item() -> None:
    """Seed one milk item, capture an unconfirmed preview, then remove it."""

    service = get_live_zepto_service()
    device_id = "gharsaathi-contract-capture"
    seeded: dict[str, Any] | None = None
    summary: dict[str, Any] = {}
    try:
        addresses = parse_addresses(
            await service.client.call_tool("list_saved_addresses", {})
        )
        if not addresses.addresses:
            raise SystemExit("The connected Zepto account has no saved address.")
        address = addresses.addresses[0]
        await service.client.call_tool(
            "select_saved_address", {"addressId": address.id}, retry_on_auth=False
        )
        products = parse_products(
            await service.client.call_tool(
                "search_products", {"query": "milk", "pageNumber": 0}
            )
        ).products
        available = [
            product
            for product in products
            if product.available and product.spin_id and product.sku_id
        ]
        if not available:
            raise SystemExit("No available milk item was returned for the saved address.")
        product = min(available, key=lambda item: item.price.offer_price)
        seeded = {
            "productVariantId": product.spin_id,
            "storeProductId": product.sku_id,
            "quantity": 1,
            "name": product.name,
            "label": product.name,
            "packSize": product.pack_size,
            "price": round(product.price.offer_price * 100),
            "mrp": round(product.price.mrp * 100),
            "isAd": False,
        }
        update_result = await service.client.call_tool(
            "update_cart",
            {"deviceId": device_id, "cartItems": [seeded], "replaceCart": False},
            retry_on_auth=False,
        )
        cart = await service.cart()
        if cart.is_empty:
            raise SystemExit("Zepto accepted the cart update but the MCP cart stayed empty.")

        payment_methods = await service.client.call_tool(
            "get_payment_methods", {}, retry_on_auth=False
        )
        preview = await service.client.call_tool(
            "create_order",
            {"confirmOrder": False, "userAddressId": address.id},
            retry_on_auth=False,
        )
        summary = {
            "seeded_product": {
                "title": product.name,
                "pack_size": product.pack_size,
                "unit_price_paise": round(product.price.offer_price * 100),
            },
            "seed_update": redact(update_result),
            "cart": {
                "is_empty": cart.is_empty,
                "total_items": cart.total_items,
                "line_count": len(cart.items),
            },
            "payment_methods": redact(payment_methods),
            "order_preview": redact(preview),
        }
    finally:
        cleanup_error: str | None = None
        if seeded is not None:
            try:
                await service.client.call_tool(
                    "update_cart",
                    {
                        "deviceId": device_id,
                        "cartItems": [
                            {
                                "productVariantId": seeded["productVariantId"],
                                "storeProductId": seeded["storeProductId"],
                                "quantity": 0,
                            }
                        ],
                        "replaceCart": False,
                    },
                    retry_on_auth=False,
                )
            except Exception as exc:  # cleanup status is intentionally surfaced
                cleanup_error = type(exc).__name__
        summary["cleanup"] = {
            "attempted": seeded is not None,
            "succeeded": seeded is not None and cleanup_error is None,
            "error_type": cleanup_error,
        }
        if summary:
            print(json.dumps(summary, indent=2, sort_keys=True))
        await close_cached_services()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Call get_payment_methods and create_order(confirmOrder=false).",
    )
    parser.add_argument(
        "--seed-preview-cleanup",
        action="store_true",
        help="Add one milk item, capture an unconfirmed preview, and remove it.",
    )
    args = parser.parse_args()
    if args.seed_preview_cleanup:
        asyncio.run(capture_with_test_item())
    else:
        asyncio.run(capture(args.preview))


if __name__ == "__main__":
    main()
