"""Unknown order outcome → read-only reconciliation from Zepto's order history.

Fakes only: in-memory baskets/approvals/attempts, a gateway whose order call
times out after (pretend) dispatch, and a fake MCP client behind the real
ZeptoService that serves canned order history. The tests assert that only
the read-only history tools are ever called during reconciliation.
"""

from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.commerce.swiggy.models import ProductSearchResult
from app.commerce.zepto.service import ZeptoService
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService, item_total_paise
from app.shopping.checkout import CheckoutService
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.models import (
    BasketLine,
    BasketStatus,
    Charge,
    CheckoutAttempt,
    CheckoutStatus,
    ProductRef,
    Quote,
)
from app.shopping.order_confirmation import OrderConfirmations
from app.shopping.reconciliation import CheckoutReconciler
from app.tracking.models import TrackingProvider, TrackingSnapshot, TrackingStatus
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import Clock, InMemoryCheckoutAttempts

USER = "user_reconcile"
ADDRESS = "zepto-home"
FEE = 2500
CODE = "UIIMJHTCC04881"
READ_TOOLS = {"list_order_history", "get_order_detail"}


class Quotes:
    async def quote(self, basket):  # type: ignore[no-untyped-def]
        items = item_total_paise(basket)
        return Quote(
            provider=basket.provider,
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=items,
            charges=[Charge(label="Delivery fee", amount_paise=FEE)],
            payable_paise=items + FEE,
            quoted_at=basket.updated_at,
        )


class TimingOutGateway:
    """Zepto accepted the order but the response never arrived."""

    def __init__(self) -> None:
        self.calls = 0

    async def confirm_cod(self, basket):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise TimeoutError("response lost after dispatch")


class HistoryClient:
    """Canned read-only Zepto history; records every tool name."""

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.calls: list[str] = []

    async def call_tool(self, name: str, arguments: dict[str, Any], *, retry_on_auth: bool = True) -> dict[str, Any]:
        self.calls.append(name)
        if name == "list_order_history":
            orders = self.orders[: arguments["limit"]]
            return {"count": len(self.orders), "orders": [order["summary"] for order in orders]}
        if name == "get_order_detail":
            return next(order["detail"] for order in self.orders if order["summary"]["id"] == arguments["orderId"])
        raise AssertionError(f"reconciliation must not call {name}")

    def add(
        self,
        order_id: str,
        code: str,
        placed: str,
        *,
        total: int = 5500,
        name: str = "Amul Sweet Lassi",
        quantity: int = 1,
    ) -> None:
        product = {
            "id": f"prod-{order_id}",
            "productName": name,
            "brand": "Amul",
            "packsize": "200 ml",
            "imagePath": "x.png",
            "status": "PLACED",
            "quantityOrdered": quantity,
            "quantityFulfilled": 0,
            "quantityRejected": 0,
            "unitMRP": 3000,
            "unitSellingPrice": 3000,
            "totalMRP": 3000 * quantity,
            "totalFinalSellingPrice": 3000 * quantity,
            "totalRefundAmount": 0,
        }
        self.orders.insert(
            0,
            {
                "summary": {
                    "id": order_id,
                    "code": code,
                    "formattedStatus": "IN_TRANSIT",
                    "grandTotalAmount": total,
                    "itemQuantityCount": quantity,
                    "productsNamesAndCounts": [{"name": name, "count": quantity}],
                    "totalArrivalTimeInMinutes": None,
                    "totalRefundAmount": 0,
                },
                "detail": {
                    "id": f"internal-{order_id}",
                    "orderId": order_id,
                    "code": code,
                    "formattedStatus": "In transit",
                    "orderType": "INSTANT",
                    "placedTime": placed,
                    "userAddressFormatted": "Home",
                    "itemQuantityCount": quantity,
                    "grandTotalAmount": total,
                    "grandTotalAmountPreDiscount": total,
                    "totalRefundAmount": 0,
                    "walletBalanceUsed": 0,
                    "isInvoiceAvailable": False,
                    "invoiceUrl": None,
                    "isEligibleForRating": False,
                    "billSummary": {"itemTotal": total, "totalBill": total, "totalSaved": 0},
                    "shipments": [
                        {
                            "shipmentId": "s1",
                            "shipmentCode": "S1",
                            "status": "IN_TRANSIT",
                            "formattedStatus": "In transit",
                            "placedTime": placed,
                            "deliveryTime": None,
                            "arrivedTime": None,
                            "committedEtaInMins": 12,
                            "totalArrivalTimeInMinutes": None,
                            "shipmentWeightInGms": 250,
                            "products": [product],
                        }
                    ],
                },
            },
        )


class TrackingAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_delivery_status(self, *, order_id: str, address_id: str | None) -> TrackingSnapshot:
        self.calls.append(order_id)
        return TrackingSnapshot(
            status=TrackingStatus.PREPARING, provider_status="In transit", poll_interval_seconds=600, order_code=CODE
        )


class NoSearch:
    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=[])


class World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.basket_repo = InMemoryBasketRepository()
        self.approval_repo = InMemoryApprovalRepository()
        self.baskets = BasketService(self.basket_repo, self.approval_repo, self.clock)
        self.approvals = ApprovalService(self.basket_repo, self.approval_repo, lambda _: Quotes(), self.clock)
        self.attempts = InMemoryCheckoutAttempts(self.basket_repo, self.approval_repo)
        self.gateway = TimingOutGateway()
        self.checkout = CheckoutService(
            self.basket_repo, self.approval_repo, self.attempts, Quotes(), self.gateway, self.clock
        )
        self.client = HistoryClient()
        self.adapter = TrackingAdapter()
        self.tracking = OrderTrackingService(
            OrderTrackingStore(Path(tempfile.mkdtemp(prefix="reconcile-tracking-"))),
            {TrackingProvider.ZEPTO: self.adapter},
        )
        self.sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.clock.advance(seconds=seconds)

        self.reconciler = CheckoutReconciler(
            self.attempts,
            self.basket_repo,
            ZeptoService(object(), self.client),  # type: ignore[arg-type]
            tracking=self.tracking,
            clock=self.clock,
            sleep=sleep,
        )
        self.confirmations = OrderConfirmations(
            self.basket_repo, self.approvals, self.clock, reconciler=self.reconciler
        )

    def toolbox(self) -> VoiceToolbox:
        return VoiceToolbox(
            NoSearch(),
            self.baskets,
            self.approvals,
            "swiggy-home",
            NoSearch(),
            ADDRESS,
            USER,
            order_confirmations=self.confirmations,
            order_tracking=self.tracking,
        )

    def placed_iso(self, offset: timedelta = timedelta(seconds=20)) -> str:
        return (self.clock.now + offset).isoformat().replace("+00:00", "Z")

    async def unknown_order(self, toolbox: VoiceToolbox) -> tuple[Any, str]:
        """1 × Amul Sweet Lassi, ₹55 COD; the tap's order call times out."""

        await self.baskets.add_line(
            USER,
            "zepto",
            BasketLine(
                ref=ProductRef(provider="zepto", product_id="lassi", variant_id="lassi-v", sku_id="lassi-s"),
                name="Amul Sweet Lassi",
                pack_size="200 ml",
                unit_price_paise=3000,
                quantity=1,
            ),
        )
        outcome = await toolbox.execute("prepare_order", json.dumps({"store": "zepto"}))
        confirmation_id = outcome.ui_event["confirmation"]["id"]  # type: ignore[index]
        result = await self.confirmations.place(USER, confirmation_id, self.checkout)
        assert result.status == "outcome_unknown" and result.payable_paise == 5500
        return result, outcome.ui_event["basket"]["id"]  # type: ignore[index]

    async def settle(self) -> None:
        for task in list(self.reconciler._tasks):
            await task


@pytest.fixture
def world() -> World:
    return World()


async def test_unknown_outcome_reconciles_to_the_single_matching_order(world: World) -> None:
    toolbox = world.toolbox()
    world.client.add("ord-old", "OLDCODE0001", world.placed_iso(-timedelta(days=2)))
    world.client.add("ord-live", CODE, world.placed_iso())
    result, basket_id = await world.unknown_order(toolbox)

    await world.settle()

    attempt = await world.attempts.get(result.checkout_id)
    assert attempt is not None and attempt.status == CheckoutStatus.CONFIRMED
    assert attempt.provider_order_id == "ord-live"
    basket = await world.basket_repo.get(basket_id)
    assert basket is not None and basket.status == BasketStatus.ORDERED
    # The store has no open basket any more: the next add starts a fresh one.
    fresh = await world.baskets.active(USER, "zepto")
    assert fresh.id != basket_id and fresh.lines == []
    # Tracking started for the reconciled order (one read-only status read).
    watch = world.tracking.find(USER, "ord-live")
    assert watch is not None and world.adapter.calls == ["ord-live"]
    updates = toolbox.order.take_updates()
    assert len(updates) == 2
    assert "did not confirm" in updates[0]
    assert "order history shows this order was placed" in updates[1] and CODE in updates[1]
    # Only read-only tools; the order call was made exactly once, by the tap.
    assert set(world.client.calls) <= READ_TOOLS and world.gateway.calls == 1
    assert world.sleeps == [15.0]


async def test_no_matching_order_stays_unknown_after_the_bounded_window(world: World) -> None:
    toolbox = world.toolbox()
    world.client.add("ord-other", "OTHER00001", world.placed_iso(), total=9900)
    result, basket_id = await world.unknown_order(toolbox)

    await world.settle()

    attempt = await world.attempts.get(result.checkout_id)
    assert attempt is not None and attempt.status == CheckoutStatus.OUTCOME_UNKNOWN
    assert world.reconciler.checks_made(attempt.id) == 3 and world.sleeps == [15.0, 45.0, 60.0]
    assert world.client.calls.count("list_order_history") == 3
    assert "get_order_detail" not in world.client.calls  # the total never matched
    basket = await world.basket_repo.get(basket_id)
    assert basket is not None and basket.status == BasketStatus.CHECKOUT_PENDING
    updates = toolbox.order.take_updates()
    assert len(updates) == 2 and "no matching order" in updates[1] and "Zepto app" in updates[1]
    assert set(world.client.calls) <= READ_TOOLS and world.gateway.calls == 1


async def test_two_matching_orders_stay_unknown_and_are_never_guessed(world: World) -> None:
    toolbox = world.toolbox()
    world.client.add("ord-a", "AAAA000001", world.placed_iso(timedelta(seconds=5)))
    world.client.add("ord-b", "BBBB000002", world.placed_iso(timedelta(seconds=40)))
    result, _ = await world.unknown_order(toolbox)

    await world.settle()

    attempt = await world.attempts.get(result.checkout_id)
    assert attempt is not None and attempt.status == CheckoutStatus.OUTCOME_UNKNOWN
    assert attempt.provider_order_id is None
    note = toolbox.order.take_updates()[-1]
    assert "#AAAA000001" in note and "#BBBB000002" in note and "not guess" in note
    assert world.reconciler.checks_made(attempt.id) == 1  # an ambiguous answer stops the schedule
    assert set(world.client.calls) <= READ_TOOLS


@pytest.mark.parametrize(
    "variant",
    [
        {"offset": timedelta(minutes=25)},  # placed outside the window
        {"offset": -timedelta(minutes=5)},  # before the tap
        {"name": "Amul Kool Cafe"},
        {"quantity": 2, "total": 5500},
    ],
)
async def test_an_order_that_differs_in_time_items_or_quantity_does_not_match(
    world: World, variant: dict[str, Any]
) -> None:
    toolbox = world.toolbox()
    offset = variant.pop("offset", timedelta(seconds=20))
    world.client.add("ord-x", CODE, world.placed_iso(offset), **variant)
    result, _ = await world.unknown_order(toolbox)

    await world.settle()

    attempt = await world.attempts.get(result.checkout_id)
    assert attempt is not None and attempt.status == CheckoutStatus.OUTCOME_UNKNOWN


async def test_check_with_zepto_is_read_only_and_rate_limited(world: World) -> None:
    toolbox = world.toolbox()
    world.reconciler.follow_up_delays = ()
    result, _ = await world.unknown_order(toolbox)

    first = await world.confirmations.check_order(USER, result.checkout_id)
    second = await world.confirmations.check_order(USER, result.checkout_id)
    assert first.status == second.status == "outcome_unknown" and first.check == "not_found"
    assert world.client.calls == ["list_order_history"]  # the second tap reused the answer

    world.client.add("ord-live", CODE, world.placed_iso(timedelta(seconds=-30)))
    world.clock.advance(seconds=16)
    third = await world.confirmations.check_order(USER, result.checkout_id)
    assert third.status == "confirmed" and third.provider_order_id == "ord-live"
    assert third.order_code == CODE and third.source == "order_history"
    assert set(world.client.calls) <= READ_TOOLS and world.gateway.calls == 1
    # Another household cannot check (or learn about) this checkout.
    with pytest.raises(Exception):
        await world.confirmations.check_order("someone-else", result.checkout_id)


async def test_order_status_tool_reports_not_confirmed_then_the_reconciled_order(world: World) -> None:
    toolbox = world.toolbox()
    world.reconciler.follow_up_delays = ()
    await world.unknown_order(toolbox)
    toolbox.order.take_updates()

    before = await toolbox.execute("get_order_status", json.dumps({"order_ref": "latest"}))
    assert before.model_output["status"] == "not_confirmed"
    assert before.model_output["order_confirmed"] is False
    assert "NOT confirmed" in before.model_output["note"] and "Check with Zepto" in before.model_output["note"]

    world.client.add("ord-live", CODE, world.placed_iso(timedelta(seconds=-30)))
    world.clock.advance(seconds=16)
    after = await toolbox.execute("get_order_status", json.dumps({"order_ref": "latest"}))
    output = after.model_output
    assert output["status"] == "ok" and output["order_confirmed"] is True
    assert output["confirmed_via"] == "order_history" and output["order_code"] == CODE
    # The tool result told the model; no duplicate note is queued for the session.
    assert toolbox.order.take_updates() == []
    assert set(world.client.calls) <= READ_TOOLS and world.gateway.calls == 1


async def test_prepaid_unknown_outcome_only_moves_to_awaiting_payment(world: World) -> None:
    basket = await world.baskets.add_line(
        USER,
        "zepto",
        BasketLine(
            ref=ProductRef(provider="zepto", product_id="lassi", variant_id="lassi-v", sku_id="lassi-s"),
            name="Amul Sweet Lassi",
            unit_price_paise=3000,
            quantity=1,
        ),
    )
    attempt = CheckoutAttempt(
        id="prepaid-attempt-1",
        approval_id="approval-1",
        basket_id=basket.id,
        household_id=USER,
        idempotency_key_hash="0" * 64,
        payable_paise=5500,
        status=CheckoutStatus.OUTCOME_UNKNOWN,
        created_at=world.clock.now,
        completed_at=world.clock.now,
        payment_method="upi_reserve",
    )
    world.attempts.by_approval[attempt.approval_id] = attempt
    world.client.add("ord-paid", CODE, world.placed_iso())

    outcome = await world.reconciler.reconcile(attempt.id)

    assert outcome.check == "matched" and not outcome.placed
    stored = await world.attempts.get(attempt.id)
    assert stored is not None and stored.status == CheckoutStatus.AWAITING_PAYMENT
    assert stored.provider_order_id == "ord-paid"
    assert world.tracking.find(USER, "ord-paid") is None  # not placed until payment is confirmed


async def test_dry_run_reports_without_writing(world: World) -> None:
    import io

    from app.shopping.reconcile_unknown import run

    toolbox = world.toolbox()
    world.reconciler.follow_up_delays = ()
    world.client.add("ord-live", CODE, world.placed_iso())
    result, _ = await world.unknown_order(toolbox)
    out = io.StringIO()

    counts = await run(
        apply=False,
        attempts=world.attempts,
        baskets=world.basket_repo,
        history_factory=lambda: ZeptoService(object(), world.client),  # type: ignore[arg-type]
        out=out,
    )

    assert counts == {"attempts": 1, "matched": 1, "applied": 0, "unresolved": 0}
    text = out.getvalue()
    assert f"order_code={CODE}" in text and "match=yes" in text and "applied=no" in text
    assert "Lassi" not in text and "5500" not in text
    stored = await world.attempts.get(result.checkout_id)
    assert stored is not None and stored.status == CheckoutStatus.OUTCOME_UNKNOWN

    counts = await run(
        apply=True,
        attempts=world.attempts,
        baskets=world.basket_repo,
        history_factory=lambda: ZeptoService(object(), world.client),  # type: ignore[arg-type]
        tracking=world.tracking,
        out=io.StringIO(),
    )
    assert counts["applied"] == 1
    stored = await world.attempts.get(result.checkout_id)
    assert stored is not None and stored.status == CheckoutStatus.CONFIRMED
    # Idempotent: a second run finds nothing left to settle.
    again = await run(
        apply=True,
        attempts=world.attempts,
        baskets=world.basket_repo,
        history_factory=lambda: (_ for _ in ()).throw(AssertionError("no provider needed")),
        out=io.StringIO(),
    )
    assert again == {"attempts": 0, "matched": 0, "applied": 0, "unresolved": 0}
    assert set(world.client.calls) <= READ_TOOLS
