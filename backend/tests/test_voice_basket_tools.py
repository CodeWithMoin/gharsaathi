import json

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.shopping.models import ApprovalStatus
from app.voice.basket_tools import HOUSEHOLD_ID, PROVIDER
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import World


def product(name: str, offer: float, mrp: float, available: bool = True) -> Product:
    return Product(
        product_id=f"p_{name}",
        sku_id=f"sku_{name}",
        spin_id=f"spin_{name}",
        name=name,
        brand="Fortune",
        pack_size="1 L",
        price=Price(mrp=mrp, offer_price=offer),
        available=available,
    )


class FakeSearcher:
    def __init__(self, products: list[Product]) -> None:
        self.products = products

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=self.products)


def toolbox(world: World, products: list[Product], address_id: str | None = "addr_home") -> VoiceToolbox:
    return VoiceToolbox(
        FakeSearcher(products), world.baskets, world.approvals, address_id
    )


async def call(box: VoiceToolbox, name: str, **arguments: object) -> dict:
    return (await box.execute(name, json.dumps(arguments))).model_dump()


async def test_voice_add_uses_only_search_option_and_provider_price() -> None:
    world = World()
    box = toolbox(world, [product("Sunlite Oil", 154.5, 180)])
    search = await call(box, "search_products", query="Fortune oil")
    option_id = search["model_output"]["results"][0]["option_id"]

    added = await call(box, "add_to_basket", option_id=option_id, quantity=2)

    assert added["model_output"]["status"] == "added"
    assert added["model_output"]["item_total"] == "₹309"
    assert added["ui_event"]["type"] == "basket.updated"
    basket = await world.baskets.active(HOUSEHOLD_ID, PROVIDER)
    line = basket.lines[0]
    assert (line.unit_price_paise, line.mrp_paise, line.quantity) == (15450, 18000, 2)
    assert basket.address_id == "addr_home"


async def test_voice_refuses_invented_or_unavailable_option() -> None:
    world = World()
    box = toolbox(world, [product("Rice Bran", 170, 170, available=False)])
    await call(box, "search_products", query="oil")

    invented = await call(box, "add_to_basket", option_id="not_from_search", quantity=1)
    unavailable = await call(box, "add_to_basket", option_id="opt1", quantity=1)

    assert invented["model_output"]["status"] == "error"
    assert unavailable["model_output"]["status"] == "error"
    assert "unavailable" in unavailable["model_output"]["message"]
    assert (await world.baskets.active(HOUSEHOLD_ID, PROVIDER)).lines == []


async def test_voice_quantity_zero_removes_and_view_emits_basket_event() -> None:
    world = World()
    box = toolbox(world, [product("Oil", 154, 180)])
    await call(box, "search_products", query="oil")
    added = await call(box, "add_to_basket", option_id="opt1", quantity=1)
    item_id = added["model_output"]["items"][0]["item_id"]

    removed = await call(box, "set_basket_quantity", item_id=item_id, quantity=0)
    viewed = await call(box, "view_basket")

    assert removed["model_output"]["items"] == []
    assert viewed["ui_event"]["type"] == "basket.updated"
    assert viewed["ui_event"]["basket"]["item_count"] == 0


async def test_voice_request_approval_only_requests_review_never_places_order() -> None:
    world = World()
    box = toolbox(world, [product("Oil", 154, 180)])
    await call(box, "search_products", query="oil")
    await call(box, "add_to_basket", option_id="opt1", quantity=1)

    result = await call(box, "request_approval")

    assert result["model_output"]["status"] == "awaiting_user_approval"
    assert result["ui_event"]["type"] == "basket.approval_requested"
    assert result["ui_event"]["approval"]["payable"] == "₹184"
    approvals = await world.approval_repo.for_basket(result["ui_event"]["approval"]["basket_id"])
    assert [approval.status for approval in approvals] == [ApprovalStatus.PENDING]
