"""Tolerant, still-strict parsing of Zepto's COD order confirmation, and its
content-free diagnostics. No provider call."""

from __future__ import annotations

import logging

import pytest

from app.commerce.zepto.checkout import ZeptoConfirmedOrder, parse_confirmed_cod_order
from app.commerce.zepto.diagnostics import CONFIRM_FAILURE_EVENT
from app.commerce.zepto.errors import ZeptoSchemaError
from app.commerce.zepto.quote_provider import ZeptoCodCheckoutGateway
from app.shopping.errors import ProviderUnavailable
from app.shopping.models import Basket, BasketLine, ProductRef

from datetime import UTC, datetime


CODE = "UIIMJHTCC04881"


@pytest.mark.parametrize(
    ("payload", "order_id", "code", "payable"),
    [
        ({"data": {"isPreview": False, "paymentMethod": "COD", "orderId": "o-1", "toPayAmount": 5500}}, "o-1", None, 5500),
        ({"order_id": "o-2", "paymentMethod": "COD"}, "o-2", None, None),
        ({"data": {"id": "o-3", "code": CODE, "grandTotalAmount": 5500}}, "o-3", CODE, 5500),
        ({"data": {"code": CODE}}, CODE, CODE, None),
        ({"data": {"order": {"orderId": "o-4", "code": CODE}, "isPreview": False}}, "o-4", CODE, None),
        ({"result": {"orderId": "o-5"}}, "o-5", None, None),
        ({"data": {"orderId": 123456}}, "123456", None, None),
        ({"content_text": f"Your order has been placed successfully. Order ID: {CODE}"}, CODE, CODE, None),
        ({"content_text": f"Order confirmed! order code #{CODE}"}, CODE, CODE, None),
    ],
)
def test_documented_variants_are_accepted_only_with_an_order_identifier(
    payload: dict, order_id: str, code: str | None, payable: int | None
) -> None:
    parsed = parse_confirmed_cod_order(payload)
    assert parsed.order_id == order_id
    assert parsed.order_code == code
    assert parsed.payable_paise == payable


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"isPreview": True, "orderId": "o-1"}},
        {"data": {"requiresConfirmation": True, "orderId": "o-1"}},
        {"data": {"paymentMethod": "UPI", "orderId": "o-1"}},
        {"data": {"isPreview": False, "paymentMethod": "COD"}},
        {"data": {"orderId": "o-1", "id": "o-2"}},
        {"data": {"orderId": ""}},
        {"data": {"orderId": "o-1", "toPayAmount": 55.5}},
        {"data": "placed"},
        {"content_text": "Order placed successfully."},
        {"content_text": f"Order could not be placed. Order ID: {CODE}"},
        {"content_text": f"Payment failed for order id {CODE}"},
        {"content_text": f"Order preview ready. Order ID: {CODE}"},
        {"content_text": f"Order ID: {CODE}"},
        {"content_text": f"Order placed. Order ID: {CODE}, order code ZZZ999999"},
        {"content_text": ""},
        [],
    ],
)
def test_previews_failures_and_identifier_free_responses_are_refused(payload: object) -> None:
    with pytest.raises(ZeptoSchemaError):
        parse_confirmed_cod_order(payload)  # type: ignore[arg-type]


def test_the_original_strict_shape_still_parses_identically() -> None:
    confirmed = parse_confirmed_cod_order(
        {"data": {"isPreview": False, "paymentMethod": "COD", "orderId": "order_123", "toPayAmount": 5000, "etaInMinutes": 12}}
    )
    assert confirmed == ZeptoConfirmedOrder("order_123", 5000, 12)


SECRET_ADDRESS = "Flat 9B Secret Towers"
SECRET_ITEM = "Amul Sweet Lassi"


class OneShotClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[str] = []

    async def call_tool(self, name: str, arguments: dict, *, retry_on_auth: bool = True) -> object:
        self.calls.append(name)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _basket() -> Basket:
    now = datetime(2026, 11, 22, 2, 48, tzinfo=UTC)
    return Basket(
        id="b1",
        household_id="h1",
        provider="zepto",
        address_id="addr-1",
        lines=[
            BasketLine(
                ref=ProductRef(provider="zepto", product_id="p1", variant_id="v1", sku_id="s1"),
                name=SECRET_ITEM,
                unit_price_paise=5500,
                quantity=1,
            )
        ],
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "response",
    [
        {"data": {"status": "PLACED", "deliveryAddress": SECRET_ADDRESS, "items": [{"name": SECRET_ITEM}], "grandTotal": 5500}},
        {"content_text": f"Order for {SECRET_ITEM} to {SECRET_ADDRESS} is on its way"},
        TimeoutError("timed out"),
    ],
)
async def test_unparsed_confirmation_logs_only_its_shape_and_is_never_retried(
    response: object, caplog: pytest.LogCaptureFixture
) -> None:
    client = OneShotClient(response)
    caplog.set_level(logging.WARNING, logger="app.commerce.zepto.diagnostics")

    with pytest.raises(ProviderUnavailable):
        await ZeptoCodCheckoutGateway(client).confirm_cod(_basket())

    assert client.calls == ["create_order"]
    records = [record.getMessage() for record in caplog.records if CONFIRM_FAILURE_EVENT in record.getMessage()]
    assert len(records) == 1
    message = records[0]
    assert "stage=confirm" in message
    for secret in (SECRET_ADDRESS, SECRET_ITEM, "Secret", "Lassi", "5500", "addr-1"):
        assert secret not in message
    if isinstance(response, dict) and "data" in response:
        assert "data.deliveryAddress" in message and "data.items[].name" in message
    if isinstance(response, Exception):
        assert "step=provider_error" in message and "outcome=TimeoutError" in message
