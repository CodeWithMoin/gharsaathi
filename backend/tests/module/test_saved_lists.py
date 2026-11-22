"""Saved lists, reorder-last and recent items, and clearing a basket.

SQLite for the list store and the real FastAPI app with in-memory baskets and
fake store searchers at its edges. Nothing here quotes, approves or orders: the
quote provider and the checkout are never wired in, and a test asserts it.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.zepto.errors import ZeptoAuthenticationRequired, ZeptoProviderError
from app.database.base import Base
from app.dependencies import (
    get_basket_service,
    get_ordered_baskets,
    get_saved_list_store,
    get_swiggy_service,
    get_zepto_service,
)
from app.main import app
from app.shopping.basket import BasketService
from app.shopping.errors import BasketConflict
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.models import Approval, ApprovalStatus, Basket, BasketLine, BasketStatus, ProductRef
from app.shopping.saved_lists import ListItem, SavedListStore, StoreTarget, add_items, recent_items
from app.voice.saved_list_tools import SavedListToolExecutor

pytestmark = pytest.mark.module

USER = "household-1"


def product(variant: str, name: str, price: float = 50.0, available: bool = True, provider: str = "zepto") -> Product:
    return Product(
        product_id=f"p_{variant}",
        sku_id=f"sku_{variant}",
        spin_id=variant,
        name=name,
        brand="Amul",
        pack_size="500 ml",
        price=Price(mrp=price + 5, offer_price=price),
        available=available,
    )


def item(variant: str, name: str, quantity: int = 1, provider: str = "zepto") -> ListItem:
    return ListItem(
        ref=ProductRef(provider=provider, product_id=f"p_{variant}", variant_id=variant, sku_id=f"sku_{variant}"),  # type: ignore[arg-type]
        name=name,
        brand="Amul",
        pack_size="500 ml",
        quantity=quantity,
        unit_price_paise=4000,
    )


class FakeSearcher:
    """A store: returns its catalogue for any query, or fails; records every search."""

    def __init__(self, products: list[Product] | None = None, error: Exception | None = None) -> None:
        self.products = products or []
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int = 0) -> ProductSearchResult:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return ProductSearchResult(products=self.products)


class FakeOrdered:
    def __init__(self, repo: InMemoryBasketRepository) -> None:
        self.repo = repo

    async def recent(self, household_id: str, limit: int = 10) -> list[Basket]:
        ordered = [
            basket for basket in self.repo._items.values()
            if basket.household_id == household_id and basket.status == BasketStatus.ORDERED
        ]
        return sorted(ordered, key=lambda basket: basket.updated_at, reverse=True)[:limit]


@pytest_asyncio.fixture
async def lists(tmp_path: Path) -> AsyncIterator[SavedListStore]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lists.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield SavedListStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


@pytest.fixture
def repos() -> tuple[InMemoryBasketRepository, InMemoryApprovalRepository]:
    return InMemoryBasketRepository(), InMemoryApprovalRepository()


@pytest.fixture
def baskets(repos) -> BasketService:
    return BasketService(*repos)


async def ordered_basket(baskets: BasketService, repo: InMemoryBasketRepository, lines: list[BasketLine]) -> Basket:
    basket = await baskets.add_lines(USER, "zepto", lines)
    basket.status = BasketStatus.ORDERED
    await repo.save(basket)
    return basket


def line_of(i: ListItem, price: int = 4000) -> BasketLine:
    return BasketLine(ref=i.ref, name=i.name, brand=i.brand, pack_size=i.pack_size, unit_price_paise=price, quantity=i.quantity)


# --- list store ---------------------------------------------------------------


async def test_lists_are_user_scoped_and_names_unique_per_user(lists: SavedListStore) -> None:
    weekly = await lists.create(USER, "Weekly list", [item("v1", "Milk")])
    await lists.create("someone-else", "weekly list")
    with pytest.raises(Exception, match="already have a list"):
        await lists.create(USER, "  WEEKLY   list ")
    assert [saved.name for saved in await lists.all(USER)] == ["Weekly list"]
    with pytest.raises(Exception, match="no longer exists"):
        await lists.get("someone-else", weekly.id)
    with pytest.raises(Exception, match="no longer exists"):
        await lists.delete("someone-else", weekly.id)


async def test_find_matches_a_spoken_name(lists: SavedListStore) -> None:
    await lists.create(USER, "Weekly list")
    await lists.create(USER, "Monthly ration")
    assert (await lists.find(USER, "weekly"))[0].name == "Weekly list"
    assert (await lists.find(USER, "monthly ration list"))[0].name == "Monthly ration"
    found, every = await lists.find(USER, "diwali")
    assert found is None and len(every) == 2


# --- adding items: re-checked at the store ------------------------------------


async def test_list_adds_same_variant_at_todays_price_and_reports_the_rest(baskets: BasketService) -> None:
    zepto = FakeSearcher([product("v1", "Amul Milk", 32.0), product("v2", "Amul Curd", available=False)])
    items = [item("v1", "Amul Milk", 2), item("v2", "Amul Curd"), item("v3", "Amul Ghee")]
    report = await add_items(baskets, USER, items, {"zepto": StoreTarget("zepto", zepto, "addr-1")})

    assert [(r.name, r.status) for r in report.items] == [
        ("Amul Milk", "added"),
        ("Amul Curd", "unavailable"),
        ("Amul Ghee", "not_found"),
    ]
    basket = report.baskets["zepto"]
    assert [(l.ref.variant_id, l.quantity, l.unit_price_paise) for l in basket.lines] == [("v1", 2, 3200)]
    assert basket.address_id == "addr-1"


async def test_store_down_reports_store_error_and_stops_searching_it(baskets: BasketService) -> None:
    zepto = FakeSearcher(error=ZeptoProviderError("down"))
    swiggy = FakeSearcher([product("s1", "Atta")])
    items = [item("v1", "Milk"), item("v2", "Curd"), item("v3", "Ghee"), item("v4", "Paneer"), item("v5", "Butter"), item("s1", "Atta", provider="swiggy_instamart")]
    report = await add_items(baskets, USER, items, {
        "zepto": StoreTarget("zepto", zepto, "addr-z"),
        "swiggy_instamart": StoreTarget("swiggy_instamart", swiggy, "addr-s"),
    })
    statuses = {r.name: r.status for r in report.items}
    assert {statuses[n] for n in ["Milk", "Curd", "Ghee", "Paneer", "Butter"]} == {"store_error"}
    assert statuses["Atta"] == "added"
    # Never "not found" for an error, and the failed store is not hammered.
    assert len(zepto.queries) <= 4
    assert "zepto" not in report.baskets


async def test_store_not_ready_without_address_or_connection(baskets: BasketService) -> None:
    report = await add_items(baskets, USER, [item("v1", "Milk"), item("s1", "Atta", provider="swiggy_instamart")], {
        "zepto": StoreTarget("zepto", FakeSearcher(error=ZeptoAuthenticationRequired("x")), "addr"),
        "swiggy_instamart": StoreTarget("swiggy_instamart", FakeSearcher([product("s1", "Atta")]), None),
    })
    assert [r.status for r in report.items] == ["store_not_ready", "store_not_ready"]
    assert report.baskets == {}


async def test_one_revision_per_store_and_the_open_bill_is_superseded(baskets: BasketService, repos) -> None:
    _, approvals = repos
    before = await baskets.add_line(USER, "zepto", line_of(item("v9", "Bread")))
    approval = Approval(id="a1", basket_id=before.id, basket_revision=before.revision, fingerprint="f" * 64, payable_paise=1, created_at=before.updated_at, expires_at=before.updated_at)
    await approvals.save(approval)
    zepto = FakeSearcher([product("v1", "Milk"), product("v2", "Curd")])
    report = await add_items(baskets, USER, [item("v1", "Milk"), item("v2", "Curd")], {"zepto": StoreTarget("zepto", zepto, None)})
    # address None → not ready; with an address the two lines land in one edit
    assert report.baskets == {}
    report = await add_items(baskets, USER, [item("v1", "Milk"), item("v2", "Curd")], {"zepto": StoreTarget("zepto", zepto, "addr")})
    after = report.baskets["zepto"]
    assert after.revision == before.revision + 2  # set_address + one add_lines edit
    assert (await approvals.get("a1")).status == ApprovalStatus.SUPERSEDED


async def test_locked_basket_is_reported_not_changed(baskets: BasketService, repos) -> None:
    repo, _ = repos
    basket = await baskets.add_line(USER, "zepto", line_of(item("v9", "Bread")))
    basket.status = BasketStatus.CHECKOUT_PENDING
    await repo.save(basket)
    report = await add_items(baskets, USER, [item("v1", "Milk")], {"zepto": StoreTarget("zepto", FakeSearcher([product("v1", "Milk")]), "addr")})
    assert [r.status for r in report.items] == ["basket_locked"]
    assert [l.ref.variant_id for l in (await repo.get(basket.id)).lines] == ["v9"]


async def test_recent_items_are_the_last_three_distinct_products(baskets: BasketService, repos) -> None:
    repo, _ = repos
    await ordered_basket(baskets, repo, [line_of(item("v1", "Milk")), line_of(item("v2", "Curd"))])
    await ordered_basket(baskets, repo, [line_of(item("v2", "Curd")), line_of(item("v3", "Ghee")), line_of(item("v4", "Paneer"))])
    recent = recent_items(await FakeOrdered(repo).recent(USER))
    assert [i.name for i in recent] == ["Curd", "Ghee", "Paneer"]


# --- clear ---------------------------------------------------------------------


async def test_clear_is_revision_checked(baskets: BasketService) -> None:
    basket = await baskets.add_lines(USER, "zepto", [line_of(item("v1", "Milk")), line_of(item("v2", "Curd"))])
    with pytest.raises(BasketConflict):
        await baskets.clear(USER, "zepto", basket.revision - 1)
    assert len((await baskets.active(USER, "zepto")).lines) == 2
    cleared = await baskets.clear(USER, "zepto", basket.revision)
    assert cleared.lines == [] and cleared.revision == basket.revision + 1
    # Clearing an empty basket changes nothing.
    assert (await baskets.clear(USER, "zepto", cleared.revision)).revision == cleared.revision


# --- API -----------------------------------------------------------------------


class World:
    def __init__(self, lists: SavedListStore) -> None:
        self.repo = InMemoryBasketRepository()
        self.approvals = InMemoryApprovalRepository()
        self.baskets = BasketService(self.repo, self.approvals)
        self.lists = lists
        self.zepto = FakeSearcher([product("v1", "Amul Milk", 32.0), product("v2", "Amul Curd", available=False)])
        self.swiggy = FakeSearcher()


@pytest_asyncio.fixture
async def world(lists: SavedListStore) -> AsyncIterator[World]:
    world = World(lists)
    overrides = {
        get_basket_service: lambda: world.baskets,
        get_saved_list_store: lambda: world.lists,
        get_ordered_baskets: lambda: FakeOrdered(world.repo),
        get_zepto_service: lambda: world.zepto,
        get_swiggy_service: lambda: world.swiggy,
    }
    app.dependency_overrides.update(overrides)
    yield world
    for key in overrides:
        app.dependency_overrides.pop(key, None)


@pytest_asyncio.fixture
async def client(world: World) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Test-User-Id": USER}) as http:
        yield http


ADDRESSES = {"zepto_address_id": "addr-z", "swiggy_address_id": None}


async def test_reorder_last_puts_items_in_the_basket_and_never_orders(client: AsyncClient, world: World) -> None:
    assert (await client.post("/api/quick-actions/repeat-last", json=ADDRESSES)).status_code == 404
    await ordered_basket(world.baskets, world.repo, [line_of(item("v1", "Amul Milk"), 3000), line_of(item("v2", "Amul Curd"))])

    actions = (await client.get("/api/quick-actions")).json()
    assert actions["last_order"]["store"] == "Zepto" and actions["last_order"]["item_count"] == 2
    assert [r["name"] for r in actions["recent_items"]] == ["Amul Milk", "Amul Curd"]

    response = await client.post("/api/quick-actions/repeat-last", json=ADDRESSES)
    assert response.status_code == 200
    body = response.json()
    assert body["added_count"] == 1
    assert [(i["name"], i["status"]) for i in body["items"]] == [("Amul Milk", "added"), ("Amul Curd", "unavailable")]
    basket = body["baskets"][0]
    assert basket["status"] == "draft" and basket["lines"][0]["unit_price"]["paise"] == 3200
    # No bill was requested and nothing was approved or ordered.
    assert await world.approvals.for_basket(basket["id"]) == []
    assert [b.status for b in world.repo._items.values() if b.status != BasketStatus.ORDERED] == [BasketStatus.DRAFT]


async def test_recent_item_must_come_from_this_households_orders(client: AsyncClient, world: World) -> None:
    await ordered_basket(world.baskets, world.repo, [line_of(item("v1", "Amul Milk"))])
    foreign = {"ref": {"provider": "zepto", "product_id": "p_x", "variant_id": "vx"}, **ADDRESSES}
    assert (await client.post("/api/quick-actions/recent-item", json=foreign)).status_code == 404
    mine = {"ref": {"provider": "zepto", "product_id": "p_v1", "variant_id": "v1", "sku_id": "sku_v1"}, **ADDRESSES}
    response = await client.post("/api/quick-actions/recent-item", json=mine)
    assert response.json()["added_count"] == 1


async def test_list_crud_from_basket_edit_and_add_to_basket(client: AsyncClient, world: World) -> None:
    await world.baskets.add_lines(USER, "zepto", [line_of(item("v1", "Amul Milk"), 3000), line_of(item("v2", "Amul Curd"))])
    created = await client.post("/api/lists", json={"name": "Weekly list", "from_baskets": ["zepto"]})
    assert created.status_code == 201
    saved = created.json()
    assert saved["item_count"] == 2 and saved["items"][0]["last_price"]["paise"] == 3000
    assert (await client.post("/api/lists", json={"name": "weekly LIST"})).status_code == 409

    edit = await client.patch(f"/api/lists/{saved['id']}", json={"items": [{"ref": saved["items"][0]["ref"], "quantity": 3}]})
    assert [(i["name"], i["quantity"]) for i in edit.json()["items"]] == [("Amul Milk", 3)]
    injected = {"items": [{"ref": {"provider": "zepto", "product_id": "p_x", "variant_id": "vx"}, "quantity": 1}]}
    assert (await client.patch(f"/api/lists/{saved['id']}", json=injected)).status_code == 422

    await world.baskets.clear(USER, "zepto")
    report = (await client.post(f"/api/lists/{saved['id']}/add-to-basket", json=ADDRESSES)).json()
    assert report["added_count"] == 1
    assert report["baskets"][0]["lines"][0]["quantity"] == 3

    assert (await client.delete(f"/api/lists/{saved['id']}")).status_code == 204
    assert (await client.get("/api/lists")).json() == {"lists": []}


async def test_empty_list_and_empty_basket_are_refused(client: AsyncClient, world: World) -> None:
    assert (await client.post("/api/lists", json={"name": "Weekly", "from_baskets": ["zepto"]})).status_code == 422
    empty = (await client.post("/api/lists", json={"name": "Weekly"})).json()
    response = await client.post(f"/api/lists/{empty['id']}/add-to-basket", json=ADDRESSES)
    assert response.status_code == 422 and "empty" in response.json()["detail"]


async def test_clear_route_refuses_a_changed_basket(client: AsyncClient, world: World) -> None:
    basket = await world.baskets.add_lines(USER, "zepto", [line_of(item("v1", "Amul Milk"))])
    stale = await client.post("/api/baskets/zepto/clear", json={"expected_revision": basket.revision - 1})
    assert stale.status_code == 409
    assert len((await world.baskets.active(USER, "zepto")).lines) == 1
    cleared = await client.post("/api/baskets/zepto/clear", json={"expected_revision": basket.revision})
    assert cleared.status_code == 200 and cleared.json()["lines"] == []


async def test_clear_route_refuses_a_basket_in_checkout(client: AsyncClient, world: World) -> None:
    basket = await world.baskets.add_lines(USER, "zepto", [line_of(item("v1", "Amul Milk"))])
    basket.status = BasketStatus.CHECKOUT_PENDING
    await world.repo.save(basket)
    response = await client.post("/api/baskets/zepto/clear", json={"expected_revision": basket.revision})
    assert response.status_code == 422
    assert len((await world.repo.get(basket.id)).lines) == 1


# --- voice/typed tool -----------------------------------------------------------


async def test_add_saved_list_tool(lists: SavedListStore, baskets: BasketService) -> None:
    zepto = FakeSearcher([product("v1", "Amul Milk")])
    tool = SavedListToolExecutor(lists, baskets, lambda: {"zepto": StoreTarget("zepto", zepto, "addr")}, USER)
    missing = await tool.execute("add_saved_list", '{"name": "weekly"}')
    assert missing.model_output["status"] == "list_not_found" and missing.model_output["saved_lists"] == []

    await lists.create(USER, "Weekly list", [item("v1", "Amul Milk"), item("v2", "Amul Curd")])
    outcome = await tool.execute("add_saved_list", '{"name": "weekly"}')
    assert outcome.model_output["status"] == "added"
    assert outcome.model_output["not_added"] == [{"name": "Amul Curd", "store": "Zepto", "reason": "not found at the store today"}]
    assert outcome.ui_event and outcome.ui_event["type"] == "basket.updated"
    assert "Nothing was ordered" in outcome.model_output["note"]

    await lists.create(USER, "Empty one")
    empty = await tool.execute("add_saved_list", '{"name": "empty one"}')
    assert empty.model_output["status"] == "list_empty"
