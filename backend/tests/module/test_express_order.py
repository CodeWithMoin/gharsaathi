"""express_order / quick_add: one call from a named request to the order card (or the basket).

Fakes only: in-memory baskets and approvals, a recording Zepto gateway that the
tests never reach (no tap), fake catalogues and memory. No provider or OpenAI call.
"""

from __future__ import annotations

import json

import pytest

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.zepto.errors import ZeptoAddressInvalid, ZeptoProviderError
from app.memory.recall import HouseholdMemory, HouseholdRecall, RecallConsent, RememberedItem
from app.shopping.errors import ProviderUnavailable
from app.shopping.models import ApprovalStatus, BasketStatus
from app.voice.express_tools import matches_query, pick_option
from app.voice.toolbox import VoiceToolbox
from tests.module.test_order_confirmation import SWIGGY_ADDRESS, USER, ZEPTO_ADDRESS, Addresses, World


def product(pid: str, name: str, brand: str, size: str, price: int, available: bool = True) -> Product:
    return Product(
        product_id=pid, sku_id=f"sku-{pid}", spin_id=f"spin-{pid}", name=name, brand=brand,
        pack_size=size, price=Price(mrp=price, offer_price=price), available=available,
    )


LASSI = product("lassi", "Amul Sweet Lassi", "Amul", "200 ml", 20)
MASTI = product("masti", "Amul Masti Spiced Buttermilk", "Amul", "200 ml", 14)
MD_LASSI = product("mdlassi", "Mother Dairy Meetha Lassi", "Mother Dairy", "180 ml", 23)
MATIC = product("matic", "Surf Excel Matic Detergent", "Surf Excel", "2 kg", 565)
EASY = product("easy", "Surf Excel Easy Wash Detergent", "Surf Excel", "1 kg", 145)
TAAZA = product("taaza", "Amul Taaza Toned Milk", "Amul", "1 L", 54)
MD_MILK = product("mdmilk", "Mother Dairy Toned Milk", "Mother Dairy", "1 L", 55)
TEA = product("tea", "Tata Tea Gold", "Tata", "500 g", 285)
CATALOGUE = [LASSI, MASTI, MD_LASSI, MATIC, EASY, TAAZA, MD_MILK, TEA]


class Catalogue:
    """Any query word in the name/brand matches, like the demo store."""

    def __init__(self, products: list[Product], error: Exception | None = None) -> None:
        self.products = products
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        words = {word for word in query.lower().split() if len(word) > 2}
        return ProductSearchResult(
            products=[p for p in self.products if words & set(f"{p.brand} {p.name}".lower().split())]
        )


class Memory:
    def __init__(self, *items: dict) -> None:
        self.memory = HouseholdMemory(
            consent=RecallConsent(saved_preferences="on", purchase_history="on"),
            items=[RememberedItem.model_validate(item) for item in items],
        )

    async def household_memory(self, household_id: str) -> HouseholdMemory:
        return self.memory

    async def recall(self, household_id: str, item: str) -> HouseholdRecall:
        return HouseholdRecall(consent=self.memory.consent, candidates=[])


USUAL_TAAZA = {"source": "purchase_history", "name": "Amul Taaza Toned Milk 1 L", "store_id": "zepto", "times": 6, "last_bought": "2026-11-18"}


def toolbox(
    world: World,
    zepto: Catalogue | None = None,
    swiggy: Catalogue | None = None,
    memory: Memory | None = None,
    zepto_address: str | None = ZEPTO_ADDRESS,
) -> VoiceToolbox:
    return VoiceToolbox(
        swiggy or Catalogue([]),
        world.baskets,
        world.approvals,
        SWIGGY_ADDRESS,
        zepto or Catalogue(CATALOGUE),
        zepto_address,
        USER,
        addresses=Addresses(),
        order_confirmations=world.confirmations,
        preference_recall=memory,
    )


async def express(box: VoiceToolbox, *items: tuple[str, int], store: str | None = None):  # type: ignore[no-untyped-def]
    return await box.execute(
        "express_order",
        json.dumps(
            {"items": [{"query": q, "quantity": n} for q, n in items], "store": store, "payment_method": None}
        ),
    )


async def zepto_basket(world: World):  # type: ignore[no-untyped-def]
    return await world.baskets.active(USER, "zepto")


@pytest.fixture
def world() -> World:
    return World()


async def test_ready_adds_bills_and_shows_the_card_without_ordering(world: World) -> None:
    box = toolbox(world)

    outcome = await express(box, ("Amul lassi", 2))

    output = outcome.model_output
    assert output["status"] == "ready"
    assert output["added"][0]["name"] == "Amul Sweet Lassi" and output["added"][0]["quantity"] == 2
    assert output["added"][0]["picked_because"] == "named_brand_and_size"
    assert output["payable"] == "₹65"  # ₹40 items + ₹25 fee, from the quote
    assert output["delivery_address"]["label"] == "Home"
    assert "Place order" in output["note"] and output["also_in_basket"] == []
    card = outcome.ui_event
    assert card is not None and card["type"] == "order.confirmation_requested"
    assert card["confirmation"]["items"] == [{"name": "Amul Sweet Lassi", "pack_size": "200 ml", "quantity": 2}]
    assert [event["type"] for event in outcome.extra_ui_events] == ["basket.updated"]
    # Never ordered or approved: only the user's tap does that.
    assert world.gateway.calls == []
    approvals = await world.approval_repo.for_basket(card["basket"]["id"])
    assert [approval.status for approval in approvals] == [ApprovalStatus.PENDING]
    # One search per item, only on the orderable store.
    assert box.search.targets[0].searcher.queries == []  # type: ignore[attr-defined]


async def test_the_card_from_express_is_placed_only_by_the_tap(world: World) -> None:
    box = toolbox(world)
    outcome = await express(box, ("Amul lassi", 1))
    assert world.gateway.calls == []

    result = await world.confirmations.place(USER, outcome.ui_event["confirmation"]["id"], world.checkout)  # type: ignore[index]
    assert result.status == "confirmed" and len(world.gateway.calls) == 1


async def test_two_items_in_one_call(world: World) -> None:
    outcome = await express(toolbox(world), ("Amul Taaza milk 1 litre", 1), ("Tata Tea Gold 500 g", 2))

    assert outcome.model_output["status"] == "ready"
    names = {item["name"]: item["quantity"] for item in outcome.ui_event["confirmation"]["items"]}  # type: ignore[index]
    assert names == {"Amul Taaza Toned Milk": 1, "Tata Tea Gold": 2}


async def test_usual_milk_picks_the_remembered_product(world: World) -> None:
    outcome = await express(toolbox(world, memory=Memory(USUAL_TAAZA)), ("milk", 1))

    assert outcome.model_output["status"] == "ready"
    assert outcome.model_output["added"][0]["name"] == "Amul Taaza Toned Milk"
    assert outcome.model_output["added"][0]["picked_because"] == "remembered"


async def test_ambiguous_item_needs_one_choice_and_adds_nothing_for_it(world: World) -> None:
    box = toolbox(world)

    outcome = await express(box, ("Surf Excel detergent", 1))

    output = outcome.model_output
    assert output["status"] == "needs_choice" and output["added"] == []
    assert output["choice"]["reason"] == "several_matches"
    assert {option["name"] for option in output["choice"]["options"]} == {MATIC.name, EASY.name}
    assert all(option["option_id"] in box.options for option in output["choice"]["options"])
    assert "ONE short question" in output["note"]
    assert outcome.ui_event is not None and outcome.ui_event["type"] == "tool.results"
    assert output["search"]["status"] == "ok"  # typed chat keeps it addable
    assert (await zepto_basket(world)).lines == []
    assert world.gateway.calls == []

    picked = await express(box, ("Surf Excel Matic 2 kg", 1))
    assert picked.model_output["status"] == "ready"
    assert picked.model_output["added"][0]["name"] == MATIC.name


async def test_confident_items_are_added_while_one_needs_a_choice_and_no_card_is_shown(world: World) -> None:
    outcome = await express(toolbox(world), ("Amul lassi", 1), ("Surf Excel detergent", 1))

    output = outcome.model_output
    assert output["status"] == "needs_choice"
    assert [item["name"] for item in output["added"]] == ["Amul Sweet Lassi"]
    assert output["choice"]["item"] == "Surf Excel detergent"
    events = [outcome.ui_event, *outcome.extra_ui_events]
    assert "order.confirmation_requested" not in {event["type"] for event in events if event}
    basket = await zepto_basket(world)
    assert [line.name for line in basket.lines] == ["Amul Sweet Lassi"]
    assert await world.approval_repo.for_basket(basket.id) == []


async def test_brand_alone_gets_the_one_product_question(world: World) -> None:
    outcome = await express(toolbox(world), ("Surf Excel", 1))

    assert outcome.model_output["status"] == "needs_choice"
    assert outcome.model_output["choice"]["reason"] == "needs_product"
    assert "kaunsa product" in outcome.model_output["choice"]["message"]


async def test_brand_not_named_is_confirmed_not_guessed(world: World) -> None:
    outcome = await express(toolbox(world, zepto=Catalogue([MD_LASSI])), ("lassi", 1))

    assert outcome.model_output["status"] == "needs_choice"
    assert outcome.model_output["choice"]["reason"] == "confirm_single"


async def test_out_of_stock_match_is_never_added(world: World) -> None:
    gone = product("lassi", "Amul Sweet Lassi", "Amul", "200 ml", 20, available=False)
    outcome = await express(toolbox(world, zepto=Catalogue([gone, MASTI])), ("Amul lassi", 1))

    assert outcome.model_output["status"] == "needs_choice"
    assert outcome.model_output["choice"]["reason"] == "not_available"
    assert (await zepto_basket(world)).lines == []


async def test_avoided_brand_is_never_picked(world: World) -> None:
    avoid = {"source": "confirmed_preference", "name": "Amul", "kind": "avoid_brand", "brand": "Amul"}
    box = toolbox(world, memory=Memory(avoid))

    named = await express(box, ("Amul lassi", 1))
    assert named.model_output["status"] == "needs_choice"
    assert named.model_output["choice"]["reason"] == "avoided"

    generic = await express(box, ("lassi", 1))
    names = {option["name"] for option in generic.model_output["choice"]["options"]}
    assert "Amul Sweet Lassi" not in names
    assert (await zepto_basket(world)).lines == []


async def test_avoided_store_fails_without_searching_it(world: World) -> None:
    rule = {"source": "confirmed_preference", "name": "Never Zepto", "kind": "avoid_store", "store_id": "zepto"}
    zepto = Catalogue(CATALOGUE)
    outcome = await express(toolbox(world, zepto=zepto, memory=Memory(rule)), ("Amul lassi", 1))

    assert outcome.model_output["status"] == "failed"
    assert outcome.model_output["reason"] == "skipped_by_household_rule"
    assert zepto.queries == [] and (await zepto_basket(world)).lines == []


async def test_merges_into_the_open_basket_and_names_what_else_is_in_it(world: World) -> None:
    await world.fill()  # Fortune oil × 2 already in the Zepto basket
    box = toolbox(world)

    outcome = await express(box, ("Amul lassi", 2))
    assert outcome.model_output["also_in_basket"] == [
        {"name": "Fortune Sunflower Oil", "pack_size": "1 L", "quantity": 2}
    ]
    items = {item["name"]: item["quantity"] for item in outcome.ui_event["confirmation"]["items"]}  # type: ignore[index]
    assert items == {"Fortune Sunflower Oil": 2, "Amul Sweet Lassi": 2}

    # Asking again sets the quantity the user said; it does not stack.
    again = await express(box, ("Amul lassi", 2))
    assert {item["name"]: item["quantity"] for item in again.ui_event["confirmation"]["items"]}["Amul Sweet Lassi"] == 2  # type: ignore[index]


@pytest.mark.parametrize("quantity", [0, 51])
async def test_quantity_limits(world: World, quantity: int) -> None:
    outcome = await express(toolbox(world), ("Amul lassi", quantity))
    assert outcome.model_output["status"] == "failed" and outcome.model_output["reason"] == "invalid_arguments"
    assert (await zepto_basket(world)).lines == []


async def test_too_many_items_are_refused(world: World) -> None:
    outcome = await express(toolbox(world), *[("Amul lassi", 1)] * 6)
    assert outcome.model_output["reason"] == "invalid_arguments"


async def test_no_address_fails_before_any_search(world: World) -> None:
    zepto = Catalogue(CATALOGUE)
    outcome = await express(toolbox(world, zepto=zepto, zepto_address=None), ("Amul lassi", 1))
    assert outcome.model_output["status"] == "failed" and outcome.model_output["reason"] == "no_address"
    assert zepto.queries == []


async def test_address_invalid_is_a_failure_not_not_found(world: World) -> None:
    outcome = await express(toolbox(world, zepto=Catalogue([], ZeptoAddressInvalid("gone"))), ("Amul lassi", 1))

    output = outcome.model_output
    assert output["status"] == "failed" and output["reason"] == "address_invalid"
    assert "not found" not in output["message"].lower() or "never say" in output["message"].lower()
    assert outcome.ui_event is not None and outcome.ui_event.get("address_removed") == "zepto"
    assert (await zepto_basket(world)).lines == []


async def test_provider_error_adds_nothing(world: World) -> None:
    outcome = await express(toolbox(world, zepto=Catalogue([], ZeptoProviderError("down"))), ("Amul lassi", 1), ("Tata Tea Gold", 1))

    assert outcome.model_output["status"] == "failed" and outcome.model_output["reason"] == "provider_error"
    assert outcome.ui_event is not None and outcome.ui_event["type"] == "tool.error"
    assert (await zepto_basket(world)).lines == []
    assert world.gateway.calls == []


async def test_bill_failure_keeps_the_items_and_shows_no_card(world: World) -> None:
    async def broken(basket):  # type: ignore[no-untyped-def]
        raise ProviderUnavailable("The store could not price this basket right now.")

    world.quotes.quote = broken  # type: ignore[method-assign]
    outcome = await express(toolbox(world), ("Amul lassi", 1))

    output = outcome.model_output
    assert output["status"] == "failed" and [item["name"] for item in output["added"]] == ["Amul Sweet Lassi"]
    assert "order card could not be prepared" in output["message"]
    assert outcome.ui_event is None or outcome.ui_event["type"] != "order.confirmation_requested"
    assert world.gateway.calls == []


async def test_instamart_orders_are_unsupported(world: World) -> None:
    outcome = await express(toolbox(world), ("Amul lassi", 1), store="swiggy_instamart")
    assert outcome.model_output["status"] == "failed" and outcome.model_output["reason"] == "unsupported_store"


async def test_locked_basket_is_reported_and_nothing_is_ordered(world: World) -> None:
    box = toolbox(world)
    first = await express(box, ("Amul lassi", 1))
    world.gateway.fail = True
    result = await world.confirmations.place(USER, first.ui_event["confirmation"]["id"], world.checkout)  # type: ignore[index]
    assert result.status == "outcome_unknown"

    outcome = await express(box, ("Tata Tea Gold", 1))
    assert outcome.model_output["status"] == "failed" and outcome.model_output["reason"] == "basket_refused"
    assert len(world.gateway.calls) == 1  # only the tap's own call; never retried


# ------------------------------------------------------------ quick_add


async def quick(box: VoiceToolbox, *items: tuple[str, int], store: str | None = None):  # type: ignore[no-untyped-def]
    return await box.execute(
        "quick_add", json.dumps({"items": [{"query": q, "quantity": n} for q, n in items], "store": store})
    )


async def test_quick_add_picks_the_usual_without_asking_and_never_bills(world: World) -> None:
    outcome = await quick(toolbox(world, memory=Memory(USUAL_TAAZA)), ("milk", 1))

    output = outcome.model_output
    assert output["status"] == "added"
    assert output["added"][0]["name"] == "Amul Taaza Toned Milk" and output["added"][0]["line_total"] == "₹54"
    assert "change karna ho to bolo" in output["note"] and "view_basket" in output["note"]
    assert output["baskets"][0]["store_id"] == "zepto"
    basket = await zepto_basket(world)
    assert basket.status == BasketStatus.DRAFT and await world.approval_repo.for_basket(basket.id) == []


async def test_quick_add_picks_the_cheaper_store_for_the_same_product(world: World) -> None:
    swiggy_taaza = product("taaza", "Amul Taaza Toned Milk", "Amul", "1 L", 50)
    outcome = await quick(
        toolbox(world, swiggy=Catalogue([swiggy_taaza]), memory=Memory(USUAL_TAAZA)), ("milk", 1)
    )
    assert outcome.model_output["added"][0]["store_id"] == "swiggy_instamart"


async def test_quick_add_asks_when_nothing_is_remembered_and_several_match(world: World) -> None:
    outcome = await quick(toolbox(world), ("milk", 1))
    assert outcome.model_output["status"] == "needs_choice"
    assert (await zepto_basket(world)).lines == []


# ------------------------------------------------------------ the pick rule


def test_match_needs_every_product_word_and_the_size() -> None:
    assert matches_query("Amul lassi", {"brand": "Amul", "name": "Amul Sweet Lassi", "pack_size": "200 ml"})
    assert not matches_query("Amul lassi", {"brand": "Amul", "name": "Amul Masti Buttermilk", "pack_size": "200 ml"})
    assert matches_query("doodh", {"brand": "Amul", "name": "Amul Taaza Toned Milk", "pack_size": "1 L"})
    assert not matches_query("Amul milk 500 ml", {"brand": "Amul", "name": "Amul Taaza Toned Milk", "pack_size": "1 L"})
    assert not matches_query("usual order", {"brand": "Amul", "name": "Amul Taaza", "pack_size": "1 L"})


def _result(option_id: str, name: str, brand: str, size: str, price: int, **extra: object) -> dict:
    return {"option_id": option_id, "name": name, "brand": brand, "pack_size": size, "price_paise": price, "available": True, "store_id": "zepto", **extra}


def test_pick_rules() -> None:
    a = _result("o1", "Amul Taaza Toned Milk", "Amul", "1 L", 5400)
    b = _result("o2", "Mother Dairy Toned Milk", "Mother Dairy", "1 L", 5500)
    assert pick_option("milk", [a, b]) == (None, "several_matches", "")
    assert pick_option("Amul milk", [a, b]) == ("o1", "", "named_brand_and_size")
    remembered = {**a, "remembered": {"source": "purchase_history", "times": 3}}
    assert pick_option("milk", [remembered, b]) == ("o1", "", "remembered")
    # Two different remembered products with the same weight: ask.
    other = {**b, "remembered": {"source": "purchase_history", "times": 3}}
    assert pick_option("milk", [remembered, other])[1] == "several_matches"
    # A confirmed preference outranks a purchase.
    preferred = {**b, "remembered": {"source": "confirmed_preference"}}
    assert pick_option("milk", [remembered, preferred])[0] == "o2"
    # Same product on two stores: the cheaper one.
    swiggy = {**a, "option_id": "o3", "store_id": "swiggy_instamart", "price_paise": 5000}
    assert pick_option("Amul milk", [a, swiggy])[0] == "o3"


async def test_voice_sideband_publishes_every_event_of_an_express_order(world: World) -> None:
    from app.voice.sideband import SidebandRunner

    class Connection:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, message: str) -> None:
            self.sent.append(message)

        def __aiter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

    published: list[dict] = []
    connection = Connection()
    runner = SidebandRunner(connection, toolbox(world), published.append)  # type: ignore[arg-type]
    arguments = json.dumps({"items": [{"query": "Amul lassi", "quantity": 1}], "store": None, "payment_method": None})

    await runner._run_call("call_1", "express_order", arguments)

    assert [event["type"] for event in published] == [
        "tool.started", "basket.updated", "order.confirmation_requested",
    ]
    assert json.loads(json.loads(connection.sent[0])["item"]["output"])["status"] == "ready"
    assert world.gateway.calls == []
