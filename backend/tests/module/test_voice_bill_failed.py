import json

import pytest

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.shopping.approval import ApprovalService
from app.shopping.errors import ProviderUnavailable
from app.shopping.models import Basket, Quote
from app.shopping.order_confirmation import OrderConfirmations
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import World

HOUSEHOLD = "user_bill_failed"
REASON = (
    "Zepto's bill preview could not be verified because the item count was invalid. "
    "Nothing was ordered. Ref 1a2b3c4d."
)


class FailingProvider:
    name = "zepto"

    async def quote(self, basket: Basket) -> Quote:
        raise ProviderUnavailable(REASON)


class Searcher:
    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=[Product(
            product_id="z1", sku_id="sku-z1", spin_id="spin-z1", name="Tata Tea Gold", brand="Tata",
            pack_size="500 g", price=Price(mrp=300, offer_price=285), available=True,
        )])


async def call(box: VoiceToolbox, name: str, **arguments: object) -> dict:
    return (await box.execute(name, json.dumps(arguments))).model_output


@pytest.fixture
async def box() -> VoiceToolbox:
    world = World()
    approvals = ApprovalService(world.basket_repo, world.approval_repo, lambda name: FailingProvider(), world.clock)
    toolbox = VoiceToolbox(
        Searcher(), world.baskets, approvals, None, Searcher(), "zepto-home", household_id=HOUSEHOLD,
        order_confirmations=OrderConfirmations(world.basket_repo, approvals, world.clock),
    )
    await call(toolbox, "search_products", query="Tata Tea Gold")
    await call(toolbox, "add_to_basket", option_id="opt1", quantity=2)
    return toolbox


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["request_approval", "prepare_order"])
async def test_provider_bill_failure_reports_reason_and_forbids_workarounds(box, tool) -> None:
    result = await call(box, tool, store="zepto")

    assert result["status"] == "bill_failed"
    assert result["reason"] == REASON
    assert "Do not change the basket yourself" in result["message"]
    assert "try again" in result["message"]
    basket = await call(box, "view_basket", store="zepto")
    assert [item["quantity"] for item in basket["items"]] == [2]
