"""Whether a provider bill priced the items as the basket did (no I/O)."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from app.commerce.zepto.checkout import quote_from_cod_preview
from app.shopping.basket import item_total_paise, quote_item_total_matches
from app.shopping.models import Basket, BasketLine, ProductRef, Quote

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent.parent / "fixtures" / "zepto_cod_preview.json"


def _line(variant: str, name: str, price: int, mrp: int | None) -> BasketLine:
    return BasketLine(
        ref=ProductRef(provider="zepto", product_id=f"p-{variant}", variant_id=f"pv-{variant}", sku_id=f"sp-{variant}"),
        name=name,
        unit_price_paise=price,
        mrp_paise=mrp,
        quantity=1,
    )


def _basket() -> Basket:
    # The real session: AXE at ₹172 (MRP ₹245) and Diet Coke at ₹50.
    return Basket(
        id="b1",
        household_id="h1",
        provider="zepto",
        address_id="addr_home",
        revision=2,
        created_at=NOW,
        updated_at=NOW,
        lines=[_line("axe", "AXE deodorant", 17200, 24500), _line("coke", "Diet Coke", 5000, 5000)],
    )


def _preview(*, axe_offer: int = 17200, offers: list[dict] | None = None, coupon: int = 0) -> dict:
    preview = copy.deepcopy(json.loads(FIXTURE.read_text()))
    data = preview["data"]
    subtotal = 24500 + 5000
    discount = (24500 - axe_offer) + coupon
    data["items"] = [
        {"productVariantId": "pv-axe", "storeProductId": "sp-axe", "quantity": 1},
        {"productVariantId": "pv-coke", "storeProductId": "sp-coke", "quantity": 1},
    ]
    data["totalItems"] = 2
    data["subTotal"] = subtotal
    data["discount"] = discount
    data["deliveryFee"] = 0
    data["deliveryFeeDiscount"] = 3000
    data["deliveryFeePreDiscount"] = 3000
    data["packagingFee"] = 0
    data["freeDelivery"] = None
    data["offers"] = offers or []
    data["toPayAmount"] = subtotal - discount
    return preview


def test_zepto_mrp_subtotal_with_item_discount_matches_the_basket() -> None:
    basket = _basket()
    quote = quote_from_cod_preview(basket, _preview(), NOW)

    assert (quote.item_total_paise, quote.payable_paise) == (29500, 22200)
    assert item_total_paise(basket) == 22200
    assert quote.item_discount_paise == 7300
    assert quote_item_total_matches(quote, basket) is True


def test_a_five_rupee_offer_price_increase_is_flagged() -> None:
    basket = _basket()
    quote = quote_from_cod_preview(basket, _preview(axe_offer=17700), NOW)

    assert quote.payable_paise == 22700
    assert quote_item_total_matches(quote, basket) is False


def test_an_applied_coupon_is_not_mistaken_for_a_price_change() -> None:
    coupon = {
        "title": "₹20 off",
        "type": "COUPON",
        "minimumOrderValue": 1000,
        "discountAmount": 2000,
        "isUnlocked": True,
        "isApplied": True,
        "isAutoApplicable": True,
    }
    basket = _basket()
    quote = quote_from_cod_preview(basket, _preview(offers=[coupon], coupon=2000), NOW)

    assert quote.payable_paise == 20200
    assert quote.item_discount_paise == 7300
    assert quote_item_total_matches(quote, basket) is True


def test_an_unreadable_applied_offer_keeps_the_check_conservative() -> None:
    coupon = {"title": "Mystery", "type": "COUPON", "discountAmount": "twenty", "isApplied": True}
    basket = _basket()
    quote = quote_from_cod_preview(basket, _preview(offers=[coupon], coupon=2000), NOW)

    assert quote_item_total_matches(quote, basket) is False


def _quote(item_total: int, item_discount: int = 0) -> Quote:
    return Quote(
        provider="zepto",
        basket_id="b1",
        basket_revision=2,
        item_total_paise=item_total,
        item_discount_paise=item_discount,
        payable_paise=item_total - item_discount,
        quoted_at=NOW,
    )


def test_selling_price_item_totals_compare_directly() -> None:
    basket = _basket()
    assert quote_item_total_matches(_quote(22200), basket) is True
    assert quote_item_total_matches(_quote(22700), basket) is False


def test_an_mrp_change_is_flagged_even_when_the_net_total_agrees() -> None:
    basket = _basket()
    assert quote_item_total_matches(_quote(30000, 7800), basket) is False


def test_approval_response_does_not_warn_on_the_valid_zepto_bill() -> None:
    from app.api.basket import approval_request_out
    from app.shopping.approval import ApprovalRequest
    from app.shopping.models import Approval

    basket = _basket()
    quote = quote_from_cod_preview(basket, _preview(), NOW)
    approval = Approval(
        id="a1", basket_id="b1", basket_revision=2, fingerprint="a" * 64,
        payable_paise=quote.payable_paise, created_at=NOW, expires_at=NOW,
    )
    response = approval_request_out(ApprovalRequest(approval=approval, quote=quote, basket=basket))

    assert response.item_total_matches is True
    assert (response.quote.item_total.paise, response.quote.payable.paise) == (29500, 22200)
