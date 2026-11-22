"""One-tap order card: prepare_order (voice/typed) → user's tap → exactly one COD order.

Fakes only: in-memory baskets/approvals/attempts and a recording Zepto gateway.
No provider or OpenAI call is possible here.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService, item_total_paise
from app.shopping.checkout import CheckoutService
from app.shopping.errors import ApprovalRefused, NotFound
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.models import (
    ApprovalStatus,
    Basket,
    BasketLine,
    BasketStatus,
    Charge,
    ProductRef,
    Quote,
)
from app.shopping.order_confirmation import OrderConfirmations, OrderResult
from app.voice.sideband import SidebandRunner
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import Clock, InMemoryCheckoutAttempts

USER = "user_order_a"
OTHER = "user_order_b"
ZEPTO_ADDRESS = "zepto-home"
SWIGGY_ADDRESS = "swiggy-home"
FEE = 2500


class ZeptoQuotes:
    def __init__(self) -> None:
        self.extra = 0
        self.calls = 0

    async def quote(self, basket: Basket) -> Quote:
        self.calls += 1
        items = item_total_paise(basket)
        return Quote(
            provider=basket.provider,
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=items,
            charges=[Charge(label="Delivery fee", amount_paise=FEE)],
            payable_paise=items + FEE + self.extra,
            quoted_at=datetime.now(UTC),
        )


@dataclass
class Placed:
    order_id: str
    payable_paise: int
    status: str = "placed"


class RecordingGateway:
    """Stands in for ZeptoCodCheckoutGateway; records every would-be order."""

    def __init__(self, quotes: ZeptoQuotes) -> None:
        self.quotes = quotes
        self.calls: list[str] = []
        self.fail = False
        self.delay = 0.0

    async def confirm_cod(self, basket: Basket) -> object:
        self.calls.append(basket.id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise TimeoutError("provider timed out")
        quote = await ZeptoQuotes().quote(basket)
        return Placed(order_id=f"ZEP-{len(self.calls)}", payable_paise=quote.payable_paise)


class NoSearch:
    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=[])


class Addresses:
    async def describe(self, external_user_id: str, provider: str, address_id: str):  # type: ignore[no-untyped-def]
        from app.commerce.provider_state import SavedAddressSummary

        return SavedAddressSummary(label="Home", short_address="Green Park")


class World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.quotes = ZeptoQuotes()
        self.basket_repo = InMemoryBasketRepository()
        self.approval_repo = InMemoryApprovalRepository()
        self.baskets = BasketService(self.basket_repo, self.approval_repo, self.clock)
        self.approvals = ApprovalService(
            self.basket_repo, self.approval_repo, lambda _: self.quotes, self.clock
        )
        self.confirmations = OrderConfirmations(self.basket_repo, self.approvals, self.clock)
        self.gateway = RecordingGateway(self.quotes)
        self.checkout = CheckoutService(
            self.basket_repo,
            self.approval_repo,
            InMemoryCheckoutAttempts(self.basket_repo, self.approval_repo),
            self.quotes,
            self.gateway,
            self.clock,
        )

    def toolbox(self, user: str = USER) -> VoiceToolbox:
        return VoiceToolbox(
            NoSearch(),
            self.baskets,
            self.approvals,
            SWIGGY_ADDRESS,
            NoSearch(),
            ZEPTO_ADDRESS,
            user,
            addresses=Addresses(),
            order_confirmations=self.confirmations,
        )

    async def fill(self, provider: str = "zepto", user: str = USER, quantity: int = 2) -> Basket:
        return await self.baskets.add_line(
            user,
            provider,  # type: ignore[arg-type]
            BasketLine(
                ref=ProductRef(provider=provider, product_id="oil", variant_id=f"{provider}-oil"),  # type: ignore[arg-type]
                name="Fortune Sunflower Oil",
                pack_size="1 L",
                unit_price_paise=9850,
                quantity=quantity,
            ),
        )


async def prepare(world: World, toolbox: VoiceToolbox, store: str | None = "zepto") -> tuple[dict, dict]:
    outcome = await toolbox.execute("prepare_order", json.dumps({"store": store}))
    return outcome.model_output, outcome.ui_event or {}


@pytest.fixture
def world() -> World:
    return World()


async def test_prepare_shows_the_card_and_places_nothing(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()

    output, event = await prepare(world, toolbox)

    assert output["status"] == "awaiting_tap"
    assert output["payable"] == "₹222" and output["item_count"] == 2
    assert output["payment"] == "cash_on_delivery"
    assert output["delivery_address"] == {"address": "Green Park", "label": "Home"}
    assert output["items"] == [{"name": "Fortune Sunflower Oil", "pack_size": "1 L", "quantity": 2}]
    assert "confirmation_id" not in json.dumps(output) and "Place order" in output["note"]
    card = event["confirmation"]
    assert event["type"] == "order.confirmation_requested" and event["provider"] == "zepto"
    assert card["payable_paise"] == 22200 and card["delivery_address"]["label"] == "Home"
    assert "request" in event  # a fresh bill was requested for the card
    # No side effect beyond the bill and the card: nothing approved, nothing ordered.
    assert world.gateway.calls == []
    approvals = await world.approval_repo.for_basket(event["basket"]["id"])
    assert [approval.status for approval in approvals] == [ApprovalStatus.PENDING]
    basket = await world.basket_repo.get(event["basket"]["id"])
    assert basket is not None and basket.status == BasketStatus.AWAITING_APPROVAL


async def test_the_tap_places_exactly_one_order_and_tells_the_assistant(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()
    _, event = await prepare(world, toolbox)
    confirmation_id = event["confirmation"]["id"]

    result = await world.confirmations.place(USER, confirmation_id, world.checkout)

    assert result.status == "confirmed" and result.provider_order_id == "ZEP-1"
    assert result.payable_paise == 22200
    assert len(world.gateway.calls) == 1
    basket = await world.basket_repo.get(event["basket"]["id"])
    assert basket is not None and basket.status == BasketStatus.ORDERED
    updates = toolbox.order.take_updates()
    assert len(updates) == 1 and "confirmed the order" in updates[0] and "ZEP-1" in updates[0]
    assert toolbox.order.take_updates() == []

    with pytest.raises(ApprovalRefused, match="already used"):
        await world.confirmations.place(USER, confirmation_id, world.checkout)
    assert len(world.gateway.calls) == 1


async def test_an_already_approved_bill_is_reused_without_requoting(world: World) -> None:
    basket = await world.fill()
    basket = await world.baskets.set_address(USER, "zepto", ZEPTO_ADDRESS)
    requested = await world.approvals.request(basket.id)
    await world.approvals.approve(requested.approval.id, requested.approval.fingerprint, USER)
    quotes = world.quotes.calls

    output, event = await prepare(world, world.toolbox())
    assert output["status"] == "awaiting_tap" and "request" not in event
    assert world.quotes.calls == quotes

    result = await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert result.status == "confirmed" and len(world.gateway.calls) == 1


async def test_expired_card_places_nothing(world: World) -> None:
    await world.fill()
    _, event = await prepare(world, world.toolbox())
    world.clock.advance(minutes=5, seconds=1)

    with pytest.raises(ApprovalRefused, match="expired"):
        await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert world.gateway.calls == []


async def test_cancel_invalidates_the_card(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()
    _, event = await prepare(world, toolbox)
    confirmation_id = event["confirmation"]["id"]

    await world.confirmations.cancel(USER, confirmation_id)
    with pytest.raises(ApprovalRefused, match="cancelled"):
        await world.confirmations.place(USER, confirmation_id, world.checkout)
    with pytest.raises(ApprovalRefused):
        await world.confirmations.cancel(USER, confirmation_id)
    assert world.gateway.calls == []
    assert toolbox.order.take_updates() == [
        "The user cancelled the order confirmation. Nothing was ordered."
    ]


async def test_basket_edit_after_the_card_places_nothing(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()
    _, event = await prepare(world, toolbox)
    await world.baskets.set_quantity(USER, "zepto", "zepto-oil", 3)

    with pytest.raises(ApprovalRefused, match="basket changed"):
        await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert world.gateway.calls == []
    assert "not placed" in toolbox.order.take_updates()[0]


async def test_a_new_bill_after_the_card_places_nothing(world: World) -> None:
    await world.fill()
    _, event = await prepare(world, world.toolbox())
    basket = await world.basket_repo.get(event["basket"]["id"])
    assert basket is not None
    await world.approvals.request(basket.id)  # supersedes the bill the card was bound to

    with pytest.raises(ApprovalRefused):
        await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert world.gateway.calls == []


async def test_store_price_change_at_checkout_places_nothing(world: World) -> None:
    await world.fill()
    _, event = await prepare(world, world.toolbox())
    world.quotes.extra = 1000

    with pytest.raises(ApprovalRefused, match="payable amount changed"):
        await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert world.gateway.calls == []


async def test_another_user_cannot_tap_or_cancel(world: World) -> None:
    await world.fill()
    _, event = await prepare(world, world.toolbox())
    confirmation_id = event["confirmation"]["id"]

    with pytest.raises(NotFound):
        await world.confirmations.place(OTHER, confirmation_id, world.checkout)
    with pytest.raises(NotFound):
        await world.confirmations.cancel(OTHER, confirmation_id)
    with pytest.raises(NotFound):
        await world.confirmations.place(USER, "not-a-real-confirmation", world.checkout)
    assert world.gateway.calls == []
    # The owner's card still works.
    result = await world.confirmations.place(USER, confirmation_id, world.checkout)
    assert result.status == "confirmed"


async def test_swiggy_order_is_unsupported_and_creates_no_card(world: World) -> None:
    basket = await world.fill("swiggy_instamart")
    output, event = await prepare(world, world.toolbox(), "swiggy_instamart")

    assert output["status"] == "unsupported" and event == {}
    assert world.confirmations._items == {}
    assert world.quotes.calls == 0
    basket = await world.baskets.set_address(USER, "swiggy_instamart", SWIGGY_ADDRESS)
    requested = await world.approvals.request(basket.id)
    with pytest.raises(ApprovalRefused, match="Only Zepto"):
        await world.confirmations.prepare(USER, requested.basket, requested.approval, store="Swiggy Instamart")


async def test_two_store_baskets_ask_which_store(world: World) -> None:
    await world.fill("zepto")
    await world.fill("swiggy_instamart")
    output, event = await prepare(world, world.toolbox(), None)
    assert output["status"] == "needs_store" and event == {}


async def test_a_newer_card_replaces_the_older_one(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()
    _, first = await prepare(world, toolbox)
    _, second = await prepare(world, toolbox)

    with pytest.raises(ApprovalRefused, match="newer order card"):
        await world.confirmations.place(USER, first["confirmation"]["id"], world.checkout)
    result = await world.confirmations.place(USER, second["confirmation"]["id"], world.checkout)
    assert result.status == "confirmed" and len(world.gateway.calls) == 1


async def test_uncertain_provider_outcome_is_reported_and_never_retried(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()
    _, event = await prepare(world, toolbox)
    world.gateway.fail = True

    result = await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert result.status == "outcome_unknown" and result.provider_order_id is None
    assert "did not confirm" in toolbox.order.take_updates()[0]
    # The frozen basket cannot get a second card.
    output, _ = await prepare(world, toolbox)
    assert output["status"] == "error"
    assert len(world.gateway.calls) == 1


async def test_two_taps_at_once_place_one_order(world: World) -> None:
    await world.fill()
    _, event = await prepare(world, world.toolbox())
    world.gateway.delay = 0.02
    confirmation_id = event["confirmation"]["id"]

    results = await asyncio.gather(
        world.confirmations.place(USER, confirmation_id, world.checkout),
        world.confirmations.place(USER, confirmation_id, world.checkout),
        return_exceptions=True,
    )
    assert len(world.gateway.calls) == 1
    assert sum(isinstance(item, OrderResult) for item in results) == 1
    assert sum(isinstance(item, ApprovalRefused) for item in results) == 1


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


async def test_voice_session_hears_the_tap_result_as_commentary(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()
    connection = FakeConnection()
    SidebandRunner(connection, toolbox, lambda event: None)
    _, event = await prepare(world, toolbox)

    await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)

    assert len(connection.sent) == 1
    sent = connection.sent[0]
    assert sent["type"] == "session.commentary.append" and sent["delegation_id"] is None
    assert "confirmed the order" in sent["content"] and "ZEP-1" in sent["content"]
    assert toolbox.order.take_updates() == []


async def test_a_closed_voice_session_does_not_fail_the_tap(world: World) -> None:
    await world.fill()
    toolbox = world.toolbox()

    async def broken(note: str) -> None:
        raise ConnectionError("session closed")

    toolbox.order.attach_sender(broken)
    _, event = await prepare(world, toolbox)
    result = await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert result.status == "confirmed"
