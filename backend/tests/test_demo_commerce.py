from datetime import UTC, datetime

import pytest

from app.commerce.demo import (
    DEMO_ADDRESS_ID,
    DemoQuoteProvider,
    DemoSwiggyService,
    DemoZeptoService,
)
from app.shopping.models import Basket, BasketLine, ProductRef


@pytest.mark.asyncio
async def test_demo_search_and_addresses_are_self_contained() -> None:
    service = DemoSwiggyService()

    assert (await service.connection_status()).mode == "demo"
    addresses = await service.addresses(1, 10)
    assert addresses.resolution.default_address_id == DEMO_ADDRESS_ID

    result = await service.search("atta oil aur chai", DEMO_ADDRESS_ID, 0)
    names = {product.name for product in result.products}
    assert "Aashirvaad Shudh Chakki Atta" in names
    assert "Fortune Sunflower Oil" in names
    assert "Tata Tea Gold" in names


@pytest.mark.asyncio
async def test_demo_stores_return_comparable_but_distinct_offers() -> None:
    swiggy = await DemoSwiggyService().search("rice", DEMO_ADDRESS_ID, 0)
    zepto = await DemoZeptoService().search("rice", DEMO_ADDRESS_ID, 0)

    assert swiggy.products[0].name == zepto.products[0].name
    assert swiggy.products[0].price.offer_price == 699
    assert zepto.products[0].price.offer_price == 675
    assert swiggy.products[0].delivery_value == "18"
    assert zepto.products[0].delivery_value == "11"
    assert zepto.products[0].sku_id.startswith("zepto-")


@pytest.mark.asyncio
async def test_demo_quote_uses_deterministic_local_fees() -> None:
    now = datetime.now(UTC)
    basket = Basket(
        id="demo-basket",
        household_id="demo-household",
        provider="swiggy_instamart",
        address_id=DEMO_ADDRESS_ID,
        lines=[
            BasketLine(
                ref=ProductRef(
                    provider="swiggy_instamart",
                    product_id="demo-oil",
                    variant_id="demo-spin-oil",
                ),
                name="Fortune Sunflower Oil",
                unit_price_paise=15500,
                quantity=2,
            )
        ],
        created_at=now,
        updated_at=now,
    )

    quote = await DemoQuoteProvider().quote(basket)

    assert quote.item_total_paise == 31000
    assert quote.payable_paise == 34000
    assert [(charge.label, charge.amount_paise) for charge in quote.charges] == [
        ("Handling fee · demo", 500),
        ("Delivery fee · demo", 2500),
    ]
