import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.commerce.zepto.checkout import (
    ZeptoConfirmedOrder,
    parse_confirmed_cod_order,
    quote_from_cod_preview,
)
from app.commerce.zepto.errors import ZeptoSchemaError
from app.commerce.zepto.quote_provider import (
    ZeptoCodCheckoutGateway,
    ZeptoQuoteProvider,
    device_id_for,
)
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Basket, BasketLine, ProductRef


NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "zepto_cod_preview.json"


def preview() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def basket() -> Basket:
    return Basket(
        id="basket_zepto_1",
        household_id="household_1",
        provider="zepto",
        address_id="addr_home",
        revision=4,
        created_at=NOW,
        updated_at=NOW,
        lines=[
            BasketLine(
                ref=ProductRef(
                    provider="zepto",
                    product_id="product_milk",
                    variant_id="pvid_milk",
                    sku_id="spid_milk",
                ),
                name="Test Milk 500 ml",
                unit_price_paise=1500,
                quantity=1,
            )
        ],
    )


class FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        self.calls.append((name, copy.deepcopy(arguments), retry_on_auth))
        value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def test_preview_is_an_exact_reconciled_cod_quote() -> None:
    quote = quote_from_cod_preview(basket(), preview(), NOW)

    assert quote.provider == "zepto"
    assert quote.item_total_paise == 1500
    assert quote.payable_paise == 5000
    assert quote.eta_text == "12 min"
    assert [(charge.label, charge.amount_paise) for charge in quote.charges] == [
        ("Delivery fee", 3000),
        ("Packaging fee", 500),
    ]


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda result: result["data"].__setitem__("paymentMethod", "UPI"), ApprovalRefused),
        (lambda result: result["data"].__setitem__("deliverable", False), ApprovalRefused),
        (lambda result: result["data"].__setitem__("isPreview", False), ZeptoSchemaError),
        (lambda result: result["data"].__setitem__("deliveryFee", 30.0), ZeptoSchemaError),
        (lambda result: result["data"]["items"][0].__setitem__("quantity", 2), ApprovalRefused),
        (lambda result: result["data"]["items"][0].__setitem__("storeProductId", "other"), ApprovalRefused),
        (lambda result: result["data"].__setitem__("toPayAmount", 4999), ZeptoSchemaError),
        (lambda result: result["data"].__setitem__("totalItems", 1.0), ZeptoSchemaError),
        (lambda result: result["data"].__setitem__("freeDelivery", False), ZeptoSchemaError),
    ],
)
def test_preview_rejects_unapproved_or_ambiguous_states(mutate: Any, error: type[Exception]) -> None:
    value = preview()
    mutate(value)

    with pytest.raises(error):
        quote_from_cod_preview(basket(), value, NOW)


def test_preview_rejects_missing_extra_or_duplicate_items() -> None:
    missing = preview()
    missing["data"]["items"] = []
    extra = preview()
    extra["data"]["items"].append(copy.deepcopy(extra["data"]["items"][0]))
    duplicate = preview()
    duplicate["data"]["items"].append(copy.deepcopy(duplicate["data"]["items"][0]))
    duplicate["data"]["totalItems"] = 2

    for value in (missing, extra, duplicate):
        with pytest.raises((ApprovalRefused, ZeptoSchemaError)):
            quote_from_cod_preview(basket(), value, NOW)


def test_preview_reconciles_strict_other_fees() -> None:
    value = preview()
    value["data"]["otherFees"] = [{"label": "Small cart fee", "amount": 100}]
    value["data"]["toPayAmount"] = 5100

    quote = quote_from_cod_preview(basket(), value, NOW)

    assert [(charge.label, charge.amount_paise) for charge in quote.charges] == [
        ("Delivery fee", 3000),
        ("Packaging fee", 500),
        ("Small cart fee", 100),
    ]

    value["data"]["otherFees"] = [{"label": "Small cart fee", "amount": 100.0}]
    with pytest.raises(ZeptoSchemaError):
        quote_from_cod_preview(basket(), value, NOW)


async def test_quote_selects_address_replaces_exact_cart_and_never_retries() -> None:
    fake = FakeClient(
        {
            "select_saved_address": {"success": True},
            "update_cart": {"success": True},
            "create_order": preview(),
        }
    )
    provider = ZeptoQuoteProvider(fake, clock=lambda: NOW)

    quote = await provider.quote(basket())

    assert quote.payable_paise == 5000
    assert fake.calls == [
        ("select_saved_address", {"addressId": "addr_home"}, False),
        (
            "update_cart",
            {
                "deviceId": device_id_for(basket()),
                "cartItems": [
                    {
                        "productVariantId": "pvid_milk",
                        "storeProductId": "spid_milk",
                        "quantity": 1,
                    }
                ],
                "replaceCart": True,
            },
            False,
        ),
        (
            "create_order",
            {"confirmOrder": False, "userAddressId": "addr_home"},
            False,
        ),
    ]


async def test_quote_converts_bad_provider_shape_to_safe_failure() -> None:
    bad_preview = preview()
    bad_preview["data"]["items"][0]["price"] = 10.5
    fake = FakeClient(
        {
            "select_saved_address": {"success": True},
            "update_cart": {"success": True},
            "create_order": bad_preview,
        }
    )

    with pytest.raises(ProviderUnavailable, match="invalid cash-on-delivery preview"):
        await ZeptoQuoteProvider(fake).quote(basket())


def test_confirmed_order_requires_explicit_non_preview_cod_order_id() -> None:
    confirmed = parse_confirmed_cod_order(
        {
            "data": {
                "isPreview": False,
                "paymentMethod": "COD",
                "orderId": "order_123",
                "toPayAmount": 5000,
                "etaInMinutes": 12,
            }
        }
    )

    assert confirmed == ZeptoConfirmedOrder("order_123", 5000, 12)

    for value in (
        {"data": {"isPreview": True, "paymentMethod": "COD", "orderId": "order_123"}},
        {"data": {"isPreview": False, "paymentMethod": "COD"}},
        {"data": {"isPreview": False, "paymentMethod": "UPI", "orderId": "order_123"}},
        {"data": {"isPreview": False, "paymentMethod": "COD", "orderId": "order_123"}},
    ):
        with pytest.raises(ZeptoSchemaError):
            parse_confirmed_cod_order(value)


async def test_confirmation_is_single_non_replayed_call_and_never_accepts_a_preview() -> None:
    fake = FakeClient(
        {
            "create_order": {
                "data": {
                    "isPreview": False,
                    "paymentMethod": "COD",
                    "orderId": "order_123",
                    "toPayAmount": 5000,
                    "etaInMinutes": None,
                }
            }
        }
    )

    result = await ZeptoCodCheckoutGateway(fake).confirm_cod(basket())

    assert result.order_id == "order_123"
    assert fake.calls == [
        ("create_order", {"confirmOrder": True, "userAddressId": "addr_home"}, False)
    ]


async def test_confirmation_surfaces_ambiguous_provider_result_without_retrying() -> None:
    fake = FakeClient({"create_order": preview()})

    with pytest.raises(ProviderUnavailable, match="ambiguous order confirmation"):
        await ZeptoCodCheckoutGateway(fake).confirm_cod(basket())
    assert fake.calls == [
        ("create_order", {"confirmOrder": True, "userAddressId": "addr_home"}, False)
    ]
