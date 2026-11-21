import pytest

from app.commerce.zepto.cart import parse_cart, parse_view_cart
from app.commerce.zepto.errors import ZeptoSchemaError


def test_parse_view_cart_normalizes_lines_and_reconciles_integer_paise() -> None:
    cart = parse_view_cart(
        {
            "data": {
                "isEmpty": False,
                "totalItems": 3,
                "items": [
                    {
                        "productVariantId": "pvid-oil",
                        "storeProductId": "spid-oil",
                        "quantity": 2,
                        "name": "Fortune Sunflower Oil",
                        "label": "Sunflower Oil",
                        "price": 15450,
                        "mrp": 18000,
                        "imageUrl": "https://images.example.test/oil.png",
                        "packSize": "1 L",
                        "availableQuantity": 4,
                    },
                    {
                        "productVariantId": "pvid-milk",
                        "storeProductId": "spid-milk",
                        "quantity": 1,
                        "price": 6800,
                    },
                ],
            }
        }
    )

    assert cart.total_items == 3
    assert cart.item_total_paise == 15450 * 2 + 6800
    assert cart.items[0].line_total_paise == 30900
    assert cart.items[0].mrp_paise == 18000
    assert cart.items[0].available_quantity == 4
    assert cart.warnings == (
        "Zepto did not provide a cart total.",
        "Zepto did not provide a fee breakdown.",
    )


def test_parse_view_cart_keeps_a_missing_price_as_a_warning_not_a_guess() -> None:
    cart = parse_cart(
        {
            "isEmpty": False,
            "totalItems": 1,
            "items": [
                {
                    "productVariantId": "pvid",
                    "storeProductId": "spid",
                    "quantity": 1,
                    "name": "Unpriced item",
                }
            ],
        }
    )

    assert cart.items[0].unit_price_paise is None
    assert cart.items[0].line_total_paise is None
    assert cart.item_total_paise is None
    assert "could not be calculated" in cart.warnings[0]


def test_parse_view_cart_normalizes_the_verified_empty_shape() -> None:
    cart = parse_view_cart({"isEmpty": True, "totalItems": 0, "items": []})

    assert cart.items == ()
    assert cart.item_total_paise == 0
    assert cart.warnings == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"isEmpty": "false", "totalItems": 0, "items": []},
        {"isEmpty": False, "totalItems": 1, "items": {}},
        {"isEmpty": True, "totalItems": 1, "items": []},
        {
            "isEmpty": False,
            "totalItems": 1,
            "items": [{"productVariantId": "pvid", "storeProductId": "spid", "quantity": 1.0}],
        },
        {
            "isEmpty": False,
            "totalItems": 1,
            "items": [
                {
                    "productVariantId": "pvid",
                    "storeProductId": "spid",
                    "quantity": 1,
                    "price": 12.5,
                }
            ],
        },
    ],
)
def test_parse_view_cart_fails_closed_for_malformed_confirmed_fields(payload: dict) -> None:
    with pytest.raises(ZeptoSchemaError):
        parse_view_cart(payload)
