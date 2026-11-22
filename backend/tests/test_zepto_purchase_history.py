from datetime import UTC, datetime

import pytest

from app.commerce.zepto.history import parse_order_detail, parse_order_history
from app.commerce.zepto.purchase_history import ZeptoPurchaseHistoryAdapter
from app.memory.history_reading import HistoryReadPlan


def _history_payload() -> dict[str, object]:
    return {
        "count": 4,
        "orders": [
            {
                "id": f"internal-{index}",
                "code": f"Z{index}",
                "formattedStatus": "Delivered",
                "grandTotalAmount": 6900,
                "itemQuantityCount": 1,
                "productsNamesAndCounts": [{"name": "Milk", "count": 1}],
                "totalArrivalTimeInMinutes": 12,
                "totalRefundAmount": 0,
            }
            for index in range(4)
        ],
    }


def _detail_payload(order_id: str) -> dict[str, object]:
    return {
        "id": order_id,
        "orderId": f"provider-{order_id}",
        "code": "Z123",
        "formattedStatus": "Delivered",
        "orderType": "INSTANT",
        "placedTime": "2026-11-20T10:00:00Z",
        "userAddressFormatted": "redacted by adapter",
        "itemQuantityCount": 1,
        "grandTotalAmount": 6900,
        "grandTotalAmountPreDiscount": 7200,
        "totalRefundAmount": 0,
        "walletBalanceUsed": 0,
        "isInvoiceAvailable": False,
        "invoiceUrl": None,
        "isEligibleForRating": False,
        "billSummary": {"itemTotal": 6900, "totalBill": 6900, "totalSaved": 300},
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
                        "id": "amul-milk-1l",
                        "productName": "Taaza Milk",
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


class FakeZeptoHistoryService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def order_history(self, *, limit: int, page_number: int, recover: bool = True):
        assert recover is False
        self.calls.append(("list", (limit, page_number)))
        return parse_order_history(_history_payload(), limit=limit, page_number=page_number)

    async def order_detail(self, order_id: str, *, recover: bool = True):
        assert recover is False
        self.calls.append(("detail", order_id))
        return parse_order_detail(_detail_payload(order_id))


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_zepto_personalization_reader_is_sequential_and_normalized() -> None:
    service = FakeZeptoHistoryService()
    read = await ZeptoPurchaseHistoryAdapter(service, sleep=_no_sleep).read_history(  # type: ignore[arg-type]
        HistoryReadPlan(
            known_ids=frozenset({"internal-3"}),
            cutoff=datetime(2025, 11, 20, tzinfo=UTC),
            cutoff_reason="lookback",
        )
    )

    assert service.calls == [
        ("list", (10, 1)),
        ("detail", "internal-0"),
        ("detail", "internal-1"),
        ("detail", "internal-2"),
    ]
    assert read.skipped_known == 1
    assert read.stopped_reason == "complete"
    assert read.next_page is None
    history = read
    assert len(history.orders) == 3
    assert history.orders[0].lookup_id == "internal-0"
    assert history.orders[0].items[0].name == "Amul Taaza Milk 1 L"
    assert history.orders[0].items[0].item_id == "amul-milk-1l"
    assert history.orders[0].created_at.isoformat() == "2026-11-20T10:00:00+00:00"
    assert history.orders[0].updated_at.isoformat() == "2026-11-20T10:12:00+00:00"
