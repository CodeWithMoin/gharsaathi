"""Unknown-outcome reconciliation and the order-placed transaction on PostgreSQL.

The order call is a recording fake that times out (the outcome is unknown);
Zepto's order history is a fake read-only client behind the real ZeptoService.
Nothing here can place a real order or reach a provider.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.commerce.demo import DemoZeptoQuoteProvider
from app.commerce.zepto.service import ZeptoService
from app.database.session import get_session_factory
from app.dependencies import get_order_confirmations, get_zepto_checkout_service
from app.shopping.basket import BasketService
from app.shopping.checkout import CheckoutService, DatabaseCheckoutAttemptRepository
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.models import ApprovalStatus, BasketLine, BasketStatus, CheckoutStatus, ProductRef
from app.shopping.reconciliation import CheckoutReconciler
from tests.integration.helpers import as_user
from tests.integration.test_order_confirmation import _confirm, _prepare
from tests.integration.test_shopping_checkout import (
    ZEPTO,
    RecordingZeptoGateway,
    _approved_zepto_basket,
    _checkout_service,
    _ready_basket,
)
from tests.module.test_checkout_reconciliation import CODE, READ_TOOLS, HistoryClient


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class FailingFlush:
    """Abort every flush while installed: the transaction must change nothing."""

    def __enter__(self) -> "FailingFlush":
        event.listen(Session, "before_flush", self._fail)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(Session, "before_flush", self._fail)

    @staticmethod
    def _fail(session, flush_context, instances) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("database write failed")


async def _unknown_attempt(session_factory, household: str = "alice"):  # type: ignore[no-untyped-def]
    baskets, approvals, approval_id = await _approved_zepto_basket(session_factory, household)
    attempts = DatabaseCheckoutAttemptRepository(session_factory)
    gateway = RecordingZeptoGateway(fail=True)
    checkout = CheckoutService(baskets, approvals, attempts, DemoZeptoQuoteProvider(), gateway)
    attempt = await checkout.checkout_cod(approval_id, household, "key-unknown")
    assert attempt.status == CheckoutStatus.OUTCOME_UNKNOWN and len(gateway.calls) == 1
    return baskets, approvals, attempts, attempt, gateway


async def test_reconcile_closes_the_basket_in_one_idempotent_transaction(session_factory) -> None:
    baskets, approvals, attempts, attempt, gateway = await _unknown_attempt(session_factory)
    at = datetime.now(UTC)

    first, second = await asyncio.gather(
        attempts.reconcile(attempt.id, provider_order_id="ord-live", at=at),
        attempts.reconcile(attempt.id, provider_order_id="ord-other", at=at),
    )
    again = await attempts.reconcile(attempt.id, provider_order_id="ord-third", at=at)

    # Exactly one writer won; everyone sees the same durable order id.
    assert first.status == second.status == again.status == CheckoutStatus.CONFIRMED
    assert first.provider_order_id == second.provider_order_id == again.provider_order_id
    assert again.provider_status == "reconciled_from_history"
    approval = await approvals.get(attempt.approval_id)
    basket = await baskets.get(attempt.basket_id)
    assert approval is not None and approval.status == ApprovalStatus.CONSUMED
    assert basket is not None and basket.status == BasketStatus.ORDERED
    # The store has no open basket: the next add starts a fresh, empty one.
    assert await baskets.active_for("alice", "zepto") is None
    fresh = await BasketService(baskets, approvals).add_line(
        "alice",
        "zepto",
        BasketLine(
            ref=ProductRef(provider="zepto", product_id="p2", variant_id="v2"),
            name="Amul Sweet Lassi",
            unit_price_paise=3000,
            quantity=1,
        ),
    )
    assert fresh.id != basket.id and [line.name for line in fresh.lines] == ["Amul Sweet Lassi"]
    assert len(gateway.calls) == 1


async def test_a_failed_transaction_changes_nothing(session_factory) -> None:
    baskets, approvals, attempts, attempt, _ = await _unknown_attempt(session_factory)

    with FailingFlush(), pytest.raises(RuntimeError):
        await attempts.reconcile(attempt.id, provider_order_id="ord-live", at=datetime.now(UTC))

    stored = await attempts.get(attempt.id)
    basket = await baskets.get(attempt.basket_id)
    approval = await approvals.get(attempt.approval_id)
    assert stored is not None and stored.status == CheckoutStatus.OUTCOME_UNKNOWN
    assert stored.provider_order_id is None
    assert basket is not None and basket.status == BasketStatus.CHECKOUT_PENDING
    assert approval is not None and approval.status == ApprovalStatus.CONSUMING


async def test_a_failed_confirm_transaction_leaves_the_claim_and_is_not_retried(session_factory) -> None:
    baskets, approvals, approval_id = await _approved_zepto_basket(session_factory, "alice")
    attempts = DatabaseCheckoutAttemptRepository(session_factory)
    gateway = RecordingZeptoGateway()
    checkout = CheckoutService(baskets, approvals, attempts, DemoZeptoQuoteProvider(), gateway)

    original = attempts.confirm

    async def failing_confirm(*args, **kwargs):  # type: ignore[no-untyped-def]
        with FailingFlush():
            return await original(*args, **kwargs)

    attempts.confirm = failing_confirm  # type: ignore[method-assign]
    attempt = await checkout.checkout_cod(approval_id, "alice", "key-1")

    # The provider call happened once; the local write failed atomically, so
    # the outcome is recorded as unknown (never a second provider call).
    assert len(gateway.calls) == 1
    assert attempt.status == CheckoutStatus.OUTCOME_UNKNOWN
    basket = await baskets.get(attempt.basket_id)
    assert basket is not None and basket.status == BasketStatus.CHECKOUT_PENDING
    replay = await checkout.checkout_cod(approval_id, "alice", "key-1")
    assert replay.id == attempt.id and len(gateway.calls) == 1


async def test_second_worker_after_a_confirmed_order_sees_the_closed_basket(session_factory) -> None:
    baskets, approvals, approval_id = await _approved_zepto_basket(session_factory, "alice")
    gateway = RecordingZeptoGateway()
    workers = [
        CheckoutService(
            baskets, approvals, DatabaseCheckoutAttemptRepository(session_factory), DemoZeptoQuoteProvider(), gateway
        )
        for _ in range(2)
    ]

    placed = await workers[0].checkout_cod(approval_id, "alice", "key-a")
    late = await workers[1].checkout_cod(approval_id, "alice", "key-a")

    assert placed.status == CheckoutStatus.CONFIRMED and late.id == placed.id
    assert len(gateway.calls) == 1
    basket = await baskets.get(placed.basket_id)
    assert basket is not None and basket.status == BasketStatus.ORDERED
    assert await baskets.active_for("alice", "zepto") is None


@pytest.fixture
def history() -> HistoryClient:
    return HistoryClient()


@pytest.fixture
def timing_out(override, history: HistoryClient) -> RecordingZeptoGateway:
    recording = RecordingZeptoGateway(fail=True)
    service = _checkout_service(recording)
    override(get_zepto_checkout_service, lambda: service)
    confirmations = get_order_confirmations()
    confirmations.reconciler = CheckoutReconciler(
        DatabaseCheckoutAttemptRepository(get_session_factory()),
        confirmations.baskets,
        ZeptoService(object(), history),  # type: ignore[arg-type]
        follow_up_delays=(),
    )
    return recording


def test_check_with_zepto_settles_the_unknown_order_end_to_end(
    api: TestClient, timing_out: RecordingZeptoGateway, history: HistoryClient
) -> None:
    alice = as_user("alice")
    _ready_basket(api, alice, oil=1)
    event_ = _prepare("alice")
    payable = event_["confirmation"]["payable_paise"]

    tapped = _confirm(api, alice, event_["confirmation"]["id"])
    assert tapped.status_code == 202 and tapped.json()["status"] == "outcome_unknown"
    checkout_id = tapped.json()["checkout_id"]
    assert checkout_id
    # Outcome unknown keeps the basket locked.
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["status"] == "checkout_pending"

    nothing = api.post("/api/orders/check", headers=alice, json={"checkout_id": checkout_id})
    assert nothing.status_code == 202 and nothing.json()["check"] == "not_found"

    history.orders.clear()
    now = datetime.now(UTC)
    history.add("ord-live", CODE, _iso(now - timedelta(seconds=5)), total=payable, name=event_["confirmation"]["items"][0]["name"])
    get_order_confirmations().reconciler.min_interval = timedelta(0)  # type: ignore[union-attr]
    assert api.post("/api/orders/check", headers=as_user("bob"), json={"checkout_id": checkout_id}).status_code == 404
    settled = api.post("/api/orders/check", headers=alice, json={"checkout_id": checkout_id})
    assert settled.status_code == 200, settled.text
    body = settled.json()
    assert body["status"] == "confirmed" and body["provider_order_id"] == "ord-live"
    assert body["provider_order_code"] == CODE
    assert body["tracking"]["order"]["order_id"] == "ord-live"
    # The basket closed with the order; the next request starts a fresh basket.
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["lines"] == []
    # Re-checking is idempotent.
    repeat = api.post("/api/orders/check", headers=alice, json={"checkout_id": checkout_id})
    assert repeat.status_code == 200 and repeat.json()["provider_order_id"] == "ord-live"
    assert set(history.calls) <= READ_TOOLS and len(timing_out.calls) == 1


def test_app_load_reports_unknown_orders_and_settles_them(
    api: TestClient, timing_out: RecordingZeptoGateway, history: HistoryClient
) -> None:
    alice = as_user("alice")
    _ready_basket(api, alice, oil=1)
    event_ = _prepare("alice")
    payable = event_["confirmation"]["payable_paise"]
    assert _confirm(api, alice, event_["confirmation"]["id"]).status_code == 202

    pending = api.post("/api/orders/reconcile-pending", headers=alice)
    assert pending.status_code == 200
    [order] = pending.json()["orders"]
    assert order["status"] == "outcome_unknown" and order["check"] == "not_found"
    assert api.post("/api/orders/reconcile-pending", headers=as_user("bob")).json() == {"orders": []}

    history.add("ord-live", CODE, _iso(datetime.now(UTC)), total=payable, name=event_["confirmation"]["items"][0]["name"])
    get_order_confirmations().reconciler.min_interval = timedelta(0)  # type: ignore[union-attr]
    [settled] = api.post("/api/orders/reconcile-pending", headers=alice).json()["orders"]
    assert settled["status"] == "confirmed" and settled["provider_order_code"] == CODE
    assert api.post("/api/orders/reconcile-pending", headers=alice).json() == {"orders": []}
    assert set(history.calls) <= READ_TOOLS and len(timing_out.calls) == 1


async def test_maintenance_dry_run_on_the_test_database_writes_nothing(session_factory, history: HistoryClient) -> None:
    import io

    from app.shopping.reconcile_unknown import run

    baskets, _, attempts, attempt, _ = await _unknown_attempt(session_factory)
    history.add("ord-live", CODE, _iso(datetime.now(UTC)), total=attempt.payable_paise, name="Fortune Sunflower Oil")
    out = io.StringIO()

    counts = await run(
        apply=False,
        attempts=attempts,
        baskets=baskets,
        history_factory=lambda: ZeptoService(object(), history),  # type: ignore[arg-type]
        out=out,
    )

    assert counts["matched"] == 1 and counts["applied"] == 0
    assert f"order_code={CODE}" in out.getvalue()
    stored = await attempts.get(attempt.id)
    assert stored is not None and stored.status == CheckoutStatus.OUTCOME_UNKNOWN


async def test_a_dispatching_attempt_cut_off_mid_order_becomes_unknown(
    session_factory, history: HistoryClient
) -> None:
    """A crash after the claim (before mark_unknown) must not strand the basket."""

    import uuid

    from app.shopping.checkout import idempotency_key_hash
    from app.shopping.models import CheckoutAttempt
    from app.shopping.reconciliation import STALE_DISPATCH

    baskets, approvals, approval_id = await _approved_zepto_basket(session_factory, "alice")
    approval = await approvals.get(approval_id)
    assert approval is not None
    basket = await baskets.get(approval.basket_id)
    assert basket is not None
    attempts = DatabaseCheckoutAttemptRepository(session_factory)
    started = datetime.now(UTC) - STALE_DISPATCH - timedelta(minutes=1)
    claimed, created = await attempts.claim(
        CheckoutAttempt(
            id=uuid.uuid4().hex,
            approval_id=approval_id,
            basket_id=basket.id,
            household_id="alice",
            idempotency_key_hash=idempotency_key_hash("key-crash"),
            payable_paise=approval.payable_paise,
            status=CheckoutStatus.DISPATCHING,
            created_at=started,
            payment_method="cod",
        ),
        basket_revision=basket.revision,
    )
    assert created and claimed.status == CheckoutStatus.DISPATCHING

    # Listing is read-only: the stale attempt is offered for checking, unchanged.
    pending = await attempts.unknown_attempts("alice")
    assert [item.id for item in pending] == [claimed.id]
    assert pending[0].status == CheckoutStatus.DISPATCHING

    reconciler = CheckoutReconciler(attempts, baskets, ZeptoService(object(), history))  # type: ignore[arg-type]
    dry = await reconciler.reconcile(claimed.id, apply=False)
    assert dry.check == "not_found"
    stored = await attempts.get(claimed.id)
    assert stored is not None and stored.status == CheckoutStatus.DISPATCHING

    checked = await reconciler.reconcile(claimed.id)
    assert checked.check == "not_found"
    stored = await attempts.get(claimed.id)
    assert stored is not None and stored.status == CheckoutStatus.OUTCOME_UNKNOWN
    assert stored.completed_at == started + STALE_DISPATCH
