from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.commerce.zepto.adapter import parse_addresses, parse_products
from app.api import zepto as zepto_api
from app.api.zepto import AddAddressRequest, OfferSuggestionRequest
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.zepto.service import ZeptoService
from app.shopping.models import Basket


def test_zepto_adapter_normalizes_saved_addresses_and_paise_prices() -> None:
    addresses = parse_addresses(
        {
            "addresses": [
                {
                    "id": "address-1",
                    "label": "Home",
                    "addressLine": "Test address",
                    "latitude": 12.9,
                    "longitude": 77.6,
                }
            ]
        }
    )
    products = parse_products(
        {
            "products": [
                {
                    "id": "product-1",
                    "productVariantId": "variant-1",
                    "storeProductId": "store-product-1",
                    "name": "Amul Milk",
                    "price": 6800,
                    "mrp": 7200,
                    "packSize": "1 L",
                    "availableQuantity": 4,
                    "imageUrl": "https://example.test/milk.png",
                }
            ]
        }
    )

    assert addresses.addresses[0].id == "address-1"
    assert addresses.addresses[0].tag == "Home"
    assert products.products[0].spin_id == "variant-1"
    assert products.products[0].sku_id == "store-product-1"
    assert products.products[0].price.offer_price == 68
    assert products.products[0].price.mrp == 72


class FakeOAuth:
    async def status(self):
        raise AssertionError("not used")


class FakeClient:
    def __init__(self) -> None:
        self.session_generation = 1
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def prepare_session(self) -> int:
        return self.session_generation

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        self.calls.append((name, arguments))
        if name == "search_products":
            return {
                "products": [
                    {
                        "id": "product-1",
                        "productVariantId": "variant-1",
                        "storeProductId": "store-product-1",
                        "name": "Milk",
                        "price": 6800,
                        "mrp": 7200,
                        "availableQuantity": 2,
                    }
                ]
            }
        if name == "search_multiple_products":
            return {
                "groups": [
                    {
                        "products": [
                            {
                                "id": "product-2",
                                "productVariantId": "variant-2",
                                "storeProductId": "store-product-2",
                                "name": "Bread",
                                "price": 4500,
                                "availableQuantity": 3,
                            }
                        ]
                    }
                ]
            }
        return {"ok": True}


@pytest.mark.asyncio
async def test_zepto_search_prepares_context_once_and_caches_duplicate_query() -> None:
    client = FakeClient()
    service = ZeptoService(FakeOAuth(), client)  # type: ignore[arg-type]

    first = await service.search("milk", "address-1")
    second = await service.search("milk", "address-1")

    assert first == second
    assert [name for name, _ in client.calls] == [
        "select_saved_address",
        "search_products",
    ]


@pytest.mark.asyncio
async def test_zepto_multi_item_query_uses_one_parallel_search_tool() -> None:
    client = FakeClient()
    service = ZeptoService(FakeOAuth(), client)  # type: ignore[arg-type]

    result = await service.search("milk, bread aur eggs", "address-1")

    assert result.products[0].name == "Bread"
    assert client.calls[-1] == (
        "search_multiple_products",
        {"queries": ["milk", "bread", "eggs"], "pageNumber": 0},
    )


class AddressClient:
    session_generation = 1

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        self.calls.append((name, arguments, retry_on_auth))
        if name == "list_saved_addresses":
            return {
                "addresses": [
                    {
                        "id": "new-address",
                        "label": "Home",
                        "addressLine": "4B, Lake View, Bengaluru",
                    }
                ]
            }
        return {"success": True}


@pytest.mark.asyncio
async def test_add_address_is_exact_non_replayed_then_refreshes_provider_truth() -> None:
    client = AddressClient()
    service = ZeptoService(FakeOAuth(), client)  # type: ignore[arg-type]
    body = AddAddressRequest(
        type="HOME",
        name=" Home ",
        flat_details="4B",
        building_name="Lake View",
        building_type="BUILDING_TYPE_SOCIETY",
        floor=" 4 ",
        landmark=" ",
        latitude=12.971599,
        longitude=77.594566,
        formatted_address="4B, Lake View, Bengaluru, Karnataka 560001",
        short_address="Bengaluru, Karnataka",
        contact_name="Resident",
        contact_number="+91 99999 99999",
    )

    result = await service.add_address(body.provider_arguments())

    assert result.addresses[0].id == "new-address"
    assert client.calls == [
        (
            "add_saved_address",
            {
                "type": "HOME",
                "name": "Home",
                "flatDetails": "4B",
                "buildingName": "Lake View",
                "buildingType": "BUILDING_TYPE_SOCIETY",
                "floor": "4",
                "latitude": 12.971599,
                "longitude": 77.594566,
                "formattedAddress": "4B, Lake View, Bengaluru, Karnataka 560001",
                "shortAddress": "Bengaluru, Karnataka",
                "contactName": "Resident",
                "contactNumber": "+91 99999 99999",
            },
            False,
        ),
        ("list_saved_addresses", {}, True),
    ]


@pytest.mark.asyncio
async def test_offer_suggestion_requires_tapped_history_and_exact_live_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_id = UUID("00000000-0000-0000-0000-000000000042")
    remembered = SimpleNamespace(
        product_id=memory_id,
        product_name="Dark chocolate 50 g",
        provider="zepto",
        provider_item_id="chocolate-product",
        last_ordered_at=datetime.now(UTC) - timedelta(days=7),
    )

    class Graph:
        def __init__(self, session: object) -> None:
            del session

        async def suggestions(self, actor: object, limit: int):
            del actor, limit
            return [remembered]

    class Products:
        async def search(self, query: str, address_id: str, page_number: int):
            assert (query, address_id, page_number) == (
                "Dark chocolate 50 g",
                "home-address",
                0,
            )
            return ProductSearchResult(
                products=[
                    Product(
                        product_id="chocolate-product",
                        sku_id="store-chocolate",
                        spin_id="variant-chocolate",
                        name="Dark chocolate",
                        pack_size="50 g",
                        price=Price(mrp=80, offer_price=72),
                        available=True,
                    )
                ]
            )

    class Baskets:
        async def active(self, household_id: str, provider: str):
            assert (household_id, provider) == ("person-1", "zepto")
            now = datetime.now(UTC)
            return Basket(
                id="basket-1",
                household_id=household_id,
                provider="zepto",
                created_at=now,
                updated_at=now,
            )

    monkeypatch.setattr(zepto_api, "PurchaseGraphService", Graph)
    result = await zepto_api.offer_suggestion(
        OfferSuggestionRequest(
            memory_product_id=memory_id,
            address_id="home-address",
            offer_id="free_delivery",
            offer_label="Free delivery above ₹99",
            minimum_order_paise=9_900,
            remaining_paise=7_200,
            confirmed_benefit_paise=3_000,
        ),
        Products(),  # type: ignore[arg-type]
        Baskets(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        "person-1",
    )

    assert result is not None
    assert result.line.ref.product_id == "chocolate-product"
    assert result.candidate_total_paise == 7_200
    assert result.confirmed_benefit_paise == 3_000
    assert result.net_incremental_cost_paise == 4_200
    assert result.requires_explicit_add is True
    assert result.requires_fresh_quote is True
