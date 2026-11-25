import pytest

from app.commerce.swiggy.adapter import SwiggyInstamartAdapter, parse_addresses, parse_products
from app.commerce.swiggy.errors import SwiggySchemaError


ADDRESS_RESULT = {
    "success": True,
    "data": {
        "addresses": [
            {
                "id": "addr_home",
                "addressLine": "12 Test Road, Bengaluru",
                "phoneNumber": "redacted",
                "addressCategory": "HOME",
                "addressTag": "Home",
            }
        ],
        "pagination": {
            "page": 1,
            "pageSize": 10,
            "total": 1,
            "totalPages": 1,
            "hasMore": False,
        },
        "resolution": {
            "defaultAddressId": "addr_home",
            "resolutionSource": "ranked_saved_address",
            "needsUserClarification": False,
            "candidateAddressIds": ["addr_home"],
        },
    },
}

PRODUCT_RESULT = {
    "success": True,
    "data": {
        "nextOffset": "20",
        "products": [
            {
                "displayName": "Fortune Sunlite Oil",
                "brand": "Fortune",
                "inStock": True,
                "isAvail": True,
                "productId": "product_1",
                "parentProductId": "parent_1",
                "variations": [
                    {
                        "spinId": "spin_1",
                        "skuId": "sku_1",
                        "quantityDescription": "1 L",
                        "displayName": "Fortune Sunlite Refined Sunflower Oil",
                        "brandName": "Fortune",
                        "price": {
                            "mrp": 180,
                            "offerPrice": 154,
                            "unitLevelPrice": "₹154/L",
                        },
                        "isInStockAndAvailable": True,
                        "imageUrl": "https://example.test/oil.png",
                        "rating": {"value": "4.6", "count": "1.2K"},
                        "sla": {"value": "12", "unit": "min"},
                    }
                ],
            }
        ],
    },
}


def test_parse_addresses_omits_phone_number() -> None:
    result = parse_addresses(ADDRESS_RESULT)
    assert result.addresses[0].id == "addr_home"
    assert result.addresses[0].address_line == "12 Test Road, Bengaluru"
    assert "phone" not in result.addresses[0].model_dump()
    assert result.resolution.default_address_id == "addr_home"


def test_parse_products_flattens_variations_without_fabricating_fields() -> None:
    result = parse_products(PRODUCT_RESULT)
    product = result.products[0]
    assert product.product_id == "product_1"
    assert product.sku_id == "sku_1"
    assert product.pack_size == "1 L"
    assert product.price.offer_price == 154
    assert product.available is True
    assert result.next_offset == "20"


def test_parse_products_rejects_missing_provider_identifier() -> None:
    invalid = {"data": {"products": [{"variations": []}]}}
    with pytest.raises(SwiggySchemaError, match="productId"):
        parse_products(invalid)


def test_one_malformed_product_does_not_hide_the_other_results() -> None:
    good = PRODUCT_RESULT["data"]["products"][0]
    mixed = {"data": {**PRODUCT_RESULT["data"], "products": [{"variations": []}, good]}}
    result = parse_products(mixed)
    assert [product.product_id for product in result.products] == ["product_1"]


@pytest.mark.asyncio
async def test_adapter_calls_only_documented_read_tools() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def call_tool(self, name: str, arguments: dict[str, object]):
            self.calls.append((name, arguments))
            return ADDRESS_RESULT if name == "get_addresses" else PRODUCT_RESULT

    client = FakeClient()
    adapter = SwiggyInstamartAdapter(client)  # type: ignore[arg-type]
    await adapter.addresses(1, 10)
    await adapter.search_products("Fortune oil", "addr_home", 0)
    assert client.calls == [
        ("get_addresses", {"page": 1, "pageSize": 10}),
        (
            "search_products",
            {"query": "Fortune oil", "addressId": "addr_home", "offset": 0},
        ),
    ]

