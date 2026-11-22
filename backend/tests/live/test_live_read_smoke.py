"""Tier 1 — read-only live provider smoke checks (opt-in; see docs/LIVE_SMOKE.md).

Each check makes one controlled provider request through the real service
classes. Results are asserted on shape only and recorded as a content-free
report under ``backend/.live-smoke/``.
"""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from typing import Any

import pytest

from app.commerce.swiggy.models import AddressList, Product, ProductSearchResult
from tests.live.harness import (
    REPORT_DIR,
    catalogue_entries,
    compare_catalogues,
    latest_snapshot,
)

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="session")]

SEARCH_QUERY = "atta"
_ADDRESS: dict[str, str] = {}


def _write_live_catalogue(provider: str, entries: list[dict[str, Any]]) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    path = REPORT_DIR / f"{provider}_tools_catalogue_{stamp}.json"
    path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n")
    return str(path)


async def _check_connection(live: Any) -> None:
    status = await live.service.connection_status()
    assert status.connected is True
    live.recorder.record_check(
        live.name,
        "connection_status",
        connected=status.connected,
        expires_at_present=status.expires_at is not None,
    )


async def _check_catalogue(live: Any) -> None:
    tools = await live.service.list_tools()
    entries = catalogue_entries(tools)
    assert entries, f"{live.name} returned an empty tool catalogue."
    written = _write_live_catalogue(live.name, entries)
    snapshot_path = latest_snapshot(live.name)
    if snapshot_path is None:
        live.recorder.record_check(
            live.name,
            "catalogue",
            tool_count=len(entries),
            snapshot="missing",
            live_catalogue_written=True,
        )
        warnings.warn(
            f"No committed {live.name} catalogue snapshot. Review {written} and "
            f"commit it as backend/tests/fixtures/{live.name}_tools_catalogue_<date>.json.",
            stacklevel=1,
        )
        return
    diff = compare_catalogues(json.loads(snapshot_path.read_text()), entries)
    live.recorder.record_check(
        live.name,
        "catalogue",
        tool_count=len(entries),
        snapshot=snapshot_path.name,
        **diff,
    )
    drift = {key: diff[key] for key in ("added", "removed", "schema_changed") if diff[key]}
    assert not drift, (
        f"{live.name} live tool catalogue drifted from {snapshot_path.name}: {drift}. "
        f"Live copy written to {written}; review adapters before refreshing the snapshot."
    )


def _record_addresses(live: Any, result: AddressList) -> None:
    assert isinstance(result, AddressList)
    for address in result.addresses:
        assert isinstance(address.id, str) and address.id
        assert isinstance(address.address_line, str)
    live.recorder.record_check(
        live.name,
        "addresses",
        address_count=len(result.addresses),
        has_default=result.resolution.default_address_id is not None,
        needs_clarification=result.resolution.needs_user_clarification,
    )
    if result.addresses:
        _ADDRESS[live.name] = (
            result.resolution.default_address_id or result.addresses[0].id
        )


def _record_search(live: Any, result: ProductSearchResult) -> None:
    assert isinstance(result, ProductSearchResult)
    assert result.products, (
        f"{live.name} returned no parseable products for {SEARCH_QUERY!r}; "
        "check the adapter against the response shape in the report."
    )
    for product in result.products:
        Product.model_validate(product.model_dump())
        assert product.product_id and product.sku_id
        assert product.price.offer_price >= 0 and product.price.mrp >= 0
    products = result.products
    live.recorder.record_check(
        live.name,
        "search",
        product_count=len(products),
        similar_count=len(result.similar_products),
        available_count=sum(p.available for p in products),
        with_brand=sum(p.brand is not None for p in products),
        with_pack_size=sum(p.pack_size is not None for p in products),
        with_image=sum(p.image_url is not None for p in products),
        has_next_offset=result.next_offset is not None,
    )


def _address_for(provider: str) -> str:
    address = _ADDRESS.get(provider)
    if address is None:
        pytest.skip(f"No {provider} saved address was available for search.")
    return address


# ---- Swiggy ---------------------------------------------------------------


async def test_swiggy_connection_status(live_swiggy: Any) -> None:
    await _check_connection(live_swiggy)


async def test_swiggy_tool_catalogue(live_swiggy: Any) -> None:
    await _check_catalogue(live_swiggy)


async def test_swiggy_saved_addresses_shape(live_swiggy: Any) -> None:
    _record_addresses(live_swiggy, await live_swiggy.service.addresses(1, 10))


async def test_swiggy_product_search_parses(live_swiggy: Any) -> None:
    address = _address_for("swiggy")
    _record_search(live_swiggy, await live_swiggy.service.search(SEARCH_QUERY, address, 0))


# ---- Zepto ----------------------------------------------------------------


async def test_zepto_connection_status(live_zepto: Any) -> None:
    await _check_connection(live_zepto)


async def test_zepto_tool_catalogue(live_zepto: Any) -> None:
    await _check_catalogue(live_zepto)


async def test_zepto_saved_addresses_shape(live_zepto: Any) -> None:
    _record_addresses(live_zepto, await live_zepto.service.addresses())


async def test_zepto_product_search_parses(live_zepto: Any) -> None:
    address = _address_for("zepto")
    _record_search(live_zepto, await live_zepto.service.search(SEARCH_QUERY, address))


async def test_zepto_payment_methods_shape(live_zepto: Any) -> None:
    """Payment contract discovery: one ``get_payment_methods`` call, shape only.

    The response's key paths and JSON types land in the report under
    ``responses[tool == "get_payment_methods"]``; no value is recorded. They are
    the input for ``app/commerce/zepto/payments.py::VERIFIED_CONTRACT``.
    """

    from app.commerce.zepto.payments import (
        VERIFIED_CONTRACT,
        PaymentShapeUnknown,
        parse_payment_methods,
    )

    payload = await live_zepto.client.call_tool(
        "get_payment_methods", {}, retry_on_auth=False
    )
    facts: dict[str, Any] = {
        "top_level_type": type(payload).__name__,
        "top_level_key_count": len(payload) if isinstance(payload, dict) else None,
        "text_envelope": isinstance(payload, dict) and set(payload) == {"content_text"},
        "contract_verified": VERIFIED_CONTRACT is not None,
    }
    if VERIFIED_CONTRACT is not None:
        try:
            facts["contract_methods"] = sorted(
                parse_payment_methods(payload, VERIFIED_CONTRACT.methods)
            )
        except PaymentShapeUnknown as exc:
            facts["contract_step_failed"] = exc.step
    live_zepto.recorder.record_check("zepto", "payment_methods_shape", **facts)
    assert isinstance(payload, dict)
