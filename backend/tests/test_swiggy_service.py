import asyncio

import pytest

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.swiggy.service import SwiggyService


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def search_products(
        self, query: str, address_id: str, offset: int
    ) -> ProductSearchResult:
        self.calls.append((query, address_id, offset))
        await asyncio.sleep(0)
        return ProductSearchResult(
            products=[
                Product(
                    product_id="milk",
                    sku_id="milk-sku",
                    spin_id="milk-spin",
                    name="Amul Taaza Milk",
                    pack_size="1 L",
                    price=Price(mrp=72, offer_price=69),
                    available=True,
                )
            ]
        )


def service(adapter: RecordingAdapter) -> SwiggyService:
    return SwiggyService(object(), object(), adapter)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_identical_searches_share_short_lived_cache() -> None:
    adapter = RecordingAdapter()
    swiggy = service(adapter)

    first, second = await asyncio.gather(
        swiggy.search("  Amul   milk  ", "home", 0),
        swiggy.search("amul milk", "home", 0),
    )

    assert adapter.calls == [("Amul milk", "home", 0)]
    assert first == second
    assert first is not second


@pytest.mark.asyncio
async def test_cache_key_keeps_address_query_and_offset_distinct() -> None:
    adapter = RecordingAdapter()
    swiggy = service(adapter)

    await swiggy.search("milk", "home", 0)
    await swiggy.search("milk", "work", 0)
    await swiggy.search("milk", "home", 10)
    await swiggy.search("bread", "home", 0)

    assert adapter.calls == [
        ("milk", "home", 0),
        ("milk", "work", 0),
        ("milk", "home", 10),
        ("bread", "home", 0),
    ]
