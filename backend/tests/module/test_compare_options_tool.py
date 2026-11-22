"""compare_options through VoiceToolbox-style wiring with fake stores (no provider calls)."""

import json

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.voice.compare_tools import CompareToolExecutor
from app.voice.tools import MultiProviderShoppingToolExecutor, ProviderSearchTarget


def p(pid: str, name: str, pack: str, price: float, available: bool = True) -> Product:
    return Product(product_id=pid, sku_id=f"s{pid}", spin_id=f"sp{pid}", name=name, brand="Amul", pack_size=pack,
                   price=Price(mrp=price, offer_price=price), available=available)


class Searcher:
    def __init__(self, products: list[Product]) -> None:
        self.products = products

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=self.products)


async def setup(swiggy: list[Product], zepto: list[Product]):
    search = MultiProviderShoppingToolExecutor([
        ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", Searcher(swiggy), "a", True),
        ProviderSearchTarget("zepto", "Zepto", Searcher(zepto), "a", True),
    ])
    await search.execute("search_products", json.dumps({"query": "Amul lassi"}))
    return search, CompareToolExecutor(search.options, search.shown)


def args(**kw):
    return json.dumps({"option_ids": None, "item": None, "positions": None, "aspect": "all", **kw})


async def test_two_options_cross_store_same_product() -> None:
    _, tool = await setup([p("1", "Amul Sweet Lassi", "200 ml", 25)], [p("1", "Amul Sweet Lassi", "200 ml", 20)])
    out = await tool.execute("compare_options", args(positions=[1, 2], aspect="store"))
    assert out.model_output["status"] == "ok"
    pair = out.model_output["pairs"][0]
    assert pair["same_product_different_store"] and pair["price_line"] == "Zepto is ₹5 cheaper"
    assert out.ui_event["type"] == "comparison.ready" and out.ui_event["headline"] == "Zepto is ₹5 cheaper"
    zepto = next(c for c in out.ui_event["options"] if c["product"]["provider"] == "zepto")
    assert "Cheapest" in zepto["badges"] and zepto["product"]["spin_id"] == "sp1"


async def test_four_options_by_item_and_option_ids() -> None:
    search, tool = await setup(
        [p("a", "Amul Sweet Lassi", "200 ml", 25), p("b", "Amul Sweet Lassi", "1 L", 90)],
        [p("c", "Amul Rose Lassi", "200 ml", 20), p("d", "Amul Masti Lassi", "6 x 200 ml", 100, available=False)],
    )
    out = await tool.execute("compare_options", args(item="amul LASSI"))
    assert len(out.model_output["options"]) == 4 and len(out.model_output["pairs"]) == 6
    assert out.model_output["better_value"]["label"] == "Amul Sweet Lassi 1 L"
    ids = list(search.options)[:2]
    by_id = await tool.execute("compare_options", args(option_ids=ids, aspect="price"))
    assert [o["option_id"] for o in by_id.model_output["options"]] == ids


async def test_not_on_screen_and_bad_cards() -> None:
    _, tool = await setup([p("1", "Amul Sweet Lassi", "200 ml", 25)], [p("2", "Amul Rose Lassi", "200 ml", 20)])
    assert (await tool.execute("compare_options", args(option_ids=["opt8", "opt9"]))).model_output["status"] == "options_not_on_screen"
    assert (await tool.execute("compare_options", args(item="atta"))).model_output["status"] == "options_not_on_screen"
    assert (await tool.execute("compare_options", args(positions=[1, 5]))).model_output["status"] == "no_such_card"
    assert (await tool.execute("compare_options", args(positions=[1, 1]))).model_output["status"] == "error"
    stale = CompareToolExecutor(tool.options, tool.shown, lambda option_id: True)
    assert (await stale.execute("compare_options", args())).model_output["status"] == "options_not_on_screen"
