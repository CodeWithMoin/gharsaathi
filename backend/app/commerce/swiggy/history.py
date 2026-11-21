from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.commerce.swiggy.adapter import _data, _optional_str, _required_str
from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.swiggy.errors import SwiggySchemaError
from app.commerce.swiggy.models import OrderItem, OrderList, OrderSummary


class SwiggyOrderHistoryAdapter:
    """Read-only adapter for the authenticated user's recent Instamart orders."""

    def __init__(self, client: SwiggyMcpClient) -> None:
        self.client = client

    async def orders(self, count: int = 10, active_only: bool = False) -> OrderList:
        arguments: dict[str, object] = {"count": count}
        if active_only:
            arguments["activeOnly"] = True
        return parse_orders(await self.client.call_tool("get_orders", arguments))


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

    return OrderList(orders=orders, has_more=data.get("hasMore") is True)


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
