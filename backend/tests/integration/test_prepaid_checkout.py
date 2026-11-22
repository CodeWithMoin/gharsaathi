"""Prepaid Zepto orders on PostgreSQL: the tap, the one-shot claim and status checks.

The Zepto client is a scripted fake behind the real ``ZeptoPaymentGateway`` and
a fake response contract (tests/payment_fakes.py). Nothing here can pay for or
place a real order.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.commerce.demo import DEMO_ADDRESS_ID, DemoSwiggyService, DemoZeptoQuoteProvider, DemoZeptoService
from app.dependencies import (
    get_approval_repository,
    get_approval_service,
    get_basket_repository,
    get_basket_service,
    get_order_confirmations,
    get_zepto_checkout_service,
)
from app.shopping.checkout import CheckoutService, DatabaseCheckoutAttemptRepository
from app.shopping.errors import ApprovalRefused
from app.shopping.models import Basket, Quote
from app.shopping.order_confirmation import OrderConfirmations, OrderResult
from app.voice.toolbox import VoiceToolbox
from tests.integration.helpers import as_user
from tests.integration.test_shopping_checkout import ZEPTO, RecordingZeptoGateway, _ready_basket
from tests.payment_fakes import (
    PAY_LINK,
    ScriptedZeptoClient,
    methods_payload,
    payment_gateway,
    start_payload,
    status_payload,
)


class DemoPreviewQuotes:
    """The demo bill, reached through the prepaid preview path."""

    def __init__(self) -> None:
        self.previews: list[str] = []

    async def quote_unlocked(
        self, basket: Basket, *, preview_tool: str = "create_order", payment_method_value: str = "COD"
    ) -> Quote:
        self.previews.append(preview_tool)
        return await DemoZeptoQuoteProvider().quote(basket)


@pytest.fixture
def world(override):
    client = ScriptedZeptoClient()
    client.script("get_payment_methods", methods_payload("FAKE_ONLINE"))
    payments = payment_gateway(client, DemoPreviewQuotes())  # type: ignore[arg-type]
    from app.database.session import get_session_factory

    checkout = CheckoutService(
        get_basket_repository(),
        get_approval_repository(),
        DatabaseCheckoutAttemptRepository(get_session_factory()),
        DemoZeptoQuoteProvider(),
        RecordingZeptoGateway(),
        payments=payments,
    )
    confirmations = OrderConfirmations(get_basket_repository(), get_approval_service(), payments=payments)
    override(get_zepto_checkout_service, lambda: checkout)
    override(get_order_confirmations, lambda: confirmations)
    return client, confirmations


def _prepare(user_id: str, confirmations: OrderConfirmations, method: str | None) -> dict:
    toolbox = VoiceToolbox(
        DemoSwiggyService(),
        get_basket_service(),
        get_approval_service(),
        DEMO_ADDRESS_ID,
        DemoZeptoService(),
        DEMO_ADDRESS_ID,
        user_id,
        order_confirmations=confirmations,
    )
    outcome = asyncio.run(
        toolbox.execute("prepare_order", json.dumps({"store": "zepto", "payment_method": method}))
    )
    assert outcome.model_output["status"] == "awaiting_tap", outcome.model_output
    assert outcome.ui_event is not None
    return outcome.ui_event


def _order_calls(client: ScriptedZeptoClient) -> list[str]:
    return [name for name in client.names() if name.startswith("create_")]


def test_online_tap_then_status_checks_through_the_api(api: TestClient, world) -> None:
    client, confirmations = world
    alice = as_user("alice")
    _ready_basket(api, alice, oil=1)
    event = _prepare("alice", confirmations, None)
    card = event["confirmation"]
    assert [item["id"] for item in card["payment_methods"]] == ["cod", "online"]

    switched = api.post(
        "/api/orders/payment-method",
        headers=alice,
        json={"confirmation_id": card["id"], "payment_method": "online"},
    )
    assert switched.status_code == 200, switched.text
    online = switched.json()
    assert online["payment_method"] == "online" and online["id"] != card["id"]
    assert api.post(
        "/api/orders/payment-method",
        headers=as_user("bob"),
        json={"confirmation_id": online["id"], "payment_method": "cod"},
    ).status_code == 404
    for bad in ({"confirmation_id": online["id"], "payment_method": "card"},
                {"confirmation_id": online["id"], "payment_method": "online", "upi_pin": "1234"}):
        assert api.post("/api/orders/payment-method", headers=alice, json=bad).status_code == 422

    client.script("create_online_payment_order", start_payload(card["payable_paise"]))
    tapped = api.post("/api/orders/confirm", headers=alice, json={"confirmation_id": online["id"]})
    assert tapped.status_code == 202, tapped.text
    body = tapped.json()
    assert body["status"] == "awaiting_payment"
    assert body["payment"] == {"method": "online", "label": "Pay online", "url": PAY_LINK}
    checkout_id = body["checkout_id"]
    # The old COD card was replaced and cannot order anything.
    assert api.post("/api/orders/confirm", headers=alice, json={"confirmation_id": card["id"]}).status_code == 409
    assert api.post("/api/orders/confirm", headers=alice, json={"confirmation_id": online["id"]}).status_code == 409
    assert _order_calls(client) == ["create_online_payment_order"]

    assert api.post("/api/orders/payment-status", headers=as_user("bob"), json={"checkout_id": checkout_id}).status_code == 404
    client.script("check_payment_status", status_payload("FAKE_OK", "FAKE_PLACED"))
    checked = api.post("/api/orders/payment-status", headers=alice, json={"checkout_id": checkout_id})
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "confirmed" and checked.json()["provider_order_id"] == "ZP-1"
    assert checked.json()["payment"]["url"] is None
    assert [args for name, args in client.calls if name == "check_payment_status"] == [
        {"orderId": "ZP-1", "poll": False}
    ]
    # The ordered basket is closed; the next one starts empty.
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["lines"] == []
    assert _order_calls(client) == ["create_online_payment_order"]


async def test_two_taps_and_two_workers_make_one_payment_order(session_factory) -> None:
    from app.shopping.approval import ApprovalService
    from app.shopping.basket import BasketService
    from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
    from app.shopping.models import BasketLine, ProductRef

    baskets = DatabaseBasketRepository(session_factory)
    approvals = DatabaseApprovalRepository(session_factory)
    approval_service = ApprovalService(baskets, approvals, lambda _: DemoZeptoQuoteProvider())
    await BasketService(baskets, approvals).add_line(
        "alice",
        "zepto",
        BasketLine(
            ref=ProductRef(provider="zepto", product_id="demo-oil", variant_id="zepto-demo-spin-oil"),
            name="Fortune Sunflower Oil",
            unit_price_paise=15100,
            quantity=1,
        ),
    )
    basket = await BasketService(baskets, approvals).set_address("alice", "zepto", "demo-home")
    requested = await approval_service.request(basket.id)
    client = ScriptedZeptoClient()
    client.script("get_payment_methods", methods_payload("FAKE_ONLINE"))
    client.script("create_online_payment_order", start_payload(requested.approval.payable_paise))

    def worker() -> tuple[OrderConfirmations, CheckoutService]:
        payments = payment_gateway(client, DemoPreviewQuotes())  # type: ignore[arg-type]
        return (
            OrderConfirmations(baskets, approval_service, payments=payments),
            CheckoutService(
                baskets, approvals, DatabaseCheckoutAttemptRepository(session_factory),
                DemoZeptoQuoteProvider(), RecordingZeptoGateway(), payments=payments,
            ),
        )

    (one, checkout_one), (two, checkout_two) = worker(), worker()
    card_one = await one.prepare("alice", requested.basket, requested.approval, store="Zepto", payment_method="online")
    card_two = await two.prepare("alice", requested.basket, requested.approval, store="Zepto", payment_method="online")

    results = await asyncio.gather(
        one.place("alice", card_one.id, checkout_one),
        one.place("alice", card_one.id, checkout_one),
        two.place("alice", card_two.id, checkout_two),
        return_exceptions=True,
    )

    assert _order_calls(client) == ["create_online_payment_order"], results
    errors = [item for item in results if isinstance(item, BaseException)]
    assert all(isinstance(error, ApprovalRefused) for error in errors), errors
    waiting = [item for item in results if isinstance(item, OrderResult)]
    assert waiting and {item.checkout_id for item in waiting} == {waiting[0].checkout_id}
    assert all(item.status in {"awaiting_payment", "outcome_unknown"} for item in waiting)

    stored = await DatabaseCheckoutAttemptRepository(session_factory).get(waiting[0].checkout_id or "")
    assert stored is not None and stored.payment_method == "online"
    assert stored.status.value == "awaiting_payment" and stored.payment_url == PAY_LINK
