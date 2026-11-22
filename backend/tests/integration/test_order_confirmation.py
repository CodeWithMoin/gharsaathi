"""The order card tap on PostgreSQL: POST /api/orders/confirm and /cancel.

The card is prepared through the voice/typed tool path (``VoiceToolbox`` →
``prepare_order``) against the app's own repositories; the only consequential
capability, Zepto's COD confirmation, is a recording fake. Nothing here can
place a real order.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.commerce.demo import DEMO_ADDRESS_ID, DemoSwiggyService, DemoZeptoService
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
from app.shopping.order_confirmation import OrderConfirmations, OrderResult
from app.voice.toolbox import VoiceToolbox
from tests.integration.helpers import as_user
from tests.integration.test_shopping_checkout import (
    ZEPTO,
    RecordingZeptoGateway,
    _checkout_service,
    _ready_basket,
)


@pytest.fixture
def gateway(override) -> RecordingZeptoGateway:
    recording = RecordingZeptoGateway()
    service = _checkout_service(recording)
    override(get_zepto_checkout_service, lambda: service)
    return recording


def _prepare(user_id: str, confirmations: OrderConfirmations | None = None) -> dict:
    """Run the prepare_order tool exactly as a voice or typed turn would."""

    toolbox = VoiceToolbox(
        DemoSwiggyService(),
        get_basket_service(),
        get_approval_service(),
        DEMO_ADDRESS_ID,
        DemoZeptoService(),
        DEMO_ADDRESS_ID,
        user_id,
        order_confirmations=confirmations or get_order_confirmations(),
    )
    outcome = asyncio.run(toolbox.execute("prepare_order", json.dumps({"store": "zepto"})))
    assert outcome.model_output["status"] == "awaiting_tap", outcome.model_output
    assert outcome.ui_event is not None
    return outcome.ui_event


def _confirm(api: TestClient, user: dict[str, str], confirmation_id: str):
    return api.post("/api/orders/confirm", headers=user, json={"confirmation_id": confirmation_id})


def test_tap_places_one_order_and_locks_the_basket(api: TestClient, gateway: RecordingZeptoGateway) -> None:
    alice = as_user("alice")
    _ready_basket(api, alice, oil=2)
    event = _prepare("alice")
    confirmation_id = event["confirmation"]["id"]
    assert gateway.calls == []
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["status"] == "awaiting_approval"

    assert _confirm(api, as_user("bob"), confirmation_id).status_code == 404
    placed = _confirm(api, alice, confirmation_id)
    assert placed.status_code == 200, placed.text
    body = placed.json()
    assert body["status"] == "confirmed" and body["provider_order_id"] == "ZEP-1"
    assert body["payable"]["paise"] == event["confirmation"]["payable_paise"]
    assert len(gateway.calls) == 1

    replay = _confirm(api, alice, confirmation_id)
    assert replay.status_code == 409 and "already used" in replay.json()["detail"]
    assert len(gateway.calls) == 1
    # The ordered basket is closed; the next one starts empty.
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["lines"] == []


def test_confirmed_tap_starts_one_user_scoped_tracking_monitor(api: TestClient, gateway: RecordingZeptoGateway) -> None:
    alice, bob = as_user("alice"), as_user("bob")
    _ready_basket(api, alice, oil=1)
    confirmation_id = _prepare("alice")["confirmation"]["id"]

    placed = _confirm(api, alice, confirmation_id).json()
    tracking = placed["tracking"]
    assert tracking is not None and tracking["order"]["order_id"] == "ZEP-1"
    assert tracking["order"]["status"] == "out_for_delivery"  # demo read-only adapter
    monitor_id = tracking["monitor_id"]

    # A replayed tap is refused and starts nothing; only alice sees the monitor.
    assert _confirm(api, alice, confirmation_id).status_code == 409
    assert [watch["id"] for watch in api.get("/api/tracking/monitors", headers=alice).json()] == [monitor_id]
    assert api.get("/api/tracking/monitors", headers=bob).json() == []
    assert api.get(f"/api/tracking/monitors/{monitor_id}", headers=bob).status_code == 404
    assert len(gateway.calls) == 1


def test_cancel_then_tap_places_nothing(api: TestClient, gateway: RecordingZeptoGateway) -> None:
    alice = as_user("alice")
    _ready_basket(api, alice)
    confirmation_id = _prepare("alice")["confirmation"]["id"]

    assert api.post("/api/orders/cancel", headers=as_user("bob"), json={"confirmation_id": confirmation_id}).status_code == 404
    cancelled = api.post("/api/orders/cancel", headers=alice, json={"confirmation_id": confirmation_id})
    assert cancelled.status_code == 200 and cancelled.json() == {"status": "cancelled"}
    assert _confirm(api, alice, confirmation_id).status_code == 409
    assert gateway.calls == []


def test_basket_edit_after_the_card_places_nothing(api: TestClient, gateway: RecordingZeptoGateway) -> None:
    alice = as_user("alice")
    product = _ready_basket(api, alice, oil=1)
    confirmation_id = _prepare("alice")["confirmation"]["id"]
    api.patch(f"/api/basket/lines/{product['spin_id']}", headers=alice, params=ZEPTO, json={"quantity": 2})

    refused = _confirm(api, alice, confirmation_id)
    assert refused.status_code == 409
    assert gateway.calls == []


def test_tap_needs_current_terms(api: TestClient, gateway: RecordingZeptoGateway, enforce_legal_consent) -> None:
    refused = _confirm(api, as_user("carol"), "confirmationid123")
    assert refused.status_code == 403
    assert gateway.calls == []


def test_malformed_confirmation_ids_are_rejected(api: TestClient, gateway: RecordingZeptoGateway) -> None:
    alice = as_user("alice")
    for body in ({}, {"confirmation_id": "short"}, {"confirmation_id": "has spaces in it!"}, {"confirmation_id": "x" * 65}):
        assert api.post("/api/orders/confirm", headers=alice, json=body).status_code == 422
    assert api.post(
        "/api/orders/confirm", headers=alice, json={"confirmation_id": "abcdefgh123", "amount": 1}
    ).status_code == 422
    assert gateway.calls == []


async def test_two_taps_on_one_card_and_two_workers_dispatch_once(session_factory) -> None:
    """Same card tapped twice, and two processes each holding a card for the same bill."""

    from app.shopping.approval import ApprovalService
    from app.shopping.basket import BasketService
    from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
    from app.shopping.models import BasketLine, ProductRef
    from app.commerce.demo import DemoZeptoQuoteProvider

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
    gateway = RecordingZeptoGateway(delay=0.05)

    def worker() -> tuple[OrderConfirmations, CheckoutService]:
        return (
            OrderConfirmations(baskets, approval_service),
            CheckoutService(
                baskets, approvals, DatabaseCheckoutAttemptRepository(session_factory),
                DemoZeptoQuoteProvider(), gateway,
            ),
        )

    (one, checkout_one), (two, checkout_two) = worker(), worker()
    card_one = await one.prepare("alice", requested.basket, requested.approval, store="Zepto")
    card_two = await two.prepare("alice", requested.basket, requested.approval, store="Zepto")

    results = await asyncio.gather(
        one.place("alice", card_one.id, checkout_one),
        one.place("alice", card_one.id, checkout_one),
        two.place("alice", card_two.id, checkout_two),
        return_exceptions=True,
    )

    assert len(gateway.calls) == 1, results
    errors = [item for item in results if isinstance(item, BaseException)]
    assert all(isinstance(error, ApprovalRefused) for error in errors), errors
    placed = [item for item in results if isinstance(item, OrderResult) and item.status == "confirmed"]
    assert len(placed) >= 1 and len({item.provider_order_id for item in placed}) == 1
