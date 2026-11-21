"""Voice basket tools report the selected saved delivery address truthfully."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commerce.provider_state import (
    ProviderAddressRepository,
    SavedAddressDirectory,
    SavedAddressSummary,
    summarise_address,
)
from app.commerce.swiggy.models import (
    Address,
    AddressList,
    AddressResolution,
    Pagination,
    Price,
    Product,
)
from app.database.base import Base
from app.memory.repository import MemoryRepository
from app.voice.toolbox import VoiceToolbox
from tests.test_voice_basket_tools import (
    TEST_HOUSEHOLD_ID,
    FakeSearcher,
    TwoStoreWorld,
    call,
    option_for,
    product,
)


def zepto_product() -> Product:
    return Product(
        product_id="zid_axe",
        sku_id="spid_axe",
        spin_id="pvid_axe",
        name="Axe Dark Temptation Deo",
        pack_size="150 ml",
        price=Price(mrp=299, offer_price=172),
        available=True,
    )


class FakeDirectory:
    def __init__(self, known: dict[tuple[str, str], SavedAddressSummary]) -> None:
        self.known = known
        self.calls: list[tuple[str, str, str]] = []

    async def describe(
        self, external_user_id: str, provider: str, address_id: str
    ) -> SavedAddressSummary | None:
        self.calls.append((external_user_id, provider, address_id))
        return self.known.get((provider, address_id))


class FailingDirectory:
    async def describe(self, *_: str) -> SavedAddressSummary | None:
        raise RuntimeError("database unavailable")


HOME = SavedAddressSummary(label="Home", short_address="Flat 4B, Sunshine Apartments")


def box_with(world: TwoStoreWorld, directory: object, zepto_address_id: str | None = "zepto_home") -> VoiceToolbox:
    return VoiceToolbox(
        FakeSearcher([product("Oil", 154, 180)]),
        world.baskets,
        world.approvals,
        "swiggy_home",
        FakeSearcher([zepto_product()]),
        zepto_address_id,
        household_id=TEST_HOUSEHOLD_ID,
        addresses=directory,  # type: ignore[arg-type]
    )


async def add_zepto(box: VoiceToolbox) -> dict:
    search = await call(box, "search_products", query="axe deo")
    return await call(box, "add_to_basket", option_id=option_for(search, "Zepto"), quantity=1)


async def test_add_view_and_bill_name_the_selected_zepto_address() -> None:
    world = TwoStoreWorld()
    directory = FakeDirectory({("zepto", "zepto_home"): HOME})
    box = box_with(world, directory)

    added = await add_zepto(box)
    viewed = await call(box, "view_basket", store="zepto")
    bill = await call(box, "request_approval", store="zepto")

    expected = {"label": "Home", "address": "Flat 4B, Sunshine Apartments"}
    assert added["model_output"]["delivery_address"] == expected
    assert viewed["model_output"]["delivery_address"] == expected
    assert bill["model_output"]["status"] == "awaiting_user_approval"
    assert bill["model_output"]["delivery_address"] == expected
    # Looked up once per store/address for the session, for the verified user.
    assert directory.calls == [(TEST_HOUSEHOLD_ID, "zepto", "zepto_home")]


async def test_all_store_view_gives_each_store_its_own_address() -> None:
    world = TwoStoreWorld()
    directory = FakeDirectory({("zepto", "zepto_home"): HOME})
    box = box_with(world, directory)
    await add_zepto(box)

    viewed = await call(box, "view_basket", store=None)

    by_store = {basket["store_id"]: basket for basket in viewed["model_output"]["baskets"]}
    assert by_store["zepto"]["delivery_address"]["label"] == "Home"
    assert "delivery_address" not in by_store["swiggy_instamart"]


async def test_unknown_unselected_or_failed_lookup_omits_the_address() -> None:
    for directory in (FakeDirectory({}), FailingDirectory(), None):
        world = TwoStoreWorld()
        added = await add_zepto(box_with(world, directory))

        assert added["model_output"]["status"] == "added"
        assert "delivery_address" not in added["model_output"]

    world = TwoStoreWorld()
    known = FakeDirectory({("zepto", "zepto_home"): HOME})
    await add_zepto(box_with(world, known))
    unselected = box_with(world, known, zepto_address_id=None)

    viewed = await call(unselected, "view_basket", store="zepto")

    assert viewed["model_output"]["items"]
    assert "delivery_address" not in viewed["model_output"]


def test_summary_uses_the_provider_label_and_shortens_long_lines() -> None:
    long_line = (
        "Flat 4B, Sunshine Apartments, 5th Cross Road, Koramangala 4th Block, "
        "Bengaluru, Karnataka 560034"
    )

    summary = summarise_address(Address(id="a", address_line=long_line, tag="Home"))

    assert summary.label == "Home"
    assert summary.short_address == "Flat 4B, Sunshine Apartments, 5th Cross Road…"
    assert summarise_address(Address(id="b", address_line="  Office\n Tower ")) == SavedAddressSummary(
        label=None, short_address="Office Tower"
    )


async def test_directory_reads_only_the_users_server_side_snapshot(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'addresses.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        subject = await MemoryRepository(session).get_or_create_person("user-a")
        await session.commit()
        await ProviderAddressRepository(session).save(
            subject.id,
            AddressList(
                addresses=[Address(id="zepto_home", address_line="Flat 4B, Sunshine", tag="Home")],
                pagination=Pagination(total=1),
                resolution=AddressResolution(),
            ),
            "zepto",
        )
    directory = SavedAddressDirectory(sessions)

    assert await directory.describe("user-a", "zepto", "zepto_home") == SavedAddressSummary(
        label="Home", short_address="Flat 4B, Sunshine"
    )
    assert await directory.describe("user-a", "zepto", "invented") is None
    assert await directory.describe("user-a", "swiggy_instamart", "zepto_home") is None
    assert await directory.describe("user-b", "zepto", "zepto_home") is None
    await engine.dispose()
