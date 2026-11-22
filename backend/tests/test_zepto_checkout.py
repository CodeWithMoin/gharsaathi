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
from app.api.basket import approval_request_out
from app.shopping.approval import ApprovalRequest
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Approval, Basket, BasketLine, ProductRef


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
    assert quote.offers[0].model_dump() == {
        "title": "Add more items to unlock free delivery",
        "kind": "free_delivery",
        "minimum_order_paise": 9900,
        "remaining_paise": 8400,
        "potential_saving_paise": 3000,
        "net_extra_paise": 5400,
        "unlocked": False,
        "applied": False,
        "provider_confirmed": True,
    }


def test_free_delivery_counts_only_the_remaining_fee_after_partial_discount() -> None:
    value = preview()
    value["data"]["deliveryFeePreDiscount"] = 3000
    value["data"]["deliveryFeeDiscount"] = 1000
    value["data"]["deliveryFee"] = 2000
    value["data"]["toPayAmount"] = 4000

    quote = quote_from_cod_preview(basket(), value, NOW)

    assert quote.offers[0].potential_saving_paise == 2000
    assert quote.offers[0].net_extra_paise == 6400


def test_preview_normalises_provider_offer_cards_without_inventing_bank_discount() -> None:
    value = preview()
    value["data"]["offers"] = [
        {
            "title": "Get 10% off with Example Bank",
            "type": "BANK_OFFER",
            "minimumOrderValue": 9900,
            "discountAmount": 0,
            "isUnlocked": False,
            "isApplied": False,
            "isAutoApplicable": False,
        },
        {
            "title": "₹20 off on this basket",
            "type": "COUPON",
            "minimumOrderValue": 1000,
            "discountAmount": 2000,
            "isUnlocked": True,
            "isApplied": True,
            "isAutoApplicable": True,
        },
        {
            "title": "Not safe to parse",
            "type": "BANK_OFFER",
            "minimumOrderValue": 9900.5,
            "discountAmount": 500,
            "isUnlocked": False,
            "isApplied": False,
            "isAutoApplicable": False,
        },
    ]
    value["data"]["discount"] = 2000
    value["data"]["toPayAmount"] = 3000

    quote = quote_from_cod_preview(basket(), value, NOW)

    assert [offer.model_dump() for offer in quote.offers[1:]] == [
        {
            "title": "Get 10% off with Example Bank",
            "kind": "bank_offer",
            "minimum_order_paise": 9900,
            "remaining_paise": 8400,
            "potential_saving_paise": None,
            "net_extra_paise": None,
            "unlocked": False,
            "applied": False,
            "provider_confirmed": False,
        },
        {
            "title": "₹20 off on this basket",
            "kind": "promo",
            "minimum_order_paise": 1000,
            "remaining_paise": 0,
            "potential_saving_paise": None,
            "net_extra_paise": None,
            "unlocked": True,
            "applied": True,
            "provider_confirmed": True,
        },
    ]


def test_approval_response_exposes_structured_offer_money() -> None:
    quote = quote_from_cod_preview(basket(), preview(), NOW)
    approval = Approval(
        id="approval_1",
        basket_id="basket_zepto_1",
        basket_revision=4,
        fingerprint="a" * 64,
        payable_paise=quote.payable_paise,
        created_at=NOW,
        expires_at=NOW,
    )

    response = approval_request_out(
        ApprovalRequest(approval=approval, quote=quote, basket=basket())
    )

    assert response.quote.offers[0].model_dump() == {
        "kind": "free_delivery",
        "title": "Add more items to unlock free delivery",
        "minimum_order_paise": 9900,
        "remaining_paise": 8400,
        "potential_saving_paise": 3000,
        "net_extra_paise": 5400,
        "unlocked": False,
        "applied": False,
        "provider_confirmed": True,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda result: result["data"]["freeDelivery"].__setitem__("remainingAmountToUnlock", 1),
        lambda result: result["data"]["freeDelivery"].__setitem__("isUnlocked", True),
    ],
)
def test_preview_rejects_inconsistent_free_delivery_progress(mutate: Any) -> None:
    value = preview()
    mutate(value)

    with pytest.raises(ZeptoSchemaError):
        quote_from_cod_preview(basket(), value, NOW)


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
        (lambda result: result["data"].__setitem__("totalItems", 1.5), ZeptoSchemaError),
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

    value["data"]["otherFees"] = [{"label": "Small cart fee", "amount": 100.5}]
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

    with pytest.raises(ProviderUnavailable, match="preview could not be verified because the item details were invalid"):
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


def test_preview_accepts_total_items_as_distinct_products_for_quantity_two() -> None:
    """Owner's live case: 2 x Amul Lassi; Zepto counts one distinct product."""
    two = basket()
    two.lines[0].quantity = 2
    value = preview()
    value["data"]["items"][0]["quantity"] = 2
    value["data"]["subTotal"] = 3000
    value["data"]["toPayAmount"] = 6500
    value["data"]["totalItems"] = 1
    value["data"]["freeDelivery"]["remainingAmountToUnlock"] = 6900

    quote = quote_from_cod_preview(two, value, NOW)

    assert quote.payable_paise == 6500


def test_preview_still_refuses_real_quantity_mismatches() -> None:
    two = basket()
    two.lines[0].quantity = 2
    wrong_line = preview()
    wrong_line["data"]["items"][0]["quantity"] = 1
    wrong_line["data"]["subTotal"] = 1500
    wrong_line["data"]["totalItems"] = 1
    wrong_total = preview()
    wrong_total["data"]["items"][0]["quantity"] = 2
    wrong_total["data"]["subTotal"] = 3000
    wrong_total["data"]["toPayAmount"] = 6500
    wrong_total["data"]["totalItems"] = 3
    wrong_total["data"]["freeDelivery"]["remainingAmountToUnlock"] = 6900

    for value in (wrong_line, wrong_total):
        with pytest.raises((ApprovalRefused, ZeptoSchemaError)):
            quote_from_cod_preview(two, value, NOW)
