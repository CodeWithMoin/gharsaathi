"""Order tracking after a placed order: auto-monitor, status steps, notifications,
the read-only voice/typed status tools and live-catalogue cancel detection.

Fakes only: a fake Zepto MCP client, fake tracking adapters and an on-disk
tracking store under tmp_path. No provider, OpenAI call, order or cancellation
is possible here.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.orders import _start_tracking
from app.commerce.zepto.errors import ZeptoAuthenticationRequired
from app.commerce.zepto.history import parse_order_history
from app.shopping.order_confirmation import OrderResult
from app.tracking.adapters import ZeptoOrderStatusAdapter
from app.tracking.cancellation import is_order_cancel_tool, order_cancel_tool
from app.tracking.errors import TrackingProviderError
from app.tracking.models import (
    StartTrackingRequest,
    TrackingProvider,
    TrackingSnapshot,
    TrackingStatus,
)
from app.tracking.notifications import meaningful_update
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore
from app.voice.order_status_tools import (
    ORDER_STATUS_TOOL_DEFINITIONS,
    OrderStatusToolExecutor,
)
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
START = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = START

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: int) -> None:
        self.value += timedelta(**delta)


class FakeZeptoStatus:
    """Read-only tracking adapter that replays scripted provider snapshots."""

    provider = TrackingProvider.ZEPTO

    def __init__(self, snapshots: list[TrackingSnapshot | TrackingProviderError]) -> None:
        self.snapshots = deque(snapshots)
        self.calls: list[str] = []

    async def fetch_delivery_status(self, *, order_id: str, address_id: str | None) -> TrackingSnapshot:
        self.calls.append(order_id)
        item = self.snapshots[0] if len(self.snapshots) == 1 else self.snapshots.popleft()
        if isinstance(item, TrackingProviderError):
            raise item
        return item


def snap(status: TrackingStatus, *, eta: int | None = None, poll: int = 60, label: str | None = None, **kwargs: Any) -> TrackingSnapshot:
    return TrackingSnapshot(
        status=status,
        provider_status=label,
        eta_at=START + timedelta(minutes=eta) if eta is not None else None,
        poll_interval_seconds=poll,
        delivered=status == TrackingStatus.DELIVERED,
        cancelled=status == TrackingStatus.CANCELLED,
        **kwargs,
    )


def service(tmp_path: Path, adapter: FakeZeptoStatus, clock: Clock) -> OrderTrackingService:
    return OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.ZEPTO: adapter},
        now=clock,
        jitter=lambda low, high: low,
    )


def zepto(order_id: str) -> StartTrackingRequest:
    return StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id=order_id)


# --- Auto-monitor on a confirmed order -------------------------------------------


def _request(tracking: OrderTrackingService | None) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(order_tracking_service=tracking)))


def _confirmed(order_id: str | None = "ZEP-9", status: str = "confirmed") -> OrderResult:
    return OrderResult("c" * 12, status, "Zepto", 22200, provider_order_id=order_id)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_confirmed_order_starts_one_monitor_and_replays_reuse_it(tmp_path: Path) -> None:
    clock = Clock()
    adapter = FakeZeptoStatus([snap(TrackingStatus.CONFIRMED, eta=15, label="Order placed")])
    tracking = service(tmp_path, adapter, clock)

    first = await _start_tracking(_request(tracking), "alice", _confirmed())
    second = await _start_tracking(_request(tracking), "alice", _confirmed())
    third = await tracking.ensure("alice", zepto("ZEP-9"))

    assert first is not None and second is not None
    assert first.monitor_id == second.monitor_id == third.monitor_id
    assert first.order.status == TrackingStatus.CONFIRMED
    # One immediate read-only status read; replays never re-read inside the interval.
    assert adapter.calls == ["ZEP-9"]
    assert [watch.id for watch in tracking.list("alice")] == [first.monitor_id]
    assert tracking.list("bob") == []


@pytest.mark.asyncio
async def test_no_monitor_without_a_provider_confirmed_order_and_failures_never_fail_the_tap(tmp_path: Path) -> None:
    clock = Clock()
    adapter = FakeZeptoStatus([snap(TrackingStatus.CONFIRMED)])
    tracking = service(tmp_path, adapter, clock)

    assert await _start_tracking(_request(tracking), "alice", _confirmed(status="outcome_unknown", order_id=None)) is None
    assert await _start_tracking(_request(tracking), "alice", _confirmed(order_id=None)) is None
    assert await _start_tracking(_request(None), "alice", _confirmed()) is None
    assert adapter.calls == [] and tracking.list("alice") == []

    class Broken(OrderTrackingService):
        async def ensure(self, user_id: str, request: StartTrackingRequest):  # type: ignore[override]
            raise RuntimeError("disk full")

    broken = Broken(OrderTrackingStore(tmp_path / "broken"), {})
    assert await _start_tracking(_request(broken), "alice", _confirmed()) is None


# --- Status card transitions from provider responses ------------------------------


def _detail(status: str, formatted: str, *, eta: int = 15) -> dict[str, Any]:
    return {
        "id": "internal-1",
        "orderId": "ZEP-9",
        "code": "Z123",
        "formattedStatus": formatted,
        "orderType": "INSTANT",
        "placedTime": "2026-11-22T18:00:00Z",
        "userAddressFormatted": "Home",
        "itemQuantityCount": 2,
        "grandTotalAmount": 22200,
        "grandTotalAmountPreDiscount": 23000,
        "totalRefundAmount": 0,
        "walletBalanceUsed": 0,
        "isInvoiceAvailable": False,
        "invoiceUrl": None,
        "isEligibleForRating": False,
        "billSummary": {"itemTotal": 22200, "totalBill": 22200, "totalSaved": 800},
        "shipments": [
            {
                "shipmentId": "s1",
                "shipmentCode": "S1",
                "status": status,
                "formattedStatus": formatted,
                "placedTime": "2026-11-22T18:00:00Z",
                "deliveryTime": None,
                "arrivedTime": None,
                "committedEtaInMins": eta,
                "totalArrivalTimeInMinutes": None,
                "shipmentWeightInGms": 900,
                "products": [],
            }
        ],
    }


class FakeZeptoClient:
    """A fake Zepto MCP session: a fixed catalogue and scripted get_order_detail payloads."""

    def __init__(self, tools: list[Any], details: list[dict[str, Any]]) -> None:
        self.tools = tools
        self.details = deque(details)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return self.details[0] if len(self.details) == 1 else self.details.popleft()


def _catalogue(extra: list[dict[str, Any]] | None = None) -> list[Any]:
    tools = json.loads((FIXTURES / "zepto_tools_catalogue_2026-11-21.json").read_text())
    return [SimpleNamespace(**tool) for tool in tools + (extra or [])]


@pytest.mark.asyncio
async def test_zepto_detail_drives_placed_packed_out_for_delivery_delivered(tmp_path: Path) -> None:
    client = FakeZeptoClient(
        _catalogue(),
        [
            _detail("ORDER_PLACED", "Order placed"),
            _detail("PACKED", "Packed"),
            _detail("OUT_FOR_DELIVERY", "Out for delivery"),
            _detail("DELIVERED", "Delivered"),
        ],
    )
    clock = Clock()
    tracking = OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.ZEPTO: ZeptoOrderStatusAdapter(client)},  # type: ignore[arg-type]
        now=clock,
    )
    read = await tracking.ensure("alice", zepto("ZEP-9"))
    steps = [read.order.status]
    assert read.order.snapshot is not None
    assert read.order.snapshot.order_code == "Z123"
    assert read.order.snapshot.eta_at == START + timedelta(minutes=15)  # Zepto's committed ETA
    assert read.order.snapshot.poll_interval_seconds == ZeptoOrderStatusAdapter.DETAIL_POLL_INTERVAL_SECONDS
    while read.order.active:
        clock.advance(seconds=ZeptoOrderStatusAdapter.DETAIL_POLL_INTERVAL_SECONDS)
        read = await tracking.refresh("alice", read.monitor_id)
        steps.append(read.order.status)

    assert steps == [
        TrackingStatus.CONFIRMED,
        TrackingStatus.PREPARING,
        TrackingStatus.OUT_FOR_DELIVERY,
        TrackingStatus.DELIVERED,
    ]
    assert read.order.stop_reason == "terminal_delivered" and read.order.snapshot.eta_at is None
    # Only the documented read-only detail tool was called; no cancel, cart or order tool.
    assert {name for name, _ in client.calls} == {"get_order_detail"}
    assert [kind.kind for kind in read.order.notifications] == ["out_for_delivery", "delivered"]


@pytest.mark.asyncio
async def test_unknown_provider_status_is_kept_as_text_never_guessed(tmp_path: Path) -> None:
    client = FakeZeptoClient(_catalogue(), [_detail("RIDER_ASSIGNED_V2", "Rider assigned")])
    tracking = OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.ZEPTO: ZeptoOrderStatusAdapter(client)},  # type: ignore[arg-type]
        now=Clock(),
    )
    read = await tracking.ensure("alice", zepto("ZEP-9"))
    assert read.order.status == TrackingStatus.UNKNOWN
    assert read.order.snapshot is not None and read.order.snapshot.provider_status == "Rider assigned"


@pytest.mark.asyncio
async def test_store_cancelled_order_stops_tracking_and_notifies_once(tmp_path: Path) -> None:
    clock = Clock()
    adapter = FakeZeptoStatus([snap(TrackingStatus.CONFIRMED, eta=15), snap(TrackingStatus.CANCELLED, label="Cancelled by store")])
    tracking = service(tmp_path, adapter, clock)
    read = await tracking.ensure("alice", zepto("ZEP-9"))
    clock.advance(seconds=60)
    read = await tracking.refresh("alice", read.monitor_id)
    assert read.order.status == TrackingStatus.CANCELLED and read.order.active is False
    assert [n.kind for n in read.order.notifications] == ["cancelled"]
    assert read.order.notifications[0].message == "Your Zepto order was cancelled."


# --- Notification diffing ----------------------------------------------------------


def test_only_meaningful_changes_notify() -> None:
    placed = snap(TrackingStatus.CONFIRMED, eta=15)
    packed = snap(TrackingStatus.PREPARING, eta=15, label="Packing")
    out = snap(TrackingStatus.OUT_FOR_DELIVERY, eta=15)

    assert meaningful_update(None, out) is None  # a first reading is not news
    assert meaningful_update(placed, packed) is None  # packing is shown on the card only
    assert meaningful_update(packed, out) == "out_for_delivery"
    assert meaningful_update(out, out) is None
    assert meaningful_update(out, snap(TrackingStatus.OUT_FOR_DELIVERY, eta=24)) is None  # 9 min drift
    assert meaningful_update(out, snap(TrackingStatus.OUT_FOR_DELIVERY, eta=25)) == "delayed"
    assert meaningful_update(out, snap(TrackingStatus.OUT_FOR_DELIVERY, eta=5)) is None  # earlier is fine
    assert meaningful_update(out, snap(TrackingStatus.OUT_FOR_DELIVERY, eta=None)) is None
    assert meaningful_update(out, snap(TrackingStatus.DELIVERED)) == "delivered"
    assert meaningful_update(placed, snap(TrackingStatus.CANCELLED)) == "cancelled"
    assert meaningful_update(snap(TrackingStatus.DELIVERED), snap(TrackingStatus.DELIVERED)) is None


@pytest.mark.asyncio
async def test_repeated_reads_never_duplicate_a_notification(tmp_path: Path) -> None:
    clock = Clock()
    adapter = FakeZeptoStatus([
        snap(TrackingStatus.PREPARING, eta=15),
        snap(TrackingStatus.OUT_FOR_DELIVERY, eta=15),
        snap(TrackingStatus.OUT_FOR_DELIVERY, eta=15),
        snap(TrackingStatus.OUT_FOR_DELIVERY, eta=27),
        snap(TrackingStatus.OUT_FOR_DELIVERY, eta=29),
    ])
    tracking = service(tmp_path, adapter, clock)
    read = await tracking.ensure("alice", zepto("ZEP-9"))
    for _ in range(4):
        clock.advance(seconds=60)
        read = await tracking.refresh("alice", read.monitor_id)
    assert [n.kind for n in read.order.notifications] == ["out_for_delivery", "delayed"]
    assert len({n.id for n in read.order.notifications}) == 2


# --- Voice/typed status tools ------------------------------------------------------


class FakeHistory:
    def __init__(self, orders: list[dict[str, Any]] | None = None, *, tools: list[Any] | None = None, error: Exception | None = None) -> None:
        self.orders = orders or []
        self.tools = tools if tools is not None else _catalogue()
        self.error = error
        self.calls: list[tuple[int, int]] = []

    async def order_history(self, *, limit: int = 10, page_number: int = 1):  # type: ignore[no-untyped-def]
        self.calls.append((limit, page_number))
        if self.error is not None:
            raise self.error
        return parse_order_history(
            {"count": len(self.orders), "orders": self.orders[:limit]}, limit=limit, page_number=page_number
        )

    async def list_tools(self) -> list[Any]:
        return self.tools


def _history_order(code: str, status: str = "Delivered") -> dict[str, Any]:
    return {
        "id": f"id-{code}",
        "code": code,
        "formattedStatus": status,
        "grandTotalAmount": 22200,
        "itemQuantityCount": 2,
        "productsNamesAndCounts": [{"name": "Amul Taaza 1 L", "count": 2}],
        "totalArrivalTimeInMinutes": 12,
        "totalRefundAmount": 0,
    }


async def _call(executor: OrderStatusToolExecutor, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return (await executor.execute(name, json.dumps(arguments))).model_output


@pytest.mark.asyncio
async def test_status_tool_answers_from_the_households_own_monitor(tmp_path: Path) -> None:
    clock = Clock()
    adapter = FakeZeptoStatus([snap(TrackingStatus.OUT_FOR_DELIVERY, eta=6, label="Out for delivery", order_code="Z123")])
    tracking = service(tmp_path, adapter, clock)
    await tracking.ensure("alice", zepto("ZEP-9"))
    history = FakeHistory([_history_order("Z999")])

    alice = OrderStatusToolExecutor(tracking, history, "alice", now=clock)
    result = await _call(alice, "get_order_status", {"order_ref": "latest"})
    assert result["status"] == "ok" and result["source"] == "live_tracking"
    assert result["order_status"] == "out_for_delivery"
    assert result["order_status_label"] == "Out for delivery"
    assert result["eta_minutes"] == 6
    assert result["stale"] is False and result["last_updated_minutes_ago"] == 0
    assert result["cancel_here"] is False and result["cancel_supported"] is False
    assert "Zepto app" in result["note"]
    assert (await _call(alice, "get_order_status", {"order_ref": "#Z123"}))["order_ref"] == "ZEP-9"
    assert history.calls == []  # a monitor answers without reading history

    # Another household never sees alice's monitor; with no history match it gets no_order.
    bob = OrderStatusToolExecutor(tracking, FakeHistory([]), "bob", now=clock)
    assert (await _call(bob, "get_order_status", {"order_ref": "latest"}))["status"] == "no_order"
    assert (await _call(bob, "get_order_status", {"order_ref": "ZEP-9"}))["status"] == "no_order"


@pytest.mark.asyncio
async def test_stale_status_reports_when_it_was_last_updated(tmp_path: Path) -> None:
    clock = Clock()
    adapter = FakeZeptoStatus([
        snap(TrackingStatus.PREPARING, eta=15, label="Packing"),
        TrackingProviderError("upstream 503"),
    ])
    tracking = service(tmp_path, adapter, clock)
    await tracking.ensure("alice", zepto("ZEP-9"))
    clock.advance(minutes=12)

    result = await _call(OrderStatusToolExecutor(tracking, None, "alice", now=clock), "get_order_status", {"order_ref": "latest"})
    assert result["order_status"] == "preparing"
    assert result["stale"] is True and result["last_updated_minutes_ago"] == 12
    assert result["eta_minutes"] == 3


@pytest.mark.asyncio
async def test_status_tool_never_guesses_an_unreported_status_or_time(tmp_path: Path) -> None:
    clock = Clock()
    tracking = service(tmp_path, FakeZeptoStatus([snap(TrackingStatus.UNKNOWN)]), clock)
    await tracking.ensure("alice", zepto("ZEP-9"))
    result = await _call(OrderStatusToolExecutor(tracking, None, "alice", now=clock), "get_order_status", {"order_ref": "latest"})
    assert result["order_status"] == "unknown"
    assert "order_status_label" not in result and "eta_minutes" not in result

    # An ETA in the past is reported as passed, never as a made-up remaining time.
    late = service(tmp_path / "late", FakeZeptoStatus([snap(TrackingStatus.OUT_FOR_DELIVERY, eta=5)]), clock)
    await late.ensure("alice", zepto("ZEP-10"))
    clock.advance(minutes=8)
    result = await _call(OrderStatusToolExecutor(late, None, "alice", now=clock), "get_order_status", {"order_ref": "latest"})
    assert "eta_minutes" not in result and result["eta_passed"] is True


@pytest.mark.asyncio
async def test_without_a_monitor_history_status_is_marked_and_has_no_eta(tmp_path: Path) -> None:
    tracking = service(tmp_path, FakeZeptoStatus([snap(TrackingStatus.CONFIRMED)]), Clock())
    result = await _call(
        OrderStatusToolExecutor(tracking, FakeHistory([_history_order("Z7", "Delivered")]), "alice"),
        "get_order_status",
        {"order_ref": "latest"},
    )
    assert result["source"] == "order_history" and result["order_code"] == "Z7"
    assert result["order_status"] == "delivered" and "eta_minutes" not in result
    assert result["tracking"] == "none"

    not_connected = OrderStatusToolExecutor(tracking, FakeHistory(error=ZeptoAuthenticationRequired("x")), "alice")
    assert (await _call(not_connected, "get_order_status", {"order_ref": "latest"}))["status"] == "not_connected"
    assert (await _call(OrderStatusToolExecutor(None, None, "alice"), "get_order_status", {"order_ref": "latest"}))["status"] == "no_order"


@pytest.mark.asyncio
async def test_recent_orders_are_capped_at_five_and_read_only() -> None:
    history = FakeHistory([_history_order(f"Z{index}") for index in range(8)])
    executor = OrderStatusToolExecutor(None, history, "alice")

    result = await _call(executor, "list_recent_orders", {"limit": 50})
    assert len(result["orders"]) == 5 and history.calls == [(5, 1)]
    assert result["orders"][0] == {
        "order_ref": "id-Z0",
        "order_code": "Z0",
        "order_status": "Delivered",
        "item_count": 2,
        "items": [{"name": "Amul Taaza 1 L", "quantity": 2}],
        "total_paise": 22200,
        "total": "₹222",
    }
    assert (await _call(executor, "list_recent_orders", {"limit": 0}))["status"] == "error"
    assert (await _call(executor, "list_recent_orders", {"limit": 2, "extra": 1}))["status"] == "error"
    assert (await _call(OrderStatusToolExecutor(None, FakeHistory([]), "alice"), "list_recent_orders", {"limit": 3}))["status"] == "no_orders"


def test_status_tools_are_strict_read_only_and_there_is_no_cancel_tool() -> None:
    for tool in ORDER_STATUS_TOOL_DEFINITIONS:
        assert tool["strict"] is True
        assert tool["parameters"]["additionalProperties"] is False
        assert "Read-only" in tool["description"]
    names = {tool["name"] for tool in VOICE_TOOL_DEFINITIONS}
    assert {"get_order_status", "list_recent_orders"} <= names
    assert not any(is_order_cancel_tool(name) for name in names)


# --- Cancellation requests and live-catalogue detection -----------------------------


def test_documented_zepto_catalogue_has_no_cancel_tool() -> None:
    assert order_cancel_tool(_catalogue()) is None


def test_cancel_detection_needs_a_clear_order_cancel_tool() -> None:
    order_schema = {"type": "object", "properties": {"orderId": {"type": "string"}}}
    assert is_order_cancel_tool("cancel_order")
    assert is_order_cancel_tool("cancelOrder")
    assert is_order_cancel_tool("order_cancellation")
    assert is_order_cancel_tool("cancel", order_schema)
    assert not is_order_cancel_tool("cancel")
    assert not is_order_cancel_tool("cancel_payment", order_schema)
    assert not is_order_cancel_tool("get_order_detail", order_schema)
    assert not is_order_cancel_tool("create_order")


@pytest.mark.asyncio
async def test_live_cancel_tool_is_reported_but_never_called(tmp_path: Path) -> None:
    cancel_tool = {"name": "cancel_order", "input_schema": {"type": "object", "properties": {"orderId": {"type": "string"}}}}
    client = FakeZeptoClient(_catalogue([cancel_tool]), [_detail("PACKED", "Packed")])
    clock = Clock()
    tracking = OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.ZEPTO: ZeptoOrderStatusAdapter(client)},  # type: ignore[arg-type]
        now=clock,
    )
    await tracking.ensure("alice", zepto("ZEP-9"))
    result = await _call(
        OrderStatusToolExecutor(tracking, FakeHistory(tools=client.tools), "alice", now=clock),
        "get_order_status",
        {"order_ref": "latest"},
    )
    assert result["cancel_supported"] is True and result["cancel_here"] is False
    assert [name for name, _ in client.calls] == ["get_order_detail"]

    # History fallback checks the live catalogue too.
    history = FakeHistory([_history_order("Z1", "Packed")], tools=client.tools)
    fallback = await _call(OrderStatusToolExecutor(None, history, "alice"), "get_order_status", {"order_ref": "Z1"})
    assert fallback["cancel_supported"] is True and fallback["cancel_here"] is False


@pytest.mark.asyncio
async def test_cancel_request_answer_points_to_the_zepto_app(tmp_path: Path) -> None:
    from app.voice.prompts import BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS

    reply = "Cancel yahan se nahi hota; Zepto app mein order kholkar jaldi Cancel dabaiye, packing se pehle."
    assert reply.replace("\n", " ") in " ".join(LIVE_INSTRUCTIONS.split())
    assert reply in " ".join(BACKEND_INSTRUCTIONS.split())
    tracking = service(tmp_path, FakeZeptoStatus([snap(TrackingStatus.PREPARING)]), Clock())
    await tracking.ensure("alice", zepto("ZEP-9"))
    result = await _call(OrderStatusToolExecutor(tracking, None, "alice"), "get_order_status", {"order_ref": "latest"})
    assert result["cancel_here"] is False and "tap Cancel" in result["note"]
    # Payment: only cash on delivery is promised.
    assert "only cash on delivery" in " ".join(BACKEND_INSTRUCTIONS.split())
    assert "Never promise UPI" in " ".join(LIVE_INSTRUCTIONS.split())
