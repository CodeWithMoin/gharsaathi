"""Failure modes around order cards, the tap, payments, tracking and restarts (docs/FAILURE_MODES.md).

The Zepto order call is a recording fake; every test asserts how many times it
was called. Nothing here can place a real order.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.commerce.zepto.quote_provider import ZeptoCodCheckoutGateway
from app.shopping.checkout import CheckoutService
from app.shopping.errors import ApprovalRefused, NotFound, ProviderUnavailable
from app.shopping.models import BasketStatus, CheckoutStatus
from app.shopping.order_confirmation import OrderConfirmations, OrderResult
from app.tracking.errors import TrackingProviderError
from app.tracking.models import StartTrackingRequest, TrackingProvider, TrackingSnapshot, TrackingStatus
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore
from tests.fault_injection import (
    FEE,
    HOUSEHOLD,
    FaultWorld,
    FaultyMcpClient,
    Payload,
    Placed,
    Raise,
    Slow,
    call,
    transport_error,
    zepto_confirmed,
)

pytestmark = pytest.mark.failure_mode

# Phrases only the confirmed note uses; no uncertain outcome may carry them.
NEVER_CLAIM = ("Tell the user in one short line that the order is placed", "confirmed the order (order id", "paid by")


async def card(world: FaultWorld, toolbox=None) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    toolbox = toolbox or world.toolbox()
    output, event = await call(toolbox, "prepare_order", store="zepto")
    assert output["status"] == "awaiting_tap", output
    assert event is not None
    return event["confirmation"]["id"], event


def payable(world: FaultWorld, quantity: int = 2) -> int:
    return 9850 * quantity + FEE + world.quotes.extra


# ---------------------------------------------------------------- FM-O01


async def test_fm_o01_timeout_after_dispatch_is_outcome_unknown_and_never_retried() -> None:
    world = FaultWorld()
    world.gateway.faults = [Raise(transport_error("timeout"))]
    await world.fill()
    toolbox = world.toolbox()
    confirmation_id, event = await card(world, toolbox)

    result = await world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout)

    assert result.status == "outcome_unknown" and result.provider_order_id is None
    assert len(world.gateway.calls) == 1
    basket = await world.basket_repo.get(event["basket"]["id"])
    assert basket is not None and basket.status == BasketStatus.CHECKOUT_PENDING  # locked, not reopened
    with pytest.raises(ApprovalRefused):
        await world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout)
    assert len(world.gateway.calls) == 1
    note = toolbox.order.take_updates()[0]
    assert "Do not say it was placed or paid" in note and "do not prepare it again" in note


async def test_fm_o01_a_cancelled_request_mid_dispatch_is_recorded_as_unknown() -> None:
    world = FaultWorld()
    world.gateway.faults = [Slow(30)]
    await world.fill()
    confirmation_id, event = await card(world)

    task = asyncio.create_task(world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout))
    while not world.gateway.calls:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    attempts = list(world.attempts.by_approval.values())
    assert [attempt.status for attempt in attempts] == [CheckoutStatus.OUTCOME_UNKNOWN]
    basket = await world.basket_repo.get(event["basket"]["id"])
    assert basket is not None and basket.status == BasketStatus.CHECKOUT_PENDING


# ---------------------------------------------------------------- FM-O02 / O03


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"status": "PLACED", "detail": {"ref": None}}},
        {"content_text": "Request received, please wait"},
        {"data": {"isPreview": False, "paymentMethod": "COD", "toPayAmount": 22200}},
    ],
    ids=["unknown_envelope", "text_without_order_code", "missing_order_id"],
)
async def test_fm_o02_confirmed_order_in_an_unknown_shape_is_unknown_never_placed_or_retried(payload: dict) -> None:
    world = FaultWorld()
    client = FaultyMcpClient({"create_order": [Payload(payload)]})
    world.gateway = ZeptoCodCheckoutGateway(client)  # type: ignore[assignment]
    world.checkout = CheckoutService(
        world.basket_repo, world.approval_repo, world.attempts, world.quotes, world.gateway, world.clock
    )
    await world.fill()
    confirmation_id, _ = await card(world)

    result = await world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout)

    assert result.status == "outcome_unknown"
    assert [(name, args["confirmOrder"], retry) for name, args, retry in client.calls] == [
        ("create_order", True, False)
    ]
    assert not any(claim in result.assistant_note() for claim in NEVER_CLAIM)


async def test_fm_o02_a_wellformed_confirmation_is_placed() -> None:
    world = FaultWorld()
    await world.fill()
    client = FaultyMcpClient({"create_order": [Payload(zepto_confirmed("ZP-123", payable(world)))]})
    world.checkout = CheckoutService(
        world.basket_repo, world.approval_repo, world.attempts, world.quotes, ZeptoCodCheckoutGateway(client), world.clock
    )
    confirmation_id, _ = await card(world)

    result = await world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout)

    assert result.status == "confirmed" and result.provider_order_id == "ZP-123"


def test_fm_o02_contract_unknown_outcomes_can_be_reconciled_read_only() -> None:
    from app.shopping import reconciliation  # noqa: F401

    assert hasattr(OrderConfirmations, "check_order")


async def test_fm_o03_confirmation_for_a_different_amount_is_unknown_not_placed() -> None:
    world = FaultWorld()
    world.gateway.faults = [Payload(Placed(order_id="ZEP-9", payable_paise=1))]
    await world.fill()
    confirmation_id, _ = await card(world)

    result = await world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout)

    assert result.status == "outcome_unknown"
    assert len(world.gateway.calls) == 1


# ---------------------------------------------------------------- FM-O04 / O05 notes


@pytest.mark.parametrize(
    "status", ["outcome_unknown", "awaiting_payment", "payment_succeeded", "payment_unknown", "payment_failed", "paid_order_failed"]
)
def test_fm_o04_assistant_note_never_lets_the_model_claim_placed_or_paid(status: str) -> None:
    note = OrderResult("conf_1", status, "Zepto", 22200, payment_method="upi_reserve").assistant_note()  # type: ignore[arg-type]

    assert not any(claim in note for claim in NEVER_CLAIM)
    assert any(word in note for word in ("Do not say", "Nothing is paid", "not placed", "not created", "not confirmed"))


# ---------------------------------------------------------------- FM-O11


async def test_fm_o11_bill_refresh_failing_at_the_tap_places_nothing_and_says_so() -> None:
    world = FaultWorld()
    await world.fill()
    toolbox = world.toolbox()
    confirmation_id, _ = await card(world, toolbox)
    world.quotes.faults = [Raise(transport_error("http_502"))]

    with pytest.raises(ProviderUnavailable, match="No order was placed"):
        await world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout)

    assert world.gateway.calls == []
    assert world.attempts.by_approval == {}
    note = toolbox.order.take_updates()[0]
    assert note.startswith("The order was not placed") and "Nothing was ordered" in note


# ---------------------------------------------------------------- FM-B08 / B06


async def test_fm_b08_two_tabs_tapping_two_cards_for_one_basket_place_one_order() -> None:
    world = FaultWorld()
    world.gateway.faults = [Slow(0.05)]
    await world.fill()
    first, _ = await card(world, world.toolbox())
    second, _ = await card(world, world.toolbox())

    results = await asyncio.gather(
        world.confirmations.place(HOUSEHOLD, first, world.checkout),
        world.confirmations.place(HOUSEHOLD, second, world.checkout),
        return_exceptions=True,
    )

    assert len(world.gateway.calls) == 1
    placed = [r for r in results if isinstance(r, OrderResult) and r.status == "confirmed"]
    refused = [r for r in results if isinstance(r, ApprovalRefused)]
    assert len(placed) == 1 and len(refused) == 1
    assert "newer order card" in str(refused[0])


async def test_fm_b06_a_basket_edit_by_voice_after_the_card_places_nothing() -> None:
    world = FaultWorld()
    await world.fill()
    toolbox = world.toolbox()
    confirmation_id, _ = await card(world, toolbox)
    basket = await world.baskets.active(HOUSEHOLD, "zepto")
    await call(toolbox, "set_basket_quantity", item_id=basket.lines[0].ref.variant_id, quantity=3, store="zepto")

    with pytest.raises(ApprovalRefused, match="changed"):
        await world.confirmations.place(HOUSEHOLD, confirmation_id, world.checkout)

    assert world.gateway.calls == []


# ---------------------------------------------------------------- FM-S01 / S04 / S05 (restart)


async def test_fm_s01_backend_restart_drops_cards_and_a_tap_asks_again_without_ordering() -> None:
    world = FaultWorld()
    await world.fill()
    confirmation_id, _ = await card(world)
    restarted = OrderConfirmations(world.basket_repo, world.approvals, world.clock)

    with pytest.raises(NotFound, match="Ask for the order again"):
        await restarted.place(HOUSEHOLD, confirmation_id, world.checkout)

    assert world.gateway.calls == []


async def test_fm_s04_backend_restart_drops_option_ids_and_add_asks_for_a_new_search() -> None:
    world = FaultWorld()
    await call(world.toolbox(), "search_products", query="Amul Taaza milk")
    fresh = world.toolbox()  # what typed chat / voice rebuild after a restart

    output, _ = await call(fresh, "add_to_basket", option_id="opt1", quantity=1)

    assert output["status"] == "error" and "Search again" in output["message"]
    assert await world.snapshot() == (0, [], "draft")


async def test_fm_s05_an_unknown_outcome_survives_a_restart_and_blocks_a_second_order() -> None:
    world = FaultWorld()
    world.gateway.faults = [Raise(transport_error("connection_reset"))]
    await world.fill()
    first, _ = await card(world)
    await world.confirmations.place(HOUSEHOLD, first, world.checkout)

    restarted = FaultWorld.__new__(FaultWorld)
    restarted.__dict__.update(world.__dict__)
    restarted.confirmations = OrderConfirmations(world.basket_repo, world.approvals, world.clock)
    output, _ = await call(restarted.toolbox(), "prepare_order", store="zepto")

    assert output["status"] == "error" and "Checkout already started" in output["message"]
    assert len(world.gateway.calls) == 1


# ---------------------------------------------------------------- FM-T01 tracking


class FailingTracker:
    provider = TrackingProvider.ZEPTO

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def fetch_delivery_status(self, *, order_id: str, address_id: str | None) -> TrackingSnapshot:
        self.calls += 1
        if self.fail:
            raise TrackingProviderError()
        return TrackingSnapshot(status=TrackingStatus.PREPARING, provider_status="packing", poll_interval_seconds=60)


async def test_fm_t01_tracking_read_failure_keeps_the_last_status_and_backs_off(tmp_path: Path) -> None:
    now = [__import__("datetime").datetime(2026, 11, 22, 10, 0, tzinfo=__import__("datetime").UTC)]
    tracker = FailingTracker()
    service = OrderTrackingService(
        OrderTrackingStore(tmp_path), {TrackingProvider.ZEPTO: tracker}, now=lambda: now[0], jitter=lambda low, high: low
    )
    started = await service.start(HOUSEHOLD, StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id="ZEP-1"))
    tracker.fail = True
    now[0] += timedelta(seconds=61)

    read = await service.refresh(HOUSEHOLD, started.order.id)

    assert read.order.status == TrackingStatus.PREPARING  # last known, never guessed
    assert read.order.last_error
    assert read.next_check_at is not None and read.next_check_at > now[0]
    await service.refresh(HOUSEHOLD, started.order.id)  # within the back-off: no provider call
    assert tracker.calls == 2


# ---------------------------------------------------------------- FM-O12 (HTTP mapping)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(ProviderUnavailable("Zepto could not refresh this bill. No order was placed."), 502), (ApprovalRefused("expired"), 409), (NotFound("gone"), 404)],
)
def test_fm_o12_tap_errors_map_to_statuses_the_card_understands(error: Exception, status_code: int) -> None:
    from fastapi import HTTPException

    from app.api.orders import _raise_http

    with pytest.raises(HTTPException) as raised:
        _raise_http(error)  # type: ignore[arg-type]
    assert raised.value.status_code == status_code


def test_fm_o12_outcome_unknown_is_a_202_with_no_order_id() -> None:
    from app.api.orders import _result_out

    out = _result_out(OrderResult("conf_1", "outcome_unknown", "Zepto", 22200))
    assert out.status == "outcome_unknown" and out.provider_order_id is None and out.tracking is None
    assert json.loads(out.model_dump_json())["payable"]["display"] == "₹222"
