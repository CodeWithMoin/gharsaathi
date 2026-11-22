"""Read-only Swiggy Instamart order history.

``get_orders`` only returns orders from the last 15 days (per its live tool
description), newest first, ``count`` up to 20, with ``data.nextCursor`` for
the next older page. Import reads at most two pages per sync, at least two
seconds apart, honours the client's known rate-limit cooldown and never
retries. History older than 15 days accumulates only through repeated syncs.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from typing import Any

from pydantic import ValidationError

from app.commerce.swiggy.adapter import _data, _optional_str, _required_str
from app.commerce.swiggy.client import SwiggyMcpClient, _rate_limit_error
from app.commerce.swiggy.errors import SwiggySchemaError
from app.commerce.swiggy.models import OrderItem, OrderList, OrderSummary
from app.memory.history_reading import (
    CallPacer,
    HistoryRead,
    HistoryReadPlan,
    stop_reason_for,
)


ORDER_TYPE = "INSTAMART"
PAGE_COUNT = 20
MAX_PAGES = 2
MIN_CALL_INTERVAL_SECONDS = 2.0


class SwiggyOrderHistoryAdapter:
    """Read-only adapter for the authenticated user's recent Instamart orders."""

    def __init__(
        self,
        client: SwiggyMcpClient,
        *,
        max_pages: int = MAX_PAGES,
        min_interval: float = MIN_CALL_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.max_pages = max_pages
        self.pacer = CallPacer(min_interval, monotonic=monotonic, sleep=sleep)

    async def orders(self, count: int = 10, active_only: bool = False) -> OrderList:
        arguments: dict[str, object] = {"orderType": ORDER_TYPE, "count": count}
        if active_only:
            arguments["activeOnly"] = True
        return parse_orders(await self.client.call_tool("get_orders", arguments))

    async def read_history(self, plan: HistoryReadPlan) -> HistoryRead:
        read = HistoryRead(coverage="last_15_days")
        cursor: str | None = None
        for _ in range(self.max_pages):
            cooldown = getattr(self.client, "cooldown_remaining", None)
            remaining = await cooldown() if cooldown is not None else 0
            if remaining > 0:
                read.error = _rate_limit_error(remaining)
                read.stopped_reason = "rate_limited"
                return read
            await self.pacer.wait()
            arguments: dict[str, object] = {"orderType": ORDER_TYPE, "count": PAGE_COUNT}
            if cursor:
                arguments["cursor"] = cursor
            read.list_calls += 1
            try:
                page = parse_orders(await self.client.call_tool("get_orders", arguments))
            except Exception as exc:  # noqa: BLE001 - handed back, never retried
                read.error = exc
                read.stopped_reason = stop_reason_for(exc)
                return read
            for order in page.orders:
                if order.created_at < plan.cutoff:
                    read.stopped_reason = plan.cutoff_reason
                    return read
                if order.order_id in plan.known_ids:
                    read.skipped_known += 1
                read.orders.append(order)
            cursor = page.next_cursor
            if not cursor:
                read.stopped_reason = "complete"
                return read
        read.stopped_reason = "page_limit"
        return read


def parse_orders(payload: dict[str, Any]) -> OrderList:
    data = _data(payload)
    raw_orders = data.get("orders")
    if not isinstance(raw_orders, list):
        raise SwiggySchemaError("Swiggy returned an invalid orders list.")

    orders: list[OrderSummary] = []
    for raw in raw_orders:
        if not isinstance(raw, dict):
            raise SwiggySchemaError("Swiggy returned an invalid order.")
        raw_items = raw.get("items")
        if not isinstance(raw_items, list):
            raise SwiggySchemaError("A Swiggy order is missing items.")
        items = [_parse_item(item) for item in raw_items]
        if not isinstance(raw.get("isActive"), bool):
            raise SwiggySchemaError("A Swiggy order is missing isActive.")
        try:
            orders.append(
                OrderSummary(
                    order_id=_required_str(raw, "orderId"),
                    status=_required_str(raw, "status"),
                    current_status=_required_str(raw, "currentStatus"),
                    history_status=_required_str(raw, "historyStatus"),
                    status_message=_optional_str(raw.get("statusMessage")),
                    created_at=_timestamp(raw, "createdAt"),
                    updated_at=_timestamp(raw, "updatedAt"),
                    estimated_delivery_time=_optional_str(
                        raw.get("estimatedDeliveryTime")
                    ),
                    item_count=_strict_integer(raw, "itemCount", minimum=0),
                    total_paise=_rupees_to_paise(raw.get("totalAmount")),
                    payment_method=_optional_str(raw.get("paymentMethod")),
                    payment_status=_optional_str(raw.get("paymentStatus")),
                    refund_status=_optional_str(raw.get("refundStatus")),
                    order_type=_required_str(raw, "orderType"),
                    is_active=raw["isActive"],
                    store_name=_optional_str(raw.get("storeName")),
                    items=items,
                )
            )
        except ValidationError as exc:
            raise SwiggySchemaError("Swiggy returned an invalid order.") from exc

    return OrderList(
        orders=orders,
        has_more=data.get("hasMore") is True,
        next_cursor=_next_cursor(data, payload),
    )


def _next_cursor(data: dict[str, Any], payload: dict[str, Any]) -> str | None:
    for source in (data, payload):
        value = source.get("nextCursor")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _parse_item(raw: object) -> OrderItem:
    if not isinstance(raw, dict):
        raise SwiggySchemaError("Swiggy returned an invalid order item.")
    try:
        return OrderItem(
            name=_required_str(raw, "name"),
            quantity=_strict_integer(raw, "quantity", minimum=1),
            item_id=_optional_str(raw.get("itemId")),
        )
    except ValidationError as exc:
        raise SwiggySchemaError("Swiggy returned an invalid order item.") from exc


def _strict_integer(payload: dict[str, Any], key: str, minimum: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SwiggySchemaError(f"Swiggy response has invalid {key}.")
    return value


def _timestamp(payload: dict[str, Any], key: str) -> datetime:
    value = _required_str(payload, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SwiggySchemaError(f"Swiggy response has invalid {key}.") from exc
    if parsed.tzinfo is None:
        raise SwiggySchemaError(f"Swiggy response has timezone-free {key}.")
    return parsed


def _rupees_to_paise(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SwiggySchemaError("Swiggy response has invalid totalAmount.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise SwiggySchemaError("Swiggy response has invalid totalAmount.") from exc
    paise = amount * 100
    if not amount.is_finite() or amount < 0 or paise != paise.to_integral_value():
        raise SwiggySchemaError("Swiggy response has invalid totalAmount.")
    return int(paise)
