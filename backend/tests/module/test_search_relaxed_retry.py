"""An empty store answer for a long catalogue title gets exactly one broader retry."""

import json

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.voice.tools import MultiProviderShoppingToolExecutor, ProviderSearchTarget


def product() -> Product:
    return Product(product_id="1", sku_id="s1", spin_id="sp1", name="Paper Boat Swing Coconut Water Drink",
                   brand="Paper Boat", pack_size="270 ml", price=Price(mrp=20, offer_price=20), available=True)


class OnlyBroadSearcher:
    """Matches only the short query, like the real store did."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        return ProductSearchResult(products=[product()] if query == "Paper Boat Swing" else [])


class NeverMatches:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        return ProductSearchResult(products=[])


async def run(searcher, query: str):
    tool = MultiProviderShoppingToolExecutor([ProviderSearchTarget("zepto", "Zepto", searcher, "a", True)])
    outcome = await tool.execute("search_products", json.dumps({"query": query}))
    return outcome.model_output


async def test_long_title_is_retried_once_with_a_broader_query() -> None:
    searcher = OnlyBroadSearcher()
    result = await run(searcher, "Paper Boat Swing Coconut Water Drink 270")
    assert searcher.queries == ["Paper Boat Swing Coconut Water Drink 270", "Paper Boat Swing"]
    assert result["status"] == "ok"


async def test_a_second_empty_answer_stops_after_one_retry() -> None:
    searcher = NeverMatches()
    await run(searcher, "Paper Boat Swing Coconut Water Drink 270")
    assert len(searcher.queries) == 2


async def test_short_queries_are_not_retried() -> None:
    searcher = NeverMatches()
    await run(searcher, "milk")
    assert searcher.queries == ["milk"]
