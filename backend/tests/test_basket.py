import pytest

from app.shopping.basket import format_rupees, item_total_paise, savings_vs_mrp_paise
from app.shopping.errors import InvalidBasketChange, NotFound
from app.shopping.models import ApprovalStatus, BasketStatus, ProductRef
from tests.shopping_fakes import HOUSEHOLD, PROVIDER, World, line


async def test_active_basket_is_created_once_per_household_and_provider() -> None:
    world = World()
    first = await world.baskets.active(HOUSEHOLD, PROVIDER)
    again = await world.baskets.active(HOUSEHOLD, PROVIDER)
    other = await world.baskets.active(HOUSEHOLD, "zepto")
    assert first.id == again.id
    assert other.id != first.id
    assert first.revision == 0 and first.status == BasketStatus.DRAFT


async def test_add_merges_same_variant_and_bumps_revision() -> None:
    world = World()
    await world.baskets.add_line(HOUSEHOLD, PROVIDER, line(quantity=2))
    basket = await world.baskets.add_line(HOUSEHOLD, PROVIDER, line(quantity=3, price=15000))
    assert len(basket.lines) == 1
    assert basket.lines[0].quantity == 5
    assert basket.lines[0].unit_price_paise == 15000
    assert basket.revision == 2


async def test_quantity_limits_and_zero_removes() -> None:
    world = World()
    await world.baskets.add_line(HOUSEHOLD, PROVIDER, line(quantity=49))
    with pytest.raises(InvalidBasketChange):
        await world.baskets.add_line(HOUSEHOLD, PROVIDER, line(quantity=2))
    basket = await world.baskets.set_quantity(HOUSEHOLD, PROVIDER, "spin1", 0)
    assert basket.lines == []
    with pytest.raises(NotFound):
        await world.baskets.remove_line(HOUSEHOLD, PROVIDER, "spin1")


async def test_line_must_belong_to_basket_provider() -> None:
    world = World()
    with pytest.raises(InvalidBasketChange):
        await world.baskets.add_line(
            HOUSEHOLD,
            PROVIDER,
            line(ref=ProductRef(provider="zepto", product_id="p", variant_id="v")),
        )


async def test_changes_supersede_open_approval() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    changed = await world.baskets.set_quantity(HOUSEHOLD, PROVIDER, "spin1", 2)
    stored = await world.approval_repo.get(requested.approval.id)
    assert changed.status == BasketStatus.DRAFT
    assert stored is not None and stored.status == ApprovalStatus.SUPERSEDED


async def test_totals_and_savings_are_integer_paise() -> None:
    world = World()
    await world.baskets.add_line(HOUSEHOLD, PROVIDER, line(price=15400, quantity=3))
    basket = await world.baskets.add_line(
        HOUSEHOLD, PROVIDER, line("spin2", price=9950, quantity=1, mrp_paise=None)
    )
    assert item_total_paise(basket) == 15400 * 3 + 9950
    assert savings_vs_mrp_paise(basket) == (18000 - 15400) * 3


def test_format_rupees_uses_indian_grouping() -> None:
    assert format_rupees(15400) == "₹154"
    assert format_rupees(12345678905) == "₹12,34,56,789.05"
