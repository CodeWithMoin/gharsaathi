"""Item prices serialise with the ₹ strings the product cards show as-is."""

from app.commerce.swiggy.models import Price
from app.conversations.schemas import ProductSnapshot


def test_display_strings_come_from_the_one_rupee_formatter() -> None:
    price = Price(mrp=1299.5, offer_price=1249.5).model_dump()
    assert price["offer_display"] == "₹1,249.50"
    assert price["mrp_display"] == "₹1,299.50"
    assert price["saving_display"] == "₹50"


def test_no_mrp_or_saving_when_the_offer_is_not_below_mrp() -> None:
    for mrp, offer in ((72, 72), (60, 72)):
        price = Price(mrp=mrp, offer_price=offer).model_dump()
        assert price["offer_display"] == "₹72"
        assert price["mrp_display"] is None
        assert price["saving_display"] is None


def test_float_rupees_are_rounded_to_paise_before_subtracting() -> None:
    # 0.1 + 0.2 style float noise must not leak into the saving string.
    price = Price(mrp=30.3, offer_price=30.1).model_dump()
    assert price["saving_display"] == "₹0.20"


def test_saved_chat_products_regain_display_strings_and_keep_annotations() -> None:
    saved = ProductSnapshot.model_validate(
        {
            "product_id": "p1",
            "sku_id": "s1",
            "name": "Amul Taaza",
            "price": {"mrp": 72, "offer_price": 69, "offer_display": "₹1"},
            "available": True,
            "remembered": "purchase_history",
            "preferred": True,
        }
    ).model_dump()
    assert saved["price"]["offer_display"] == "₹69"
    assert saved["price"]["saving_display"] == "₹3"
    assert (saved["remembered"], saved["preferred"], saved["avoided"]) == ("purchase_history", True, None)
