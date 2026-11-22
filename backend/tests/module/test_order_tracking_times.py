"""Store-reported order times: placed, estimate, arrived and delivered.

Fakes only: a fake Zepto MCP client replaying ``get_order_detail`` payloads
shaped like the owner's real delivered order, and a tracking store under
tmp_path. No provider call, order or cancellation is possible here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.tracking.adapters import ZeptoOrderStatusAdapter
from app.tracking.models import (
    OrderWatch,
    StartTrackingRequest,
    TrackingProvider,
    TrackingSnapshot,
    TrackingStatus,
)
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore
from app.voice.order_status_tools import OrderStatusToolExecutor

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PLACED = datetime(2026, 11, 22, 2, 48, 10, tzinfo=UTC)


def _shipment(**overrides: Any) -> dict[str, Any]:
    shipment = {
        "shipmentId": "s1",
        "shipmentCode": "S1",
        "status": "DELIVERED",
        "formattedStatus": "Delivered",
        "placedTime": "2026-11-22T02:48:10Z",
        "deliveryTime": "2026-11-22T02:56:59Z",
        "arrivedTime": "2026-11-22T02:55:10Z",
        "committedEtaInMins": 8,
        "totalArrivalTimeInMinutes": 7,
        "shipmentWeightInGms": 900,
        "products": [],
    }
    shipment.update(overrides)
    return {key: value for key, value in shipment.items() if value is not ...}


def _detail(shipment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "internal-1",
        "orderId": "UIIMJHTCC04881",
        "code": "UIIMJHTCC04881",
        "formattedStatus": shipment["formattedStatus"],
        "orderType": "INSTANT",
        "placedTime": "2026-11-22T02:48:10Z",
        "userAddressFormatted": "Home",
        "itemQuantityCount": 1,
        "grandTotalAmount": 5500,
        "grandTotalAmountPreDiscount": 5500,
        "totalRefundAmount": 0,
        "walletBalanceUsed": 0,
        "isInvoiceAvailable": False,
        "invoiceUrl": None,
        "isEligibleForRating": False,
        "billSummary": {"itemTotal": 5500, "totalBill": 5500, "totalSaved": 0},
        "shipments": [shipment],
    }


class FakeZeptoClient:
    def __init__(self, details: list[dict[str, Any]]) -> None:
        tools = json.loads((FIXTURES / "zepto_tools_catalogue_2026-11-21.json").read_text())
        self.tools = [SimpleNamespace(**tool) for tool in tools]
        self.details = list(details)
        self.calls: list[str] = []

    async def list_tools(self) -> list[Any]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(name)
        return self.details.pop(0) if len(self.details) > 1 else self.details[0]


async def _snapshot(shipment: dict[str, Any]) -> TrackingSnapshot:
    adapter = ZeptoOrderStatusAdapter(FakeZeptoClient([_detail(shipment)]))  # type: ignore[arg-type]
    return await adapter.fetch_delivery_status(order_id="UIIMJHTCC04881", address_id=None)


@pytest.mark.asyncio
async def test_real_delivered_shipment_keeps_zepto_times() -> None:
    snapshot = await _snapshot(_shipment())
    assert snapshot.status == TrackingStatus.DELIVERED
    assert snapshot.placed_at == PLACED
    assert snapshot.arrived_at == datetime(2026, 11, 22, 2, 55, 10, tzinfo=UTC)
    assert snapshot.delivered_at == datetime(2026, 11, 22, 2, 56, 59, tzinfo=UTC)
    assert snapshot.took_minutes == 9  # 8 min 49 s
    assert snapshot.committed_eta_minutes == 8
    assert snapshot.total_arrival_minutes == 7
    assert snapshot.eta_at is None  # no estimate is shown once delivered


@pytest.mark.asyncio
async def test_in_transit_eta_is_placed_plus_zeptos_estimate() -> None:
    snapshot = await _snapshot(
        _shipment(status="OUT_FOR_DELIVERY", formattedStatus="Out for delivery", deliveryTime=None, arrivedTime=None, totalArrivalTimeInMinutes=None)
    )
    assert snapshot.status == TrackingStatus.OUT_FOR_DELIVERY
    assert snapshot.eta_at == PLACED + timedelta(minutes=8)
    assert snapshot.placed_at == PLACED
    assert snapshot.delivered_at is None and snapshot.took_minutes is None
    assert snapshot.arrived_at is None and snapshot.total_arrival_minutes is None


@pytest.mark.asyncio
async def test_missing_or_unreadable_times_stay_empty() -> None:
    absent = await _snapshot(
        _shipment(deliveryTime=..., arrivedTime=..., totalArrivalTimeInMinutes=...)
    )
    assert absent.status == TrackingStatus.DELIVERED
    assert absent.delivered_at is None and absent.arrived_at is None and absent.took_minutes is None
    assert absent.total_arrival_minutes is None

    unreadable = await _snapshot(
        _shipment(placedTime="soon", deliveryTime="2026-11-22 02:56", committedEtaInMins=0)
    )
    assert unreadable.placed_at is None and unreadable.eta_at is None
    assert unreadable.committed_eta_minutes is None and unreadable.took_minutes is None
    # A time without a zone is never assumed to be UTC or India time.
    assert unreadable.delivered_at is None

    moving = await _snapshot(_shipment(status="PACKED", formattedStatus="Packed", placedTime="bad"))
    assert moving.eta_at is None and moving.placed_at is None
    # A shipment that is not delivered never gets a delivered time.
    assert moving.delivered_at is None


def _clock(value: datetime):
    state = {"now": value}

    def now() -> datetime:
        return state["now"]

    return state, now


@pytest.mark.asyncio
async def test_delivered_watch_reports_zeptos_delivery_time_not_our_check(tmp_path: Path) -> None:
    _, now = _clock(datetime(2026, 11, 22, 3, 28, tzinfo=UTC))
    client = FakeZeptoClient([_detail(_shipment())])
    tracking = OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.ZEPTO: ZeptoOrderStatusAdapter(client)},  # type: ignore[arg-type]
        now=now,
    )
    read = await tracking.ensure(
        "alice", StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id="UIIMJHTCC04881")
    )
    assert read.order.status_at == datetime(2026, 11, 22, 2, 56, 59, tzinfo=UTC)
    assert read.order.checked_at == datetime(2026, 11, 22, 3, 28, tzinfo=UTC)
    saved = json.loads(read.model_dump_json())["order"]["snapshot"]
    assert saved["took_minutes"] == 9 and saved["delivered_at"] == "2026-11-22T02:56:59Z"

    result = (await OrderStatusToolExecutor(tracking, None, "alice", now=now).execute(
        "get_order_status", json.dumps({"order_ref": "latest"})
    )).model_output
    assert result["order_status"] == "delivered"
    assert result["delivered_at"] == "2026-11-22T02:56:59+00:00"
    assert result["delivered_time_india"] == "8:26 am"
    assert result["placed_time_india"] == "8:18 am"
    assert result["took_minutes"] == 9 and result["estimate_minutes"] == 8
    assert result["delivered_minutes_ago"] == 31
    assert "eta_minutes" not in result and "eta_at" not in result
    assert result["stale"] is False


@pytest.mark.asyncio
async def test_in_transit_status_tool_gives_minutes_left_and_clock_time(tmp_path: Path) -> None:
    state, now = _clock(PLACED + timedelta(minutes=4))
    client = FakeZeptoClient([
        _detail(_shipment(status="OUT_FOR_DELIVERY", formattedStatus="Out for delivery", deliveryTime=None, arrivedTime=None))
    ])
    tracking = OrderTrackingService(
        OrderTrackingStore(tmp_path / "tracking"),
        {TrackingProvider.ZEPTO: ZeptoOrderStatusAdapter(client)},  # type: ignore[arg-type]
        now=now,
    )
    await tracking.ensure(
        "alice", StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id="UIIMJHTCC04881")
    )
    executor = OrderStatusToolExecutor(tracking, None, "alice", now=now)
    result = (await executor.execute("get_order_status", json.dumps({"order_ref": "latest"}))).model_output
    assert result["eta_minutes"] == 4
    assert result["eta_at"] == "2026-11-22T02:56:10+00:00"
    assert result["eta_time_india"] == "8:26 am"
    assert result["placed_at"] == "2026-11-22T02:48:10+00:00"
    assert "delivered_at" not in result and "took_minutes" not in result

    state["now"] = PLACED + timedelta(minutes=10)
    late = (await executor.execute("get_order_status", json.dumps({"order_ref": "latest"}))).model_output
    assert late["eta_passed"] is True and "eta_minutes" not in late
    assert late["eta_time_india"] == "8:26 am"


def test_old_watch_files_without_the_new_times_still_load(tmp_path: Path) -> None:
    store = OrderTrackingStore(tmp_path)
    old = {
        "id": "8d4f5a52-31cf-4b2b-9d0b-1c3e2f4a5b6c",
        "user_id": "alice",
        "provider": "zepto",
        "order_id": "UIIMJHTCC04881",
        "address_id": None,
        "active": False,
        "status": "delivered",
        "snapshot": {
            "status": "delivered",
            "provider_status": "Delivered",
            "message": None,
            "eta_at": None,
            "poll_interval_seconds": 90,
            "delivered": True,
            "cancelled": False,
            "disabled": False,
            "order_code": "UIIMJHTCC04881",
            "cancel_supported": False,
        },
        "last_fingerprint": "delivered|delivered||||delivered||",
        "events": [],
        "notifications": [],
        "consecutive_failures": 0,
        "last_error": None,
        "checked_at": "2026-11-22T03:28:00Z",
        "status_at": "2026-11-22T03:28:00Z",
        "next_check_at": None,
        "stopped_at": "2026-11-22T03:28:00Z",
        "stop_reason": "terminal_delivered",
        "created_at": "2026-11-22T03:28:00Z",
        "updated_at": "2026-11-22T03:28:00Z",
    }
    path = store._path("alice", TrackingProvider.ZEPTO, "UIIMJHTCC04881")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old), encoding="utf-8")

    watch = store.get("alice", TrackingProvider.ZEPTO, "UIIMJHTCC04881")
    assert isinstance(watch, OrderWatch) and watch.snapshot is not None
    assert watch.snapshot.placed_at is None and watch.snapshot.delivered_at is None
    assert watch.snapshot.took_minutes is None and watch.snapshot.committed_eta_minutes is None
    assert [item.id for item in store.list("alice")] == [old["id"]]
    # Saving and reading back keeps it loadable (took_minutes is derived, not stored state).
    store.save(watch)
    assert store.get("alice", TrackingProvider.ZEPTO, "UIIMJHTCC04881").snapshot == watch.snapshot
