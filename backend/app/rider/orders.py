"""The owner's active orders, read only from GharSaathi's own records.

No store is called while a rider is on the line: the order comes from the
provider-confirmed checkout attempt and its basket, the status from the local
tracking monitor (last provider reading), the address from the saved-address
snapshot. Fields are an allow-list; nothing like an OTP is ever read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.commerce.provider_state import SavedAddressDirectory
from app.database.models import ShoppingBasketRecord, ZeptoCheckoutAttemptRecord
from app.shopping.models import CheckoutStatus
from app.tracking.errors import TrackingNotFound
from app.tracking.models import TrackingProvider, TrackingStatus
from app.tracking.store import OrderTrackingStore

# Orders confirmed longer ago than this are not "active" for a rider call.
ACTIVE_WINDOW = timedelta(hours=12)
MAX_ORDERS = 3
STATUS_TEXT = {
    TrackingStatus.CONFIRMED: "order placed",
    TrackingStatus.PREPARING: "being packed",
    TrackingStatus.OUT_FOR_DELIVERY: "out for delivery",
    TrackingStatus.PAUSED: "status unavailable",
    TrackingStatus.UNAVAILABLE: "status unavailable",
    TrackingStatus.UNKNOWN: "status unknown",
}
FINISHED = {TrackingStatus.DELIVERED, TrackingStatus.CANCELLED}


class ActiveOrder(BaseModel):
    store: str
    status: str
    items_count: int
    payment: str
    address_label: str | None = None
    address: str | None = None
    placed_at: datetime | None = None


class ActiveOrderReader(Protocol):
    async def active_orders(self, user_id: str) -> list[ActiveOrder]: ...


class DatabaseActiveOrderReader:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tracking: OrderTrackingStore | None,
        addresses: SavedAddressDirectory | None,
        *,
        now: Any = None,
    ) -> None:
        self.sessions = sessions
        self.tracking = tracking
        self.addresses = addresses
        self.now = now or (lambda: datetime.now(UTC))

    async def active_orders(self, user_id: str) -> list[ActiveOrder]:
        since = self.now() - ACTIVE_WINDOW
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(ZeptoCheckoutAttemptRecord, ShoppingBasketRecord)
                    .join(
                        ShoppingBasketRecord,
                        ShoppingBasketRecord.id == ZeptoCheckoutAttemptRecord.basket_id,
                    )
                    .where(
                        ZeptoCheckoutAttemptRecord.household_id == user_id,
                        ZeptoCheckoutAttemptRecord.status == CheckoutStatus.CONFIRMED.value,
                        ZeptoCheckoutAttemptRecord.provider_order_id.is_not(None),
                    )
                    .order_by(ZeptoCheckoutAttemptRecord.created_at.desc())
                    .limit(10)
                )
            ).all()
        orders: list[ActiveOrder] = []
        for attempt, basket in rows:
            placed = attempt.completed_at or attempt.created_at
            if placed is not None and placed.tzinfo is None:
                placed = placed.replace(tzinfo=UTC)
            if placed is not None and placed < since:
                continue
            status = self._status(user_id, attempt.provider_order_id or "")
            if status in FINISHED:
                continue
            label, line = await self._address(user_id, basket.address_id)
            orders.append(
                ActiveOrder(
                    store="Zepto",
                    status=STATUS_TEXT.get(status, "status unknown"),
                    items_count=sum(
                        int(line_item.get("quantity", 0) or 0)
                        for line_item in basket.lines or []
                        if isinstance(line_item, dict)
                    ),
                    payment="cash_on_delivery" if attempt.payment_method == "cod" else "paid_online",
                    address_label=label,
                    address=line,
                    placed_at=placed,
                )
            )
            if len(orders) >= MAX_ORDERS:
                break
        return orders

    def _status(self, user_id: str, order_id: str) -> TrackingStatus:
        if self.tracking is None or not order_id:
            return TrackingStatus.UNKNOWN
        try:
            watch = self.tracking.get(user_id, TrackingProvider.ZEPTO, order_id)
        except (TrackingNotFound, OSError, ValueError):
            return TrackingStatus.UNKNOWN
        snapshot = watch.snapshot
        if snapshot is not None and (snapshot.delivered or snapshot.cancelled):
            return TrackingStatus.DELIVERED if snapshot.delivered else TrackingStatus.CANCELLED
        return watch.status

    async def _address(self, user_id: str, address_id: str | None) -> tuple[str | None, str | None]:
        if self.addresses is None or not address_id:
            return None, None
        try:
            summary = await self.addresses.describe(user_id, "zepto", address_id)
        except Exception:  # noqa: BLE001 - a missing address must not break the call
            return None, None
        if summary is None:
            return None, None
        return summary.label, summary.short_address
