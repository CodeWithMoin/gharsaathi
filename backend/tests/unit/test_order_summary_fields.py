from datetime import UTC, datetime

from app.shopping.models import BasketLine, Charge, ProductRef, Quote
from app.voice.basket_tools import bill_breakdown, item_summary


def line(name: str, pack_size: str | None, quantity: int = 1) -> BasketLine:
    return BasketLine(
        ref=ProductRef(provider="zepto", product_id=name, variant_id=name, sku_id=name),
        name=name,
        brand=None,
        pack_size=pack_size,
        unit_price_paise=2000,
        quantity=quantity,
        available=True,
    )


def quote(item_total: int, charges: list[int], payable: int) -> Quote:
    return Quote(
        provider="zepto",
        basket_id="b1",
        basket_revision=1,
        item_total_paise=item_total,
        charges=[Charge(label=f"c{index}", amount_paise=amount) for index, amount in enumerate(charges)],
        payable_paise=payable,
        quoted_at=datetime.now(UTC),
    )


def test_item_summary_names_items_and_caps_at_three() -> None:
    assert item_summary([line("Amul Sweet Lassi", "200 ml")]) == "Amul Sweet Lassi 200 ml × 1"
    assert item_summary([line("Diet Coke Can 330 ml", "330 ml", 2)]) == "Diet Coke Can 330 ml × 2"
    many = [line(f"Item {index}", None) for index in range(5)]
    assert item_summary(many) == "Item 0 × 1, Item 1 × 1, Item 2 × 1 +2 more"


def test_bill_breakdown_sums_fees_and_discounts_in_code() -> None:
    fields = bill_breakdown(quote(2000, [1800, 2500], 6300))
    assert fields["fees_total"] == "₹43"
    assert fields["bill_breakdown"] == "₹20 items + ₹43 fees = ₹63"

    discounted = bill_breakdown(quote(30000, [500, -2000], 28500))
    assert discounted["bill_breakdown"] == "₹300 items + ₹5 fees − ₹20 discount = ₹285"
    assert discounted["discount_total"] == "₹20"


def test_bill_breakdown_is_omitted_when_it_would_not_add_up_or_has_no_fees() -> None:
    assert "bill_breakdown" not in bill_breakdown(quote(2000, [500], 9900))
    assert "bill_breakdown" not in bill_breakdown(quote(2000, [], 2000))
