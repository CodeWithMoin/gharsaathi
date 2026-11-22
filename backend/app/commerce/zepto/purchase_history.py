"""Conservative, read-only Zepto history reader for consented personalization.

Rate limits are the main risk, so the reader is deliberately slow and small:
one call at a time, at least a second apart, at most ``detail_budget`` detail
reads per sync, never a retry, and a full stop on the first rate limit or
session error. A sync that runs out of budget records where it stopped and
the next explicit sync resumes there.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
import time

from app.commerce.swiggy.models import OrderItem, OrderSummary
from app.commerce.zepto.history import ZeptoOrderDetail
from app.commerce.zepto.service import ZeptoService
from app.commerce.zepto.errors import ZeptoSchemaError, ZeptoUnrecognisedResponse
from app.memory.history_reading import (
    CallPacer,
    HistoryRead,
    HistoryReadPlan,
    stop_reason_for,
)


_COMPLETED = {"delivered", "completed"}
PAGE_LIMIT = 10
MAX_ORDERS = 100
DETAIL_BUDGET = 30
MIN_CALL_INTERVAL_SECONDS = 1.0
# One malformed order is skipped and counted; anything else stops the sync.
_TOLERATED = (ZeptoSchemaError, ZeptoUnrecognisedResponse)


class ZeptoPurchaseHistoryAdapter:
    def __init__(
        self,
        service: ZeptoService,
        *,
        detail_budget: int = DETAIL_BUDGET,
        max_orders: int = MAX_ORDERS,
        page_limit: int = PAGE_LIMIT,
        min_interval: float = MIN_CALL_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self.service = service
        self.detail_budget = detail_budget
        self.max_orders = max_orders
        self.page_limit = page_limit
        self.pacer = CallPacer(min_interval, monotonic=monotonic, sleep=sleep)

    async def read_history(self, plan: HistoryReadPlan) -> HistoryRead:
        read = HistoryRead(coverage="full_history")
        page = max(plan.start_page, 1)
        max_pages = -(-self.max_orders // self.page_limit)
        scanned = (page - 1) * self.page_limit
        while True:
            if read.list_calls >= max_pages or scanned >= self.max_orders:
                read.stopped_reason = "max_orders"
                return read
            await self.pacer.wait()
            read.list_calls += 1
            try:
                history = await self.service.order_history(
                    limit=self.page_limit, page_number=page, recover=False
                )
            except Exception as exc:  # noqa: BLE001 - handed back, never retried
                return self._stopped(read, exc, page)
            for summary in history.orders:
                scanned += 1
                if summary.formatted_status.strip().casefold() not in _COMPLETED:
                    continue
                if summary.id in plan.known_ids:
                    read.skipped_known += 1
                    if plan.incremental:
                        read.stopped_reason = "known"
                        return read
                    continue
                if read.detail_calls >= self.detail_budget:
                    read.stopped_reason = "detail_budget"
                    read.next_page = page
                    return read
                await self.pacer.wait()
                read.detail_calls += 1
                try:
                    order = _normalize_detail(
                        await self.service.order_detail(summary.id, recover=False),
                        lookup_id=summary.id,
                    )
                except _TOLERATED:
                    read.detail_failures += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    return self._stopped(read, exc, page)
                if order.created_at < plan.cutoff:
                    # Newest-first: everything after this is older still.
                    read.stopped_reason = plan.cutoff_reason
                    return read
                read.orders.append(order)
            if (
                len(history.orders) < self.page_limit
                or not history.pagination.has_more
            ):
                read.stopped_reason = "complete"
                return read
            page += 1

    @staticmethod
    def _stopped(read: HistoryRead, exc: Exception, page: int) -> HistoryRead:
        read.error = exc
        read.stopped_reason = stop_reason_for(exc)
        read.next_page = page
        return read


def _normalize_detail(
    detail: ZeptoOrderDetail, *, lookup_id: str | None = None
) -> OrderSummary:
    placed_at = _timestamp(detail.placed_time, "placedTime")
    updated_at = placed_at
    items: list[OrderItem] = []
    for shipment in detail.shipments:
        for timestamp in (shipment.arrived_time, shipment.delivery_time):
            if timestamp:
                updated_at = max(updated_at, _timestamp(timestamp, "shipment time"))
        for product in shipment.products:
            quantity = product.quantity_fulfilled
            if quantity <= 0:
                continue
            items.append(
                OrderItem(
                    name=_product_name(product.brand, product.product_name, product.pack_size),
                    quantity=quantity,
                    item_id=product.id,
                )
            )
    return OrderSummary(
        order_id=detail.order_id,
        status=detail.formatted_status,
        current_status=detail.formatted_status,
        history_status=detail.formatted_status,
        created_at=placed_at,
        updated_at=updated_at,
        item_count=sum(item.quantity for item in items),
        total_paise=detail.grand_total_paise,
        order_type=detail.order_type,
        is_active=False,
        items=items,
        lookup_id=lookup_id,
    )


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ZeptoSchemaError(f"Zepto returned an invalid {field}.") from exc
    if parsed.tzinfo is None:
        raise ZeptoSchemaError(f"Zepto returned a timezone-free {field}.")
    return parsed


def _product_name(brand: str, name: str, pack_size: str) -> str:
    parts: list[str] = []
    normalized_name = name.casefold()
    if brand.strip() and brand.casefold() not in normalized_name:
        parts.append(brand.strip())
    parts.append(name.strip())
    if pack_size.strip() and pack_size.casefold() not in normalized_name:
        parts.append(pack_size.strip())
    return " ".join(parts)
