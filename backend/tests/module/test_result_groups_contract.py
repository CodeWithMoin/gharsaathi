"""Backend contracts for per-item result groups: tool events and the saved chat schema."""

import json

import pytest
from pydantic import ValidationError

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.conversations.schemas import ConversationStateUpsert
from app.voice.tools import MultiProviderShoppingToolExecutor, ProviderSearchTarget, ShoppingToolExecutor


class Catalogue:
    def __init__(self, names: dict[str, list[str]]) -> None:
        self.names = names

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(
            products=[
                Product(product_id=name, sku_id=name, spin_id=name, name=name, price=Price(mrp=50, offer_price=45), available=True)
                for name in self.names.get(query.casefold(), [])
            ]
        )


CATALOGUE = Catalogue({"atta": ["Daily Good Sharbati Atta", "Aashirvaad Atta"], "dahi": ["Desi Farms Malai Dahi"]})


@pytest.mark.asyncio
async def test_each_voice_search_event_names_its_item() -> None:
    executor = MultiProviderShoppingToolExecutor(
        [
            ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", CATALOGUE, "a1", True),
            ProviderSearchTarget("zepto", "Zepto", CATALOGUE, "z1", True),
        ]
    )
    atta, dahi = [
        await executor.execute("search_products", json.dumps({"query": query}))
        for query in ("Atta", "dahi")
    ]
    assert atta.ui_event is not None and dahi.ui_event is not None
    assert (atta.ui_event["type"], atta.ui_event["item_key"], atta.ui_event["item_label"]) == ("tool.results", "atta", "Atta")
    assert (dahi.ui_event["item_key"], len(dahi.ui_event["products"])) == ("dahi", 2)
    assert atta.model_output["item_key"] == "atta" and atta.model_output["item_label"] == "Atta"


@pytest.mark.asyncio
async def test_failed_and_unconnected_searches_carry_the_item_key_too() -> None:
    none_ready = MultiProviderShoppingToolExecutor([ProviderSearchTarget("zepto", "Zepto", CATALOGUE, None)])
    outcome = await none_ready.execute("search_products", json.dumps({"query": "Amul  Dahi"}))
    assert outcome.ui_event == {"type": "tool.not_connected", "query": "Amul Dahi", "item_key": "amul dahi", "item_label": "Amul Dahi"}

    single = await ShoppingToolExecutor(CATALOGUE, "a1").execute("search_products", json.dumps({"query": "atta"}))
    assert single.ui_event is not None and single.ui_event["item_key"] == "atta"


def group(key: str, seq: int) -> dict[str, object]:
    return {
        "key": key,
        "query": key.title(),
        "seq": seq,
        "after_message_id": "m1",
        "products": [{"product_id": "p", "sku_id": "s", "name": "Atta", "price": {"mrp": 1, "offer_price": 1}, "available": True}],
    }


def test_result_groups_round_trip_and_old_chats_still_load() -> None:
    saved = ConversationStateUpsert.model_validate({"result_groups": [group("atta", 1), group("dahi", 2)]})
    assert [item.key for item in saved.result_groups] == ["atta", "dahi"]
    assert ConversationStateUpsert.model_validate(saved.model_dump(mode="json")) == saved
    old = ConversationStateUpsert.model_validate({"submitted_query": "atta", "products": [], "searched": True})
    assert old.result_groups == []


def test_result_groups_reject_duplicate_keys_and_too_many_groups() -> None:
    with pytest.raises(ValidationError):
        ConversationStateUpsert.model_validate({"result_groups": [group("atta", 1), group("atta", 2)]})
    with pytest.raises(ValidationError):
        ConversationStateUpsert.model_validate({"result_groups": [group(f"item {i}", i) for i in range(13)]})
