"""One option order for the model and the screen, so "doosra wala" is card 2 for both."""

import json

import pytest

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.conversations.schemas import ProductSnapshot
from app.memory.recall import HouseholdMemory, RecallConsent, RememberedItem
from app.voice.tools import (
    MAX_OPTIONS_PER_ITEM,
    MultiProviderShoppingToolExecutor,
    ProviderSearchTarget,
    RankedOption,
    ShoppingToolExecutor,
    order_options,
)


def product(name: str, *, brand: str = "Generic", pack: str = "1 kg", available: bool = True) -> Product:
    pid = name.lower().replace(" ", "-")
    return Product(
        product_id=pid,
        sku_id=f"sku-{pid}",
        spin_id=f"spin-{pid}",
        name=name,
        brand=brand,
        pack_size=pack,
        price=Price(mrp=60, offer_price=50),
        available=available,
    )


class Searcher:
    def __init__(self, products: list[Product]) -> None:
        self.products = products

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=self.products)


class Memory:
    def __init__(self, items: list[RememberedItem]) -> None:
        self.memory = HouseholdMemory(
            consent=RecallConsent(saved_preferences="on", purchase_history="on"), items=items
        )

    async def household_memory(self, household_id: str) -> HouseholdMemory:
        return self.memory


ATTA = [
    product("Daily Good Sharbati Atta", brand="Daily Good"),
    product("Pillsbury Chakki Atta", brand="Pillsbury"),
    product("Fortune Chakki Atta", brand="Fortune", available=False),
    product("Aashirvaad Shudh Chakki Atta", brand="Aashirvaad", pack="5 kg"),
    product("Nature Fresh Atta", brand="Nature Fresh"),
]


def bought(name: str, store: str = "swiggy_instamart") -> RememberedItem:
    return RememberedItem(source="purchase_history", name=name, store_id=store, times=3)


def rule(kind: str, **fields: str) -> RememberedItem:
    return RememberedItem(source="confirmed_preference", name=fields.get("brand") or fields.get("product") or kind, kind=kind, **fields)


async def search(executor: MultiProviderShoppingToolExecutor, query: str = "atta"):
    outcome = await executor.execute("search_products", json.dumps({"query": query}))
    assert outcome.model_output["status"] == "ok" and outcome.ui_event is not None
    return outcome.model_output["results"], outcome.ui_event["products"]


def one_store(products: list[Product], memory: Memory | None = None) -> MultiProviderShoppingToolExecutor:
    return MultiProviderShoppingToolExecutor(
        [ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", Searcher(products), "a1", True)],
        memory=memory,
        household_id="house" if memory else "",
    )


@pytest.mark.asyncio
async def test_remembered_then_preferred_then_provider_order_with_unavailable_last() -> None:
    memory = Memory([bought("Aashirvaad Shudh Chakki Atta 5 kg"), rule("prefer_brand", brand="Pillsbury", item="atta")])
    results, cards = await search(one_store(ATTA, memory))
    order = ["Aashirvaad Shudh Chakki Atta", "Pillsbury Chakki Atta", "Daily Good Sharbati Atta", "Nature Fresh Atta", "Fortune Chakki Atta"]
    assert [r["name"] for r in results] == order
    assert [c["name"] for c in cards] == order
    assert [r["position"] for r in results] == [c["position"] for c in cards] == [1, 2, 3, 4, 5]
    # Card 1 carries the annotations its badge comes from.
    assert cards[0]["remembered"] == "purchase_history" and cards[1]["preferred"] is True
    assert results[0]["remembered"]["source"] == "purchase_history"


@pytest.mark.asyncio
async def test_doosra_wala_option_id_is_the_product_on_card_two() -> None:
    executor = one_store(ATTA, Memory([bought("Nature Fresh Atta 1 kg")]))
    results, cards = await search(executor)
    second = next(r for r in results if r["position"] == 2)
    card = next(c for c in cards if c["position"] == 2)
    chosen = executor.options[second["option_id"]].product
    assert (chosen.sku_id, chosen.name) == (card["sku_id"], card["name"]) == ("sku-daily-good-sharbati-atta", "Daily Good Sharbati Atta")


@pytest.mark.asyncio
async def test_avoided_options_are_hidden_from_model_and_screen_even_when_named() -> None:
    memory = Memory([rule("avoid_brand", brand="Pillsbury")])
    results, cards = await search(one_store(ATTA, memory), "Pillsbury atta")
    assert "Pillsbury Chakki Atta" not in {r["name"] for r in results} | {c["name"] for c in cards}
    assert [c["position"] for c in cards] == [1, 2, 3, 4]
    outcome = await one_store(ATTA, memory).execute("search_products", json.dumps({"query": "Pillsbury atta"}))
    assert outcome.model_output["hidden_by_household_rules"] == 1
    assert not any("avoided" in item for item in outcome.ui_event["products"])  # type: ignore[index]


@pytest.mark.asyncio
async def test_two_stores_share_one_numbering_and_the_remembered_store_option_leads() -> None:
    swiggy = [product("Daily Good Sharbati Atta"), product("Pillsbury Chakki Atta")]
    zepto = [product("Aashirvaad Shudh Chakki Atta", brand="Aashirvaad", pack="5 kg"), product("Nature Fresh Atta")]
    executor = MultiProviderShoppingToolExecutor(
        [
            ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", Searcher(swiggy), "a1", True),
            ProviderSearchTarget("zepto", "Zepto", Searcher(zepto), "z1", True),
        ],
        memory=Memory([bought("Aashirvaad Shudh Chakki Atta 5 kg", "zepto")]),
        household_id="house",
    )
    results, cards = await search(executor)
    assert [(r["store_id"], r["name"], r["position"]) for r in results] == [
        (c["provider"], c["name"], c["position"]) for c in cards
    ]
    assert [r["name"] for r in results] == ["Aashirvaad Shudh Chakki Atta", "Daily Good Sharbati Atta", "Pillsbury Chakki Atta", "Nature Fresh Atta"]


def test_order_options_caps_and_drops_duplicates() -> None:
    many = [RankedOption("zepto", product(f"Item {index}")) for index in range(MAX_OPTIONS_PER_ITEM + 4)]
    assert len(order_options(many)) == MAX_OPTIONS_PER_ITEM == 8
    same = product("Same Atta")
    kept = order_options([RankedOption("zepto", same), RankedOption("zepto", same), RankedOption("swiggy_instamart", same)])
    assert [option.provider for option in kept] == ["zepto", "swiggy_instamart"]


@pytest.mark.asyncio
async def test_single_store_search_numbers_cards_and_puts_unavailable_last() -> None:
    executor = ShoppingToolExecutor(Searcher([product("Gone Oil", available=False), product("Sunlite Oil")]), "a1")
    outcome = await executor.execute("search_products", json.dumps({"query": "oil"}))
    assert [(r["name"], r["position"]) for r in outcome.model_output["results"]] == [("Sunlite Oil", 1), ("Gone Oil", 2)]
    assert [(p["name"], p["position"]) for p in outcome.ui_event["products"]] == [("Sunlite Oil", 1), ("Gone Oil", 2)]  # type: ignore[index]


def test_saved_chats_keep_positions_and_old_ones_still_load() -> None:
    base = {"product_id": "p", "sku_id": "s", "name": "Atta", "price": {"mrp": 1, "offer_price": 1}, "available": True}
    assert ProductSnapshot.model_validate({**base, "position": 2}).position == 2
    assert ProductSnapshot.model_validate(base).position is None
