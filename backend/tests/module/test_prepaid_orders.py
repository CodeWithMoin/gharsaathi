"""Prepaid Zepto orders end to end with fakes: card → tap → payment states.

Every method goes through the same bound order card, tap, approval and one-shot
checkout claim as cash on delivery. The Zepto client is a scripted fake; no
provider, payment or OpenAI call is possible here.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from app.api.orders import ConfirmationRequest, PaymentMethodRequest, PaymentStatusRequest
from app.commerce.swiggy.models import ProductSearchResult
from app.commerce.zepto.payments import ORDER_TOOLS
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
    CheckoutStatus,
    ProductRef,
    Quote,
)
from app.shopping.order_confirmation import OrderConfirmations
from app.voice.order_tools import ORDER_TOOL_DEFINITIONS
from app.voice.toolbox import VoiceToolbox
from tests.payment_fakes import (
    FEE,
    PAY_LINK,
    FakePreviewQuotes,
    ScriptedZeptoClient,
    methods_payload,
    payment_gateway,
    start_payload,
    status_payload,
)
from tests.shopping_fakes import Clock, InMemoryCheckoutAttempts

USER = "user_pay_a"
OTHER = "user_pay_b"
ZEPTO_ADDRESS = "zepto-home"
PAYABLE = 2 * 9850 + FEE  # ₹222


class CodQuotes:
    async def quote(self, basket: Basket) -> Quote:
        items = item_total_paise(basket)
        return Quote(
            provider=basket.provider,
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=items,
            charges=[Charge(label="Delivery fee", amount_paise=FEE)],
            payable_paise=items + FEE,
            quoted_at=datetime.now(UTC),
        )


class NoCod:
    def __init__(self) -> None:
        self.calls = 0

    async def confirm_cod(self, basket: Basket) -> object:
        self.calls += 1
        raise AssertionError("COD must not be called for a prepaid card")


class NoSearch:
    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=[])


class Addresses:
    async def describe(self, external_user_id: str, provider: str, address_id: str):  # type: ignore[no-untyped-def]
        from app.commerce.provider_state import SavedAddressSummary

        return SavedAddressSummary(label="Home", short_address="Green Park")


class World:
    def __init__(self, *, enabled: bool = True, listed: tuple[str, ...] = ("FAKE_ONLINE", "FAKE_UPI", "FAKE_WALLET")) -> None:
        self.clock = Clock()
        self.client = ScriptedZeptoClient()
        self.client.script("get_payment_methods", methods_payload(*listed))
        self.previews = FakePreviewQuotes()
        self.payments = payment_gateway(self.client, self.previews, enabled=enabled)
        self.basket_repo = InMemoryBasketRepository()
        self.approval_repo = InMemoryApprovalRepository()
        quotes = CodQuotes()
        self.baskets = BasketService(self.basket_repo, self.approval_repo, self.clock)
        self.approvals = ApprovalService(
            self.basket_repo, self.approval_repo, lambda _: quotes, self.clock
        )
        self.confirmations = OrderConfirmations(
            self.basket_repo, self.approvals, self.clock, payments=self.payments
        )
        self.cod = NoCod()
        self.checkout = CheckoutService(
            self.basket_repo,
            self.approval_repo,
            InMemoryCheckoutAttempts(self.basket_repo, self.approval_repo),
            quotes,
            self.cod,
            self.clock,
            payments=self.payments,
        )

    def toolbox(self, user: str = USER) -> VoiceToolbox:
        return VoiceToolbox(
            NoSearch(),
            self.baskets,
            self.approvals,
            "swiggy-home",
            NoSearch(),
            ZEPTO_ADDRESS,
            user,
            addresses=Addresses(),
            order_confirmations=self.confirmations,
        )

    async def fill(self, user: str = USER) -> Basket:
        return await self.baskets.add_line(
            user,
            "zepto",
            BasketLine(
                ref=ProductRef(provider="zepto", product_id="oil", variant_id="zepto-oil", sku_id="sku-oil"),
                name="Fortune Sunflower Oil",
                pack_size="1 L",
                unit_price_paise=9850,
                quantity=2,
            ),
        )

    def order_calls(self) -> list[tuple[str, dict]]:
        return [(name, args) for name, args in self.client.calls if name.startswith("create_")]

    def status_calls(self) -> list[dict]:
        return [args for name, args in self.client.calls if name == "check_payment_status"]


async def prepare(toolbox: VoiceToolbox, method: str | None) -> tuple[dict, dict]:
    outcome = await toolbox.execute(
        "prepare_order", json.dumps({"store": "zepto", "payment_method": method})
    )
    return outcome.model_output, outcome.ui_event or {}


async def tap(world: World, method: str, *, start: object) -> tuple[object, dict, VoiceToolbox]:
    await world.fill()
    toolbox = world.toolbox()
    output, event = await prepare(toolbox, method)
    assert output["status"] == "awaiting_tap", output
    world.client.script(ORDER_TOOLS[method], start)
    result = await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    return result, event, toolbox


async def basket_status(world: World, event: dict) -> BasketStatus:
    basket = await world.basket_repo.get(event["basket"]["id"])
    assert basket is not None
    return basket.status


# ---- availability and binding ----------------------------------------------


async def test_flag_off_offers_only_cash_on_delivery_without_asking_zepto() -> None:
    world = World(enabled=False)
    await world.fill()
    toolbox = world.toolbox()

    output, _ = await prepare(toolbox, "online")
    assert output["status"] == "payment_method_unavailable"
    assert output["payment_methods"] == ["Cash on delivery"]
    output, event = await prepare(toolbox, None)
    assert output["status"] == "awaiting_tap" and output["payment"] == "cash_on_delivery"
    assert event["confirmation"]["payment_methods"] == [{"id": "cod", "label": "Cash on delivery"}]
    assert world.client.calls == []


async def test_only_methods_zepto_lists_are_offered_or_accepted() -> None:
    world = World(listed=("FAKE_ONLINE",))
    await world.fill()
    toolbox = world.toolbox()

    refused, _ = await prepare(toolbox, "wallet")
    assert refused["status"] == "payment_method_unavailable" and refused["requested"] == "Zepto Cash"
    output, event = await prepare(toolbox, "online")
    assert output["status"] == "awaiting_tap" and output["payment"] == "online"
    assert output["payment_method_label"] == "Pay online"
    assert "UPI PIN" in output["note"] and "Never ask" in output["note"]
    card = event["confirmation"]
    assert card["payment_method"] == "online"
    assert [item["id"] for item in card["payment_methods"]] == ["cod", "online"]
    # A card bound to a method Zepto does not list cannot be made by any path.
    with pytest.raises(ApprovalRefused, match="not available"):
        await world.confirmations.change_payment_method(USER, card["id"], "upi_reserve")
    assert world.order_calls() == []


async def test_changing_the_method_replaces_the_card() -> None:
    world = World()
    await world.fill()
    toolbox = world.toolbox()
    _, event = await prepare(toolbox, None)
    old_id = event["confirmation"]["id"]

    fresh = await world.confirmations.change_payment_method(USER, old_id, "online")
    assert fresh.id != old_id and fresh.payment_method == "online"
    assert fresh.payable_paise == PAYABLE
    with pytest.raises(ApprovalRefused, match="newer order card"):
        await world.confirmations.place(USER, old_id, world.checkout)
    with pytest.raises(NotFound):
        await world.confirmations.change_payment_method(OTHER, fresh.id, "cod")

    world.client.script("create_online_payment_order", start_payload(PAYABLE))
    result = await world.confirmations.place(USER, fresh.id, world.checkout)
    assert result.status == "awaiting_payment" and result.payment_method == "online"
    assert world.cod.calls == 0


async def test_turning_the_flag_off_after_the_card_places_nothing() -> None:
    world = World()
    await world.fill()
    _, event = await prepare(world.toolbox(), "online")
    world.payments.enabled = False

    with pytest.raises(ApprovalRefused, match="not available"):
        await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert world.order_calls() == [] and world.cod.calls == 0


# ---- online: each outcome -------------------------------------------------


async def test_online_success_needs_payment_and_order_confirmation() -> None:
    world = World()
    result, event, toolbox = await tap(world, "online", start=start_payload(PAYABLE))

    assert result.status == "awaiting_payment" and result.payment_url == PAY_LINK
    assert result.provider_order_id == "ZP-1" and result.checkout_id
    # The fresh bill came from this method's own unconfirmed preview, then one confirmed call.
    assert world.previews.previews == [("create_online_payment_order", "FAKE_ONLINE")]
    assert world.order_calls() == [
        ("create_online_payment_order", {"confirmOrder": True, "userAddressId": ZEPTO_ADDRESS})
    ]
    assert await basket_status(world, event) == BasketStatus.CHECKOUT_PENDING
    note = toolbox.order.take_updates()[-1]
    assert "waiting for payment" in note and "Nothing is paid" in note and "PIN" in note

    # Order OK but payment still pending: not placed.
    world.client.script(
        "check_payment_status",
        status_payload("FAKE_WAIT", "FAKE_PLACED"),
        status_payload("FAKE_OK", "FAKE_PLACED"),
    )
    pending = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert pending.status == "awaiting_payment" and pending.payment_url == PAY_LINK
    # A second tap within the interval reuses the answer: no provider call.
    again = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert again.status == "awaiting_payment" and len(world.status_calls()) == 1

    world.clock.advance(seconds=6)
    placed = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert placed.status == "confirmed" and placed.provider_order_id == "ZP-1"
    assert placed.payment_url is None
    assert await basket_status(world, event) == BasketStatus.ORDERED
    approvals = await world.approval_repo.for_basket(event["basket"]["id"])
    assert [a.status for a in approvals] == [ApprovalStatus.CONSUMED]
    assert world.status_calls() == [
        {"orderId": "ZP-1", "poll": False},
        {"orderId": "ZP-1", "poll": False},
    ]
    assert "paid by Pay online" in toolbox.order.take_updates()[-1]

    # Terminal: no further status calls, and the order call was made exactly once.
    world.clock.advance(seconds=60)
    await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert len(world.status_calls()) == 2 and len(world.order_calls()) == 1


async def test_payment_failed_is_terminal_and_never_retried() -> None:
    world = World()
    result, event, toolbox = await tap(world, "online", start=start_payload(PAYABLE))
    world.client.script("check_payment_status", status_payload("FAKE_FAIL"))

    failed = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert failed.status == "payment_failed" and failed.payment_url is None
    assert "did not go through" in toolbox.order.take_updates()[-1]
    world.clock.advance(minutes=1)
    assert (await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)).status == "payment_failed"
    assert len(world.status_calls()) == 1 and len(world.order_calls()) == 1
    assert await basket_status(world, event) == BasketStatus.CHECKOUT_PENDING


async def test_paid_but_order_failed_is_explicit() -> None:
    world = World()
    result, _, toolbox = await tap(world, "online", start=start_payload(PAYABLE))
    world.client.script("check_payment_status", status_payload("FAKE_OK", "FAKE_ORDER_FAIL"))

    outcome = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert outcome.status == "paid_order_failed"
    note = toolbox.order.take_updates()[-1]
    assert "not to pay again" in note and "Zepto support" in note and "refund" in note
    assert len(world.order_calls()) == 1


async def test_payment_succeeded_without_order_confirmation_is_not_placed() -> None:
    world = World()
    result, event, _ = await tap(world, "online", start=start_payload(PAYABLE))
    world.client.script("check_payment_status", status_payload("FAKE_OK"))

    outcome = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert outcome.status == "payment_succeeded"
    assert await basket_status(world, event) == BasketStatus.CHECKOUT_PENDING


async def test_unrecognised_status_is_payment_unknown_and_can_be_checked_again() -> None:
    world = World()
    result, _, toolbox = await tap(world, "online", start=start_payload(PAYABLE))
    world.client.script(
        "check_payment_status",
        {"content_text": "Payment processing"},
        status_payload("FAKE_OK", "FAKE_PLACED"),
    )

    unknown = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert unknown.status == "payment_unknown" and unknown.payment_url is None
    assert "did not clearly confirm" in toolbox.order.take_updates()[-1]
    world.clock.advance(seconds=6)
    placed = await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
    assert placed.status == "confirmed"


async def test_poll_true_only_after_the_provider_explicitly_asks() -> None:
    world = World()
    result, _, _ = await tap(world, "online", start=start_payload(PAYABLE))
    world.client.script(
        "check_payment_status",
        status_payload("FAKE_WAIT", poll_next=True),
        status_payload("FAKE_WAIT"),
        status_payload("FAKE_WAIT"),
    )
    for _ in range(3):
        await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)
        world.clock.advance(seconds=6)
    assert [call["poll"] for call in world.status_calls()] == [False, True, False]


@pytest.mark.parametrize(
    "start",
    [
        {"content_text": "Order created"},
        start_payload(PAYABLE + 100),  # amount the user did not approve
        start_payload(PAYABLE, link="http://insecure.test/pay"),
        TimeoutError("dropped after send"),
    ],
)
async def test_unknown_start_is_outcome_unknown_and_never_retried(start: object) -> None:
    world = World()
    result, event, toolbox = await tap(world, "online", start=start)

    assert result.status == "outcome_unknown" and result.payment_url is None
    assert "do not prepare it again" in toolbox.order.take_updates()[-1]
    # No order id: nothing to check, and no second order call by any path.
    assert (await world.confirmations.check_payment(USER, result.checkout_id, world.checkout)).status == "outcome_unknown"
    with pytest.raises(ApprovalRefused, match="already used"):
        await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    attempt = await world.checkout.checkout(
        (await world.approval_repo.for_basket(event["basket"]["id"]))[0].id,
        USER,
        f"order-confirmation:{event['confirmation']['id']}",
        payment_method="online",
    )
    assert attempt.status == CheckoutStatus.OUTCOME_UNKNOWN
    assert len(world.order_calls()) == 1 and world.status_calls() == []


async def test_price_change_for_the_method_places_nothing() -> None:
    world = World()
    await world.fill()
    _, event = await prepare(world.toolbox(), "online")
    world.previews.extra = 500

    with pytest.raises(ApprovalRefused, match="payable amount changed"):
        await world.confirmations.place(USER, event["confirmation"]["id"], world.checkout)
    assert world.order_calls() == []


# ---- wallet and UPI ---------------------------------------------------------


async def test_wallet_placed_at_once_when_zepto_confirms_it() -> None:
    world = World()
    result, event, _ = await tap(
        world, "wallet", start=start_payload(PAYABLE, link=None, state="FAKE_PAID")
    )
    assert result.status == "confirmed" and result.provider_order_id == "ZP-1"
    assert await basket_status(world, event) == BasketStatus.ORDERED
    assert world.order_calls()[0][0] == "create_wallet_order"


async def test_upi_reserve_waits_for_the_users_upi_app_without_a_link() -> None:
    world = World()
    result, _, _ = await tap(world, "upi_reserve", start=start_payload(PAYABLE, link=None))
    assert result.status == "awaiting_payment" and result.payment_url is None
    assert world.order_calls()[0][0] == "create_upi_reserve_pay_order"


async def test_status_checks_are_owner_only() -> None:
    world = World()
    result, _, _ = await tap(world, "online", start=start_payload(PAYABLE))
    with pytest.raises(NotFound):
        await world.confirmations.check_payment(OTHER, result.checkout_id, world.checkout)
    assert world.status_calls() == []


# ---- no secrets anywhere ----------------------------------------------------

_SECRET_FIELD = re.compile(r"pin|card|cvv|otp|password|vpa", re.IGNORECASE)


def test_no_pin_card_or_otp_fields_in_any_payment_input() -> None:
    schema = ORDER_TOOL_DEFINITIONS[0]["parameters"]
    assert set(schema["properties"]) == {"store", "payment_method"}
    for model in (ConfirmationRequest, PaymentMethodRequest, PaymentStatusRequest):
        assert not [name for name in model.model_fields if _SECRET_FIELD.search(name)]
        assert model.model_config.get("extra") == "forbid"
    assert schema["properties"]["payment_method"]["enum"] == ["cod", "upi_reserve", "wallet", "online", None]
