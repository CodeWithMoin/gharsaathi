import pytest

from app.commerce.zepto.errors import ZeptoSchemaError
from app.commerce.zepto.history import parse_order_history


def _history_response() -> dict[str, object]:
    return {
        "count": 3,
        "orders": [
            {
                "id": "order_123",
                "code": "ZEP123",
                "formattedStatus": "Delivered",
                "grandTotalAmount": 27850,
                "itemQuantityCount": 3,
                "productsNamesAndCounts": [
                    {"name": "Milk", "count": 2},
                    {"name": "Bread", "count": 1},
                ],
                "totalArrivalTimeInMinutes": 12,
                "totalRefundAmount": 500,
            }
        ],
    }


def test_zepto_order_history_preserves_provider_ids_status_and_paise() -> None:
    result = parse_order_history(_history_response(), limit=1, page_number=1)

    order = result.orders[0]
    assert order.id == "order_123"
    assert order.code == "ZEP123"
    assert order.formatted_status == "Delivered"
    assert order.grand_total_paise == 27850
    assert order.total_refund_paise == 500
    assert [(product.name, product.count) for product in order.products] == [
        ("Milk", 2),
        ("Bread", 1),
    ]
    assert result.pagination.model_dump() == {
        "page_number": 1,
        "limit": 1,
        "total": 3,
        "total_pages": 3,
        "has_more": True,
    }


def test_zepto_order_history_accepts_null_arrival_time_and_last_page() -> None:
    response = _history_response()
    order = response["orders"][0]  # type: ignore[index]
    order["totalArrivalTimeInMinutes"] = None  # type: ignore[index]

    result = parse_order_history(response, limit=2, page_number=2)

    assert result.orders[0].total_arrival_time_minutes is None
    assert result.pagination.total_pages == 2
    assert result.pagination.has_more is False


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda response: response.__setitem__("count", True), "count"),
        (lambda response: response.__setitem__("orders", {}), "order list"),
        (
            lambda response: response["orders"][0].__setitem__("grandTotalAmount", 278.5),  # type: ignore[index]
            "grand total",
        ),
        (
            lambda response: response["orders"][0].__setitem__("productsNamesAndCounts", [{}]),  # type: ignore[index]
            "product name",
        ),
        (
            lambda response: response["orders"][0].__setitem__("totalArrivalTimeInMinutes", "12"),  # type: ignore[index]
            "arrival time",
        ),
    ],
)
def test_zepto_order_history_fails_closed_for_malformed_provider_payload(
    mutate, error: str
) -> None:
    response = _history_response()
    mutate(response)

    with pytest.raises(ZeptoSchemaError, match=error):
        parse_order_history(response)


@pytest.mark.parametrize(
    ("limit", "page_number"), [(0, 1), (1, 0), (True, 1), (1, True)])
def test_zepto_order_history_rejects_invalid_pagination_request(
    limit: int, page_number: int
) -> None:
    with pytest.raises(ZeptoSchemaError):
        parse_order_history(_history_response(), limit=limit, page_number=page_number)
