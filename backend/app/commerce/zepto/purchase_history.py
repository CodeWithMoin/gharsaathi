"""Bounded, read-only Zepto history adapter for consented personalization."""

from datetime import datetime

from app.commerce.swiggy.models import OrderItem, OrderList, OrderSummary
from app.commerce.zepto.history import ZeptoOrderDetail
from app.commerce.zepto.service import ZeptoService
from app.commerce.zepto.errors import ZeptoSchemaError


_COMPLETED = {"delivered", "completed"}


class ZeptoPurchaseHistoryAdapter:
    """Read at most three delivered orders, sequentially, from verified tools."""

    def __init__(self, service: ZeptoService, *, detail_limit: int = 3) -> None:
        self.service = service
        self.detail_limit = detail_limit

    async def orders(self, count: int = 10, active_only: bool = False) -> OrderList:
        if active_only:
            return OrderList(orders=[])
        list_limit = min(max(count, 1), 5)
        history = await self.service.order_history(limit=list_limit, page_number=1)
        candidates = [
            order
            for order in history.orders
            if order.formatted_status.strip().casefold() in _COMPLETED
        ][: self.detail_limit]
        orders: list[OrderSummary] = []
        for candidate in candidates:
            detail = await self.service.order_detail(candidate.id)
            orders.append(_normalize_detail(detail))
        return OrderList(orders=orders, has_more=history.pagination.has_more)


def _normalize_detail(detail: ZeptoOrderDetail) -> OrderSummary:
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
