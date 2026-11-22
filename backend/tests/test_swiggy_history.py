from copy import deepcopy

import pytest

from app.commerce.swiggy.errors import SwiggySchemaError
from app.commerce.swiggy.history import SwiggyOrderHistoryAdapter, parse_orders


ORDER_HISTORY = {
    "success": True,
    "data": {
        "orders": [
            {
                "orderId": "IM-1234567890",
                "status": "DELIVERED",
                "createdAt": "2026-11-01T09:55:00Z",
                "updatedAt": "2026-11-01T10:15:00Z",
                "itemCount": 3,
                "totalAmount": 388.1,
                "deliveryAddress": {
                    "id": "addr_home",
                    "addressLine": "12 Test Road",
                    "phoneNumber": "9999999999",
                },
                "paymentMethod": "COD",
                "paymentStatus": "PAID",
                "orderType": "DASH",
                "isActive": False,
                "currentStatus": "DELIVERED",
                "historyStatus": "COMPLETED",
                "storeName": "Instamart Indiranagar",
                "items": [
                    {
                        "name": "Fortune Sunflower Oil 1 L",
                        "quantity": 2,
                        "itemId": "spin_42",
                    },
                    {"name": "Amul Taaza Milk 1 L", "quantity": 1},
                ],
            }
        ],
        "hasMore": False,
    },
}


def test_parse_orders_normalizes_history_and_omits_address_pii() -> None:
    result = parse_orders(ORDER_HISTORY)
    order = result.orders[0]

    assert order.order_id == "IM-1234567890"
    assert order.total_paise == 38810
    assert order.created_at.isoformat() == "2026-11-01T09:55:00+00:00"
    assert order.items[0].item_id == "spin_42"
    serialized = order.model_dump(mode="json")
    assert "delivery_address" not in serialized
    assert "phone" not in str(serialized).lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("totalAmount", 10.001),
        ("createdAt", "not-a-date"),
        ("itemCount", True),
    ],
)
def test_parse_orders_rejects_invalid_provider_values(field: str, value: object) -> None:
    payload = deepcopy(ORDER_HISTORY)
    payload["data"]["orders"][0][field] = value
    with pytest.raises(SwiggySchemaError):
        parse_orders(payload)


@pytest.mark.asyncio
async def test_history_adapter_calls_only_get_orders() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def call_tool(
            self, name: str, arguments: dict[str, object]
        ) -> dict[str, object]:
            self.calls.append((name, arguments))
            return ORDER_HISTORY

    client = FakeClient()
    adapter = SwiggyOrderHistoryAdapter(client)  # type: ignore[arg-type]

    result = await adapter.orders(count=5, active_only=True)

    assert len(result.orders) == 1
    assert client.calls == [
        ("get_orders", {"orderType": "INSTAMART", "count": 5, "activeOnly": True})
    ]
