import json
import logging
from typing import Any

import pytest

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.zepto.errors import (
    ZeptoProviderError,
    ZeptoRateLimited,
)
from app.commerce.zepto.service import RECOVERABLE_READ_TOOLS, ZeptoService
from app.voice.tools import MultiProviderShoppingToolExecutor, ProviderSearchTarget


MILK = {
    "products": [
        {
            "id": "product-1",
            "productVariantId": "variant-1",
            "storeProductId": "store-product-1",
            "name": "Amul Lassi",
            "price": 2500,
            "availableQuantity": 3,
        }
    ]
}


class FakeOAuth:
    pass


class FlakyClient:
    """A persistent-session fake: scripted failures per tool until the session is reset."""

    def __init__(self, failures: dict[str, list[Exception]] | None = None) -> None:
        self.session_generation = 1
        self.failures = failures or {}
        self.calls: list[str] = []
        self.resets = 0
        self.addresses = ["address-1"]

    async def prepare_session(self) -> int:
        return self.session_generation

    async def reset_session(self) -> None:
        self.resets += 1
        self.session_generation += 1

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, retry_on_auth: bool = True
    ) -> dict[str, Any]:
        self.calls.append(name)
        queue = self.failures.get(name)
        if queue:
            raise queue.pop(0)
        if name == "select_saved_address" and arguments["addressId"] not in self.addresses:
            raise ZeptoProviderError("Zepto could not complete the request.")
        if name in {"search_products", "search_multiple_products"}:
            return MILK
        if name == "list_saved_addresses":
            return {"addresses": [{"id": item, "addressLine": "Somewhere"} for item in self.addresses]}
        return {"success": True}


def service(client: FlakyClient) -> ZeptoService:
    return ZeptoService(FakeOAuth(), client)  # type: ignore[arg-type]


async def test_stale_session_is_reset_and_the_search_retried_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FlakyClient({"search_products": [ZeptoProviderError("stale session")]})
    zepto = service(client)

    with caplog.at_level(logging.WARNING):
        result = await zepto.search("lassi", "address-1")

    assert [product.name for product in result.products] == ["Amul Lassi"]
    assert client.resets == 1
    # The address is selected again on the fresh session before the retry.
    assert client.calls == [
        "select_saved_address",
        "search_products",
        "select_saved_address",
        "search_products",
    ]
    record = next(r.getMessage() for r in caplog.records if "zepto_session_reset" in r.getMessage())
    assert "reason=unavailable tool=search_products correlation_id=" in record
    assert "stale session" not in caplog.text


async def test_a_second_failure_is_raised_and_never_cached() -> None:
    error = ZeptoProviderError("down")
    client = FlakyClient({"search_products": [error, ZeptoProviderError("still down")]})
    zepto = service(client)

    with pytest.raises(ZeptoProviderError):
        await zepto.search("lassi", "address-1")
    assert client.resets == 1

    # The failure was not cached: the next search reaches Zepto and succeeds.
    result = await zepto.search("lassi", "address-1")
    assert result.products
    assert client.calls.count("search_products") == 3


async def test_rate_limits_are_not_retried() -> None:
    client = FlakyClient({"search_products": [ZeptoRateLimited("limited")]})
    with pytest.raises(ZeptoRateLimited):
        await service(client).search("lassi", "address-1")
    assert client.resets == 0
    assert client.calls.count("search_products") == 1


async def test_add_address_mutation_is_never_retried() -> None:
    client = FlakyClient({"add_saved_address": [ZeptoProviderError("unclear")]})
    with pytest.raises(ZeptoProviderError):
        await service(client).add_address({"type": "HOME"})
    assert client.resets == 0
    assert client.calls == ["add_saved_address"]


def test_mutating_and_order_tools_are_not_recoverable() -> None:
    for name in ("update_cart", "create_order", "create_cod_order", "check_payment_status", "add_saved_address"):
        assert name not in RECOVERABLE_READ_TOOLS
    assert {"get_order_detail", "list_order_history", "view_cart"} <= RECOVERABLE_READ_TOOLS


async def test_read_tools_recover_after_one_reset() -> None:
    client = FlakyClient({"list_order_history": [ZeptoProviderError("stale")]})
    zepto = service(client)
    with pytest.raises(Exception):
        # parse_order_history rejects the fake's payload; the retry still happened.
        await zepto.order_history()
    assert client.resets == 1
    assert client.calls == ["list_order_history", "list_order_history"]


class FailingSearcher:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        raise self.error


class OkSearcher:
    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(
            products=[
                Product(
                    product_id="p1",
                    sku_id="s1",
                    spin_id="v1",
                    name="Amul Lassi",
                    price=Price(mrp=25, offer_price=25),
                    available=True,
                )
            ]
        )


async def test_provider_error_is_a_distinct_status_not_empty_results() -> None:
    executor = MultiProviderShoppingToolExecutor(
        [ProviderSearchTarget("zepto", "Zepto", FailingSearcher(ZeptoProviderError("x")), "address-1")]
    )
    outcome = await executor.execute("search_products", json.dumps({"query": "lassi"}))

    assert outcome.model_output["status"] == "provider_error"
    assert "results" not in outcome.model_output
    assert outcome.model_output["unreachable_stores"] == [
        {"store": "Zepto", "store_id": "zepto", "reason": "unavailable"}
    ]
    assert "never say the item was not found" in outcome.model_output["message"]
    assert outcome.ui_event is not None
    assert outcome.ui_event["type"] == "tool.error"
    assert outcome.ui_event["message"] == "Zepto isn't responding—try again."


async def test_partial_failure_keeps_results_and_names_the_unreachable_store() -> None:
    executor = MultiProviderShoppingToolExecutor(
        [
            ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", OkSearcher(), "swiggy-home", True),
            ProviderSearchTarget("zepto", "Zepto", FailingSearcher(ZeptoRateLimited("x")), "address-1"),
        ]
    )
    outcome = await executor.execute("search_products", json.dumps({"query": "lassi"}))

    assert outcome.model_output["status"] == "ok"
    assert outcome.model_output["unreachable_stores"][0]["reason"] == "rate_limited"
    assert "not a search result" in outcome.model_output["note"]
    assert outcome.ui_event is not None
    assert outcome.ui_event["type"] == "tool.results"
    assert outcome.ui_event["notice"] == "Zepto isn't responding—try again."
