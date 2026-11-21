import json

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.shopping.approval import ApprovalService
from app.shopping.models import ApprovalStatus, ProviderName
from app.voice.basket_tools import BASKET_TOOL_DEFINITIONS
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import FakeProvider, World


TEST_HOUSEHOLD_ID = "user_test_voice"
PROVIDER: ProviderName = "swiggy_instamart"


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
        FakeSearcher(products), world.baskets, world.approvals, address_id,
        household_id=TEST_HOUSEHOLD_ID,
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
    basket = await world.baskets.active(TEST_HOUSEHOLD_ID, PROVIDER)
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
    assert (await world.baskets.active(TEST_HOUSEHOLD_ID, PROVIDER)).lines == []


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


def zepto_product(name: str, offer: float, mrp: float) -> Product:
    return Product(
        product_id=f"zid_{name}",
        sku_id=f"store_product_{name}",
        spin_id=f"variant_{name}",
        name=name,
        brand="Amul",
        pack_size="500 ml",
        price=Price(mrp=mrp, offer_price=offer),
        available=True,
    )


class TwoStoreWorld(World):
    def __init__(self) -> None:
        super().__init__()
        self.zepto = FakeProvider()
        self.zepto.name = "zepto"
        providers = {"swiggy_instamart": self.provider, "zepto": self.zepto}
        self.approvals = ApprovalService(
            self.basket_repo, self.approval_repo, lambda name: providers[name], self.clock
        )


def two_store_box(
    world: World,
    swiggy: list[Product],
    zepto: list[Product],
    zepto_address_id: str | None = "zepto_home",
) -> VoiceToolbox:
    return VoiceToolbox(
        FakeSearcher(swiggy),
        world.baskets,
        world.approvals,
        "swiggy_home",
        FakeSearcher(zepto),
        zepto_address_id,
        household_id=TEST_HOUSEHOLD_ID,
    )


def option_for(search: dict, store: str) -> str:
    return next(r["option_id"] for r in search["model_output"]["results"] if r["provider"] == store)


async def test_voice_adds_zepto_option_to_zepto_basket_like_the_add_button() -> None:
    world = TwoStoreWorld()
    box = two_store_box(world, [product("Oil", 154, 180)], [zepto_product("Milk", 27.5, 30)])
    search = await call(box, "search_products", query="Amul milk")
    zepto_result = next(r for r in search["model_output"]["results"] if r["provider"] == "Zepto")
    assert zepto_result["add_supported"] is True
    assert zepto_result["store_id"] == "zepto"

    added = await call(box, "add_to_basket", option_id=zepto_result["option_id"], quantity=2)

    assert added["model_output"]["status"] == "added"
    assert added["model_output"]["store"] == "Zepto"
    assert added["model_output"]["item_total"] == "₹55"
    assert added["ui_event"]["provider"] == "zepto"
    assert added["ui_event"]["basket"]["provider"] == "zepto"
    zepto_basket = await world.baskets.active(TEST_HOUSEHOLD_ID, "zepto")
    assert zepto_basket.address_id == "zepto_home"
    line = zepto_basket.lines[0]
    # Same ref the on-screen Add button sends (frontend/app/page.tsx addProduct).
    assert line.ref.model_dump() == {
        "provider": "zepto",
        "product_id": "zid_Milk",
        "variant_id": "variant_Milk",
        "sku_id": "store_product_Milk",
    }
    assert (line.unit_price_paise, line.mrp_paise, line.quantity) == (2750, 3000, 2)
    assert (await world.baskets.active(TEST_HOUSEHOLD_ID, PROVIDER)).lines == []


async def test_voice_option_store_cannot_be_overridden_by_model_arguments() -> None:
    world = TwoStoreWorld()
    box = two_store_box(world, [product("Oil", 154, 180)], [zepto_product("Milk", 27.5, 30)])
    search = await call(box, "search_products", query="milk")

    added = await call(
        box,
        "add_to_basket",
        option_id=option_for(search, "Zepto"),
        quantity=1,
        store="swiggy_instamart",
        unit_price_paise=1,
    )

    assert added["model_output"]["status"] == "added"
    assert added["model_output"]["store_id"] == "zepto"
    assert (await world.baskets.active(TEST_HOUSEHOLD_ID, "zepto")).lines[0].unit_price_paise == 2750
    assert (await world.baskets.active(TEST_HOUSEHOLD_ID, PROVIDER)).lines == []


async def test_voice_view_lists_each_store_basket_separately() -> None:
    world = TwoStoreWorld()
    box = two_store_box(world, [product("Oil", 154, 180)], [zepto_product("Milk", 27.5, 30)])
    search = await call(box, "search_products", query="milk")
    await call(box, "add_to_basket", option_id=option_for(search, "Swiggy Instamart"), quantity=1)
    await call(box, "add_to_basket", option_id=option_for(search, "Zepto"), quantity=1)

    everything = await call(box, "view_basket", store=None)
    only_zepto = await call(box, "view_basket", store="zepto")

    baskets = {b["store"]: b for b in everything["model_output"]["baskets"]}
    assert baskets["Swiggy Instamart"]["item_total"] == "₹154"
    assert baskets["Zepto"]["item_total"] == "₹27.50"
    assert everything["ui_event"] is None
    assert only_zepto["model_output"]["store"] == "Zepto"
    assert [i["name"] for i in only_zepto["model_output"]["items"]] == ["Milk"]
    assert only_zepto["ui_event"]["provider"] == "zepto"


async def test_voice_quantity_change_infers_store_from_item_or_uses_given_store() -> None:
    world = TwoStoreWorld()
    box = two_store_box(world, [product("Oil", 154, 180)], [zepto_product("Milk", 27.5, 30)])
    search = await call(box, "search_products", query="milk")
    await call(box, "add_to_basket", option_id=option_for(search, "Swiggy Instamart"), quantity=1)
    await call(box, "add_to_basket", option_id=option_for(search, "Zepto"), quantity=1)

    inferred = await call(box, "set_basket_quantity", item_id="variant_Milk", quantity=3, store=None)
    explicit = await call(box, "set_basket_quantity", item_id="spin_Oil", quantity=0, store="swiggy_instamart")
    wrong_store = await call(box, "set_basket_quantity", item_id="spin_Oil", quantity=1, store="zepto")
    unknown = await call(box, "set_basket_quantity", item_id="nope", quantity=1, store=None)

    assert inferred["model_output"]["store_id"] == "zepto"
    assert inferred["model_output"]["item_total"] == "₹82.50"
    assert explicit["model_output"]["items"] == []
    assert wrong_store["model_output"]["status"] == "error"
    assert unknown["model_output"]["status"] == "error"


async def test_voice_quantity_change_asks_when_item_id_is_in_both_baskets() -> None:
    world = TwoStoreWorld()
    same = product("Milk", 30, 30)
    box = two_store_box(world, [same], [same])
    search = await call(box, "search_products", query="milk")
    await call(box, "add_to_basket", option_id=option_for(search, "Swiggy Instamart"), quantity=1)
    await call(box, "add_to_basket", option_id=option_for(search, "Zepto"), quantity=1)

    result = await call(box, "set_basket_quantity", item_id="spin_Milk", quantity=2, store=None)

    assert result["model_output"]["status"] == "needs_store"
    for provider in ("swiggy_instamart", "zepto"):
        assert (await world.baskets.active(TEST_HOUSEHOLD_ID, provider)).lines[0].quantity == 1


async def test_voice_request_approval_asks_which_store_when_both_have_items() -> None:
    world = TwoStoreWorld()
    box = two_store_box(world, [product("Oil", 154, 180)], [zepto_product("Milk", 27.5, 30)])
    search = await call(box, "search_products", query="milk")
    await call(box, "add_to_basket", option_id=option_for(search, "Swiggy Instamart"), quantity=1)
    await call(box, "add_to_basket", option_id=option_for(search, "Zepto"), quantity=1)

    ambiguous = await call(box, "request_approval", store=None)

    assert ambiguous["model_output"]["status"] == "needs_store"
    assert ambiguous["ui_event"] is None
    assert world.provider.quotes == 0 and world.zepto.quotes == 0

    zepto = await call(box, "request_approval", store="zepto")

    assert zepto["model_output"]["status"] == "awaiting_user_approval"
    assert zepto["model_output"]["store"] == "Zepto"
    assert zepto["model_output"]["payable"] == "₹57.50"
    assert zepto["ui_event"]["provider"] == "zepto"
    assert world.zepto.quotes == 1 and world.provider.quotes == 0
    zepto_basket = await world.baskets.active(TEST_HOUSEHOLD_ID, "zepto")
    approvals = await world.approval_repo.for_basket(zepto_basket.id)
    assert [approval.status for approval in approvals] == [ApprovalStatus.PENDING]


async def test_voice_request_approval_infers_the_only_filled_store() -> None:
    world = TwoStoreWorld()
    box = two_store_box(world, [product("Oil", 154, 180)], [zepto_product("Milk", 27.5, 30)])
    search = await call(box, "search_products", query="milk")
    await call(box, "add_to_basket", option_id=option_for(search, "Zepto"), quantity=1)

    result = await call(box, "request_approval", store=None)

    assert result["model_output"]["store_id"] == "zepto"
    assert world.zepto.quotes == 1 and world.provider.quotes == 0


async def test_voice_zepto_approval_requires_zepto_address() -> None:
    world = TwoStoreWorld()
    box = two_store_box(world, [], [], zepto_address_id=None)
    world_line_box = two_store_box(world, [], [zepto_product("Milk", 27.5, 30)])
    search = await call(world_line_box, "search_products", query="milk")
    await call(world_line_box, "add_to_basket", option_id=option_for(search, "Zepto"), quantity=1)

    result = await call(box, "request_approval", store="zepto")

    assert result["model_output"]["status"] == "error"
    assert "Zepto delivery address" in result["model_output"]["message"]
    assert world.zepto.quotes == 0


async def test_voice_basket_tools_reject_zepto_when_not_connected() -> None:
    world = TwoStoreWorld()
    box = toolbox(world, [product("Oil", 154, 180)])

    viewed = await call(box, "view_basket", store="zepto")
    approval = await call(box, "request_approval", store="zepto")

    assert viewed["model_output"]["status"] == "error"
    assert "Zepto is not connected" in approval["model_output"]["message"]


def test_basket_tool_schemas_stay_strict_with_every_property_required() -> None:
    for tool in BASKET_TOOL_DEFINITIONS:
        parameters = tool["parameters"]
        assert tool["strict"] is True
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])
