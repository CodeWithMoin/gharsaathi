import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from app.commerce.swiggy.quote_provider import SwiggyQuoteProvider, money_text_to_paise
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import Basket, BasketLine, ProductRef


NOW = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.retry_on_auth: list[bool] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        self.calls.append((name, copy.deepcopy(arguments)))
        self.retry_on_auth.append(retry_on_auth)
        response = self.responses[name]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


def line(spin: str, sku: str, quantity: int, price: int, name: str) -> BasketLine:
    return BasketLine(
        ref=ProductRef(
            provider="swiggy_instamart",
            product_id=f"product_{spin}",
            variant_id=spin,
            sku_id=sku,
        ),
        name=name,
        unit_price_paise=price,
        quantity=quantity,
    )


def basket() -> Basket:
    return Basket(
        id="basket_1",
        household_id="household_1",
        provider="swiggy_instamart",
        address_id="addr_home",
        lines=[
            line("spin_42", "sku_88", 2, 15400, "Fortune Sunflower Oil 1 L"),
            line("spin_77", "sku_99", 1, 6750, "Amul Taaza Milk 1 L"),
        ],
        revision=3,
        created_at=NOW,
        updated_at=NOW,
    )


def cart_item(spin: str, sku: str, quantity: int, price: float, name: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "spinId": spin,
        "skuId": sku,
        "productId": f"product_{spin}",
        "itemName": name,
        "quantity": quantity,
        "isInStockAndAvailable": True,
        "discountedFinalPrice": price,
    }
    item.update(extra)
    return item


CART = {
    "success": True,
    "data": {
        "selectedAddressDetails": {"id": "addr_home"},
        "cartTotalAmount": "₹388.10",
        "items": [
            cart_item("spin_42", "sku_88", 2, 154.0, "Fortune Sunflower Oil 1 L"),
            cart_item("spin_77", "sku_99", 1, 67.5, "Amul Taaza Milk 1 L"),
        ],
        "billBreakdown": {
            "lineItems": [
                {"label": "Item Total", "value": "₹375.50"},
                {"label": "Handling Fee", "value": "₹9.80"},
                {"label": "Delivery Fee", "value": "FREE"},
                {"label": "Small Cart Fee", "value": "₹22.90"},
                {"label": "Item Discount", "value": "-₹20.10"},
            ],
            "toPay": {"label": "To Pay", "value": "₹388.10"},
        },
    },
}


async def test_final_bill_syncs_exact_basket_then_reads_and_reconciles_cart() -> None:
    fake = FakeClient({"update_cart": {"success": True, "data": {}}, "get_cart": CART})
    provider = SwiggyQuoteProvider(fake, clock=lambda: NOW)

    quote = await provider.quote(basket())

    assert fake.calls == [
        (
            "update_cart",
            {
                "selectedAddressId": "addr_home",
                "items": [
                    {"spinId": "spin_42", "skuId": "sku_88", "quantity": 2},
                    {"spinId": "spin_77", "skuId": "sku_99", "quantity": 1},
                ],
            },
        ),
        ("get_cart", {}),
    ]
    assert fake.retry_on_auth == [False, True]
    assert quote.item_total_paise == 37550
    assert quote.payable_paise == 38810
    assert [(charge.label, charge.amount_paise) for charge in quote.charges] == [
        ("Handling Fee", 980),
        ("Delivery Fee", 0),
        ("Small Cart Fee", 2290),
        ("Item Discount", -2010),
    ]
    assert quote.item_total_paise + sum(charge.amount_paise for charge in quote.charges) == quote.payable_paise
    assert quote.quoted_at == NOW
    assert "checkout" not in [name for name, _ in fake.calls]


async def test_final_bill_marks_capped_or_unavailable_lines() -> None:
    payload = copy.deepcopy(CART)
    payload["data"]["items"][0]["quantity"] = 1
    payload["data"]["items"][1]["isInStockAndAvailable"] = False
    payload["data"]["billBreakdown"] = {
        "lineItems": [{"label": "Item Total", "value": "₹221.50"}],
        "toPay": {"label": "To Pay", "value": "₹221.50"},
    }
    fake = FakeClient({"update_cart": {"success": True, "data": {}}, "get_cart": payload})

    quote = await SwiggyQuoteProvider(fake, clock=lambda: NOW).quote(basket())

    assert [reference.variant_id for reference in quote.unavailable_refs] == ["spin_42", "spin_77"]


async def test_final_bill_rejects_wrong_address_and_never_orders() -> None:
    payload = copy.deepcopy(CART)
    payload["data"]["selectedAddressDetails"] = {"id": "addr_other"}
    fake = FakeClient({"update_cart": {"success": True, "data": {}}, "get_cart": payload})

    with pytest.raises(ApprovalRefused, match="different delivery address"):
        await SwiggyQuoteProvider(fake).quote(basket())
    assert [name for name, _ in fake.calls] == ["update_cart", "get_cart"]


async def test_final_bill_normalizes_invalid_provider_payload_without_leaking_details() -> None:
    payload = copy.deepcopy(CART)
    payload["data"]["billBreakdown"]["toPay"]["value"] = "not a price"
    fake = FakeClient({"update_cart": {"success": True, "data": {}}, "get_cart": payload})

    with pytest.raises(ProviderUnavailable, match="invalid final bill"):
        await SwiggyQuoteProvider(fake).quote(basket())


def test_money_parser_uses_integer_paise_without_float_drift() -> None:
    assert money_text_to_paise("₹0.10") + money_text_to_paise("₹0.20") == 30
    assert money_text_to_paise("−₹5") == -500
