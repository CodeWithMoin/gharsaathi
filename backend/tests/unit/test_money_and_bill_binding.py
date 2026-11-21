"""Pure money arithmetic and exact-bill binding (no I/O)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.shopping.approval import check_quote
from app.shopping.basket import (
    format_rupees,
    item_total_paise,
    line_savings_paise,
    same_variant,
    savings_vs_mrp_paise,
)
from app.shopping.errors import ApprovalRefused
from app.shopping.models import Basket, BasketLine, ProductRef, Quote, basket_fingerprint, rupees_to_paise

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def _line(variant: str, *, price: int = 10000, quantity: int = 1, mrp: int | None = None, **extra) -> BasketLine:
    return BasketLine(
        ref=ProductRef(provider="zepto", product_id=f"p-{variant}", variant_id=variant, sku_id=extra.pop("sku", None)),
        name=extra.pop("name", variant),
        brand=extra.pop("brand", None),
        unit_price_paise=price,
        mrp_paise=mrp,
        quantity=quantity,
    )


def _basket(*lines: BasketLine, address: str | None = "home", revision: int = 3) -> Basket:
    return Basket(
        id="b1",
        household_id="h1",
        provider="zepto",
        address_id=address,
        lines=list(lines),
        revision=revision,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("value", "paise"),
    [
        (155, 15500),
        ("154.5", 15450),
        (154.5, 15450),
        (0.1 + 0.2, 30),  # float noise must not leak into paise
        ("0.005", 1),  # half-up, never banker's rounding
        ("0.004", 0),
        (Decimal("1999.99"), 199999),
        ("0", 0),
    ],
)
def test_rupees_to_paise_is_exact_and_rounds_half_up(value, paise: int) -> None:
    assert rupees_to_paise(value) == paise


@pytest.mark.parametrize(
    ("paise", "text"),
    [
        (0, "₹0"),
        (5, "₹0.05"),
        (15450, "₹154.50"),
        (100000, "₹1,000"),
        (12345678, "₹1,23,456.78"),
        (1234567800, "₹1,23,45,678"),
        (-2500, "-₹25"),
    ],
)
def test_format_rupees_uses_indian_grouping_and_exact_paise(paise: int, text: str) -> None:
    assert format_rupees(paise) == text


def test_totals_and_savings_ignore_mrp_below_the_selling_price() -> None:
    discounted = _line("oil", price=15100, quantity=2, mrp=17500)
    overpriced = _line("tea", price=30000, quantity=1, mrp=29900)
    no_mrp = _line("eggs", price=9200, quantity=3)
    basket = _basket(discounted, overpriced, no_mrp)

    assert item_total_paise(basket) == 2 * 15100 + 30000 + 3 * 9200
    assert line_savings_paise(discounted) == 2 * 2400
    assert line_savings_paise(overpriced) == 0
    assert line_savings_paise(no_mrp) == 0
    assert savings_vs_mrp_paise(basket) == 4800


def test_same_variant_ignores_sku_but_not_provider_product_or_variant() -> None:
    base = ProductRef(provider="zepto", product_id="p", variant_id="v", sku_id="s1")
    assert same_variant(base, base.model_copy(update={"sku_id": "s2"}))
    for change in ({"provider": "swiggy_instamart"}, {"product_id": "q"}, {"variant_id": "w"}):
        assert not same_variant(base, base.model_copy(update=change))


def test_fingerprint_ignores_line_order_and_display_text() -> None:
    first = _basket(_line("a", name="Atta"), _line("b", name="Dal"))
    reordered = _basket(_line("b", name="Toor dal", brand="Tata"), _line("a", name="Atta 5kg"))
    assert basket_fingerprint(first, 30000) == basket_fingerprint(reordered, 30000)


@pytest.mark.parametrize(
    "changed",
    [
        _basket(_line("a", quantity=2)),
        _basket(_line("a", price=10001)),
        _basket(_line("a", sku="other-sku")),
        _basket(_line("a"), address="office"),
        _basket(_line("a"), _line("b")),
        _basket(),
    ],
    ids=["quantity", "unit-price", "sku", "address", "extra-line", "empty"],
)
def test_fingerprint_changes_with_every_commercial_detail(changed: Basket) -> None:
    original = _basket(_line("a"))
    assert basket_fingerprint(changed, 10000) != basket_fingerprint(original, 10000)


def test_fingerprint_is_bound_to_the_payable_amount() -> None:
    basket = _basket(_line("a"))
    assert basket_fingerprint(basket, 10000) != basket_fingerprint(basket, 10001)
    assert len(basket_fingerprint(basket, 10000)) == 64


def _quote(basket: Basket, **changes) -> Quote:
    values = {
        "provider": basket.provider,
        "basket_id": basket.id,
        "basket_revision": basket.revision,
        "item_total_paise": item_total_paise(basket),
        "payable_paise": item_total_paise(basket),
        "quoted_at": NOW,
    }
    values.update(changes)
    return Quote(**values)


def test_check_quote_accepts_only_the_exact_basket_revision() -> None:
    basket = _basket(_line("a"))
    check_quote(basket, _quote(basket))
    for change in ({"basket_revision": 2}, {"basket_id": "b2"}, {"provider": "swiggy_instamart"}):
        with pytest.raises(ApprovalRefused, match="basket changed"):
            check_quote(basket, _quote(basket, **change))


def test_check_quote_names_unavailable_items() -> None:
    basket = _basket(_line("atta", name="Aashirvaad Atta"), _line("dal", name="Toor Dal"))
    gone = ProductRef(provider="zepto", product_id="p-dal", variant_id="dal", sku_id="changed")
    with pytest.raises(ApprovalRefused, match="Not available right now: Toor Dal"):
        check_quote(basket, _quote(basket, unavailable_refs=[gone]))
    unknown = ProductRef(provider="zepto", product_id="p-x", variant_id="x")
    with pytest.raises(ApprovalRefused, match="Some items"):
        check_quote(basket, _quote(basket, unavailable_refs=[unknown]))
