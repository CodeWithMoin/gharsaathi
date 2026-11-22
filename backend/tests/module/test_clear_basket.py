"""clear_basket and BasketService.clear: one revision-checked edit, never while an order is unconfirmed.

Fakes only (in-memory baskets/approvals, recording gateway). No provider or OpenAI call.
"""

from __future__ import annotations

import json

import pytest

from app.shopping.errors import ApprovalRefused, InvalidBasketChange
from app.shopping.models import ApprovalStatus
from app.voice.toolbox import VoiceToolbox
from tests.module.test_order_confirmation import USER, World, prepare


@pytest.fixture
def world() -> World:
    return World()


async def clear(box: VoiceToolbox, store: str | None = None):  # type: ignore[no-untyped-def]
    return await box.execute("clear_basket", json.dumps({"store": store}))


async def test_service_clear_is_one_revision_and_a_no_op_when_empty(world: World) -> None:
    filled = await world.fill()
    emptied = await world.baskets.clear(USER, "zepto")
    assert emptied.lines == [] and emptied.revision == filled.revision + 1 and emptied.id == filled.id
    again = await world.baskets.clear(USER, "zepto")
    assert again.revision == emptied.revision


async def test_empty_basket_is_already_empty(world: World) -> None:
    outcome = await clear(world.toolbox())
    assert outcome.model_output["status"] == "already_empty"
    assert outcome.model_output["cleared"] == [] and outcome.ui_event is None


async def test_null_clears_every_store_with_items_in_one_call(world: World) -> None:
    await world.fill("zepto")
    await world.fill("swiggy_instamart")

    outcome = await clear(world.toolbox())

    output = outcome.model_output
    assert output["status"] == "cleared"
    assert {entry["store_id"]: entry["items_removed"] for entry in output["cleared"]} == {
        "zepto": 2, "swiggy_instamart": 2,
    }
    assert "view_basket" in output["note"]
    events = [*outcome.extra_ui_events, outcome.ui_event]
    assert sorted(event["provider"] for event in events) == ["swiggy_instamart", "zepto"]
    assert all(event["type"] == "basket.updated" and event["basket"]["item_count"] == 0 for event in events)
    for provider in ("zepto", "swiggy_instamart"):
        assert (await world.baskets.active(USER, provider)).lines == []  # type: ignore[arg-type]


async def test_a_named_store_clears_only_that_store(world: World) -> None:
    await world.fill("zepto")
    await world.fill("swiggy_instamart")

    outcome = await clear(world.toolbox(), "zepto")

    assert [entry["store_id"] for entry in outcome.model_output["cleared"]] == ["zepto"]
    assert (await world.baskets.active(USER, "swiggy_instamart")).lines != []
    assert (await clear(world.toolbox(), "all")).model_output["cleared"][0]["store_id"] == "swiggy_instamart"


async def test_clearing_cancels_the_bill_and_the_order_card(world: World) -> None:
    await world.fill()
    box = world.toolbox()
    _, card = await prepare(world, box)

    outcome = await clear(box)

    assert outcome.model_output["cleared"][0]["bill_cancelled"] is True
    approvals = await world.approval_repo.for_basket(card["basket"]["id"])
    assert [approval.status for approval in approvals] == [ApprovalStatus.SUPERSEDED]
    assert outcome.ui_event["basket"]["revision"] > card["basket"]["revision"]  # type: ignore[index]
    with pytest.raises(ApprovalRefused):
        await world.confirmations.place(USER, card["confirmation"]["id"], world.checkout)
    assert world.gateway.calls == []


async def test_unknown_order_outcome_locks_the_basket_and_says_why(world: World) -> None:
    await world.fill()
    box = world.toolbox()
    _, card = await prepare(world, box)
    world.gateway.fail = True
    result = await world.confirmations.place(USER, card["confirmation"]["id"], world.checkout)
    assert result.status == "outcome_unknown"

    outcome = await clear(box)

    output = outcome.model_output
    assert output["status"] == "locked" and output["cleared"] == []
    assert "outcome not known" in output["locked"][0]["message"]
    assert outcome.ui_event is None
    basket = await world.basket_repo.get(card["basket"]["id"])
    assert basket is not None and len(basket.lines) == 1
    with pytest.raises(InvalidBasketChange):
        await world.baskets.clear(USER, "zepto")
    assert len(world.gateway.calls) == 1  # never retried


async def test_change_results_say_not_to_view_the_basket_again(world: World) -> None:
    await world.fill()
    box = world.toolbox()
    updated = await box.execute(
        "set_basket_quantity", json.dumps({"item_id": "zepto-oil", "quantity": 1, "store": "zepto"})
    )
    assert updated.model_output["status"] == "updated"
    assert "do not call view_basket" in updated.model_output["note"]
    assert updated.model_output["items"][0]["quantity"] == 1
    viewed = await box.execute("view_basket", json.dumps({"store": "zepto"}))
    assert "do not call view_basket" not in viewed.model_output["note"]


async def test_typed_clear_cart_is_tool_then_reply_with_every_store_event() -> None:
    from app.commerce.swiggy.models import ProductSearchResult
    from tests.module.test_chat_turn import FakeResponses, call, service
    from tests.shopping_fakes import World as ChatWorld

    class Empty:
        async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
            return ProductSearchResult(products=[])

    world = ChatWorld()
    for provider in ("zepto", "swiggy_instamart"):
        from app.shopping.models import BasketLine, ProductRef

        await world.baskets.add_line(
            "user_chat_a",
            provider,  # type: ignore[arg-type]
            BasketLine(
                ref=ProductRef(provider=provider, product_id="tea", variant_id=f"{provider}-tea"),  # type: ignore[arg-type]
                name="Tata Tea Gold", pack_size="500 g", unit_price_paise=28500, quantity=1,
            ),
        )
    responses = FakeResponses([[call("clear_basket", "c1", store=None)], "Dono basket khali kar diye."])
    chat = service(responses, world, Empty(), Empty())  # type: ignore[arg-type]

    result = await chat.run_turn("user_chat_a", None, "clear cart", "addr", "addr")

    assert len(responses.requests) == 2  # one tool round, then the reply
    assert result.reply == "Dono basket khali kar diye."
    assert sorted(event["provider"] for event in result.ui_events if event["type"] == "basket.updated") == [
        "swiggy_instamart", "zepto",
    ]
