from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.commerce.zepto.service import ZeptoService
from app.dependencies import get_zepto_service
from app.main import app


class UnusedOAuth:
    pass


class ReadClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        self.calls.append((name, arguments, retry_on_auth))
        if name == "view_cart":
            return {"isEmpty": True, "items": [], "totalItems": 0}
        if name == "get_order_detail":
            return {
                "id": "internal-1",
                "orderId": "order-1",
                "code": "Z123",
                "formattedStatus": "Delivered",
                "orderType": "INSTANT",
                "placedTime": "2026-11-20T10:00:00Z",
                "userAddressFormatted": "Home",
                "itemQuantityCount": 1,
                "grandTotalAmount": 6900,
                "grandTotalAmountPreDiscount": 7200,
                "totalRefundAmount": 0,
                "walletBalanceUsed": 0,
                "isInvoiceAvailable": True,
                "invoiceUrl": "https://example.test/invoice",
                "isEligibleForRating": False,
                "billSummary": {
                    "itemTotal": 6900,
                    "totalBill": 6900,
                    "totalSaved": 300,
                },
                "shipments": [
                    {
                        "shipmentId": "shipment-1",
                        "shipmentCode": "S1",
                        "status": "DELIVERED",
                        "formattedStatus": "Delivered",
                        "placedTime": "2026-11-20T10:00:00Z",
                        "deliveryTime": "2026-11-20T10:12:00Z",
                        "arrivedTime": "2026-11-20T10:12:00Z",
                        "committedEtaInMins": 15,
                        "totalArrivalTimeInMinutes": 12,
                        "shipmentWeightInGms": 1000,
                        "products": [
                            {
                                "id": "product-1",
                                "productName": "Milk",
                                "brand": "Amul",
                                "packsize": "1 L",
                                "imagePath": "https://example.test/milk.png",
                                "status": "DELIVERED",
                                "quantityOrdered": 1,
                                "quantityFulfilled": 1,
                                "quantityRejected": 0,
                                "unitMRP": 7200,
                                "unitSellingPrice": 6900,
                                "totalMRP": 7200,
                                "totalFinalSellingPrice": 6900,
                                "totalRefundAmount": 0,
                            }
                        ],
                    }
                ],
            }
        return {"count": 0, "orders": []}


@pytest.mark.asyncio
async def test_read_service_never_replays_cart_and_normalizes_order_history() -> None:
    client = ReadClient()
    service = ZeptoService(UnusedOAuth(), client)  # type: ignore[arg-type]

    cart = await service.cart()
    history = await service.order_history(limit=5, page_number=2)

    assert cart.is_empty is True
    assert history.pagination.page_number == 2
    assert client.calls == [
        ("view_cart", {}, False),
        ("list_order_history", {"limit": 5, "pageNumber": 2}, True),
    ]


def test_read_endpoints_expose_cart_and_history_without_mutation_routes() -> None:
    service = ZeptoService(UnusedOAuth(), ReadClient())  # type: ignore[arg-type]
    app.dependency_overrides[get_zepto_service] = lambda: service
    try:
        with TestClient(app) as client:
            cart = client.get("/api/zepto/cart")
            history = client.get(
                "/api/zepto/orders", params={"limit": 5, "page_number": 2}
            )
            detail = client.get("/api/zepto/orders/order-1")
            schema = client.get("/openapi.json").json()
    finally:
        app.dependency_overrides.pop(get_zepto_service, None)

    assert cart.status_code == 200
    assert cart.json()["is_empty"] is True
    assert history.status_code == 200
    assert history.json()["pagination"]["page_number"] == 2
    assert detail.status_code == 200
    assert detail.json()["grand_total_paise"] == 6900
    assert detail.json()["shipments"][0]["products"][0]["product_name"] == "Milk"
    assert "/api/zepto/cart" in schema["paths"]
    assert set(schema["paths"]["/api/zepto/cart"]) == {"get"}
    assert "/api/zepto/orders" in schema["paths"]
    assert set(schema["paths"]["/api/zepto/orders"]) == {"get"}
    assert set(schema["paths"]["/api/zepto/orders/{order_id}"]) == {"get"}
    assert "/api/zepto/checkout" not in schema["paths"]
    assert "/api/zepto/order" not in schema["paths"]
