"""Deterministic comparison maths for compare_options (app/shopping/compare.py)."""

from decimal import Decimal

from app.shopping.compare import (
    NOT_COMPARABLE,
    CompareItem,
    PackSize,
    compare,
    parse_pack,
    price_difference,
    unit_price_difference,
    unit_price_paise,
)


def test_parse_units_and_multipacks() -> None:
    assert parse_pack("500 g") == PackSize("mass", Decimal(500))
    assert parse_pack("1 kg") == PackSize("mass", Decimal(1000))
    assert parse_pack("1.5 L") == PackSize("volume", Decimal(1500))
    assert parse_pack("200 ml") == PackSize("volume", Decimal(200))
    assert parse_pack("12 pcs") == PackSize("count", Decimal(12))
    assert parse_pack("6 x 330 ml") == PackSize("volume", Decimal(1980), 6)
    assert parse_pack("330 ml x 6") == PackSize("volume", Decimal(1980), 6)
    assert parse_pack("Pack of 2, 400 g") == PackSize("mass", Decimal(800), 2)
    assert parse_pack("1 kg (2 x 500 g)") == PackSize("mass", Decimal(1000))
    assert parse_pack("1 m") is None and parse_pack("") is None and parse_pack(None) is None


def test_unit_price_rounds_half_up_in_paise() -> None:
    assert unit_price_paise(2000, PackSize("volume", Decimal(200))) == 1000
    assert unit_price_paise(4500, PackSize("volume", Decimal(330))) == 1364  # 1363.6…
    assert unit_price_paise(100, PackSize("mass", Decimal(800))) == 13  # 12.5 -> 13
    assert unit_price_paise(9900, PackSize("count", Decimal(12))) == 825


def test_difference_strings() -> None:
    assert price_difference(2000, 2000) == "same price"
    assert price_difference(4200, 3000) == "₹12 cheaper"
    assert unit_price_difference((1000, "volume"), (650, "volume")) == "₹3.50 cheaper per 100 ml"
    assert unit_price_difference((1000, "volume"), (1000, "volume")) == "same per 100 ml"
    assert unit_price_difference((1000, "volume"), (1000, "mass")) == NOT_COMPARABLE
    assert unit_price_difference(None, (1000, "mass")) == NOT_COMPARABLE


def item(ref: int, name: str, store: str, price: int, pack: str | None, available: bool = True) -> CompareItem:
    return CompareItem(ref, name, store, "zepto" if store == "Zepto" else "swiggy_instamart", price, available, pack)


def test_same_price_lassi() -> None:
    facts = compare([item(1, "Amul Sweet Lassi", "Zepto", 2000, "200 ml"), item(2, "Amul Rose Lassi", "Zepto", 2000, "200 ml")])
    pair = facts["pairs"][0]
    assert pair["price_line"] == "same price" and pair["unit_price_line"] == "same per 100 ml"
    assert "cheapest" not in facts and facts["better_value"] is None and facts["all_in_stock_same_price"]
    assert [o["unit_price"] for o in facts["options"]] == ["₹10 per 100 ml", "₹10 per 100 ml"]


def test_incomparable_units_and_unparseable_packs() -> None:
    facts = compare([item(1, "Ghee", "Zepto", 30000, "500 ml"), item(2, "Ghee Jar", "Zepto", 28000, "500 g"), item(3, "Ghee Tin", "Zepto", 9000, None)])
    assert facts["options"][2]["unit_price"] == NOT_COMPARABLE
    assert all(pair["unit_price_difference"] == NOT_COMPARABLE for pair in facts["pairs"])
    assert facts["better_value"] is None and facts["value_note"] == NOT_COMPARABLE


def test_best_value_excludes_out_of_stock() -> None:
    facts = compare([
        item(1, "Diet Coke", "Zepto", 4500, "330 ml"),
        item(2, "Diet Coke", "Zepto", 23000, "6 x 330 ml", available=False),
        item(3, "Diet Coke", "Zepto", 12000, "3 x 330 ml"),
    ])
    assert facts["better_value"]["ref"] == 3 and facts["better_value"]["badge"] == "Best value per 100 ml"
    assert facts["cheapest"]["ref"] == 1
    assert any(pair.get("out_of_stock") == [2] for pair in facts["pairs"])


def test_same_product_different_store() -> None:
    facts = compare([item(1, "Mother Dairy Chaach", "Swiggy Instamart", 3000, "500 ml"), item(2, "Mother Dairy Chaach", "Zepto", 4200, "500 ml")])
    pair = facts["pairs"][0]
    assert pair["same_product_different_store"] and facts["same_product_different_store"]
    assert pair["price_line"] == "Swiggy Instamart is ₹12 cheaper"
    assert facts["options"][0]["unit_price"] == "₹6 per 100 ml"
