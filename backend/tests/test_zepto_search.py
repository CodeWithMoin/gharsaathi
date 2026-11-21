from typing import Any

import pytest

from app.commerce.zepto.adapter import parse_addresses, parse_products
from app.commerce.zepto.service import ZeptoService


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
