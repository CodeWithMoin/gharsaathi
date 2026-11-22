import json
from datetime import UTC, datetime

import pytest

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.shopping.approval import ApprovalService
from app.shopping.basket import item_total_paise
from app.shopping.models import Basket, Charge, OfferOpportunity, Quote
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import World

HOUSEHOLD = "user_offers"


class OfferProvider:
    name = "zepto"

    def __init__(self) -> None:
        self.quotes = 0

    async def quote(self, basket: Basket) -> Quote:
        self.quotes += 1
        items = item_total_paise(basket)
        return Quote(
            provider="zepto",
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=items,
            charges=[
                Charge(label="Delivery fee", amount_paise=0),
                Charge(label="Discount", amount_paise=-2500),
            ],
            offers=[
                OfferOpportunity(title="Free Delivery", kind="free_delivery", minimum_order_paise=9900,
                                 remaining_paise=0, unlocked=True, applied=True, provider_confirmed=True),
                OfferOpportunity(title="Snacks Saver", kind="promo", minimum_order_paise=50000,
                                 remaining_paise=30000, potential_saving_paise=5000, net_extra_paise=25000,
                                 unlocked=False, applied=False, provider_confirmed=True),
                OfferOpportunity(title="Bank card 10% off", kind="bank_offer", minimum_order_paise=0,
                                 remaining_paise=0, unlocked=True, applied=False, provider_confirmed=False),
            ],
            payable_paise=items - 2500,
            quoted_at=datetime.now(UTC),
        )


class Searcher:
    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=[Product(
            product_id="z1", sku_id="sku-z1", spin_id="spin-z1", name="Diet Coke Can", brand="Coca-Cola",
            pack_size="330 ml", price=Price(mrp=50, offer_price=45), available=True,
        )])


async def call(box: VoiceToolbox, name: str, **arguments: object) -> dict:
    return (await box.execute(name, json.dumps(arguments))).model_output


@pytest.fixture
def setup() -> tuple[VoiceToolbox, OfferProvider]:
    world = World()
    provider = OfferProvider()
    approvals = ApprovalService(world.basket_repo, world.approval_repo, lambda name: provider, world.clock)
    box = VoiceToolbox(Searcher(), world.baskets, approvals, None, Searcher(), "zepto-home", household_id=HOUSEHOLD)
    return box, provider


EXPECTED_OFFERS = [
    {"title": "Free Delivery", "kind": "free_delivery", "status": "applied"},
    {"title": "Snacks Saver", "kind": "promo", "status": "not_unlocked",
     "possible_saving": "₹50", "add_more_to_unlock": "₹300"},
]


@pytest.mark.asyncio
async def test_bill_reports_only_provider_confirmed_offers(setup) -> None:
    box, _ = setup
    await call(box, "search_products", query="Diet Coke")
    await call(box, "add_to_basket", option_id="opt1", quantity=1)

    bill = await call(box, "request_approval", store="zepto")

    assert bill["status"] == "awaiting_user_approval"
    assert bill["bill_offers"] == EXPECTED_OFFERS
    assert bill["promo_code_entry"] == "not_available"
    assert {"label": "Discount", "amount": "-₹25"} in bill["charges"]
    assert "Never invent" in bill["note"]


@pytest.mark.asyncio
async def test_view_basket_shows_current_bill_offers_without_requoting(setup) -> None:
    box, provider = setup
    before = await call(box, "view_basket", store="zepto")
    assert "current_bill" not in before

    await call(box, "search_products", query="Diet Coke")
    await call(box, "add_to_basket", option_id="opt1", quantity=1)
    await call(box, "request_approval", store="zepto")
    viewed = await call(box, "view_basket", store="zepto")
    every = await call(box, "view_basket", store=None)

    assert provider.quotes == 1
    assert viewed["current_bill"]["bill_offers"] == EXPECTED_OFFERS
    assert viewed["current_bill"]["payable"] == "₹20"
    assert "promo or coupon code" in viewed["note"]
    zepto = next(basket for basket in every["baskets"] if basket["store_id"] == "zepto")
    assert zepto["current_bill"]["promo_code_entry"] == "not_available"

    changed = await call(box, "add_to_basket", option_id="opt1", quantity=1)
    assert "current_bill" not in changed
    assert "current_bill" not in await call(box, "view_basket", store="zepto")
