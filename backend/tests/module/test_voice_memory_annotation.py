import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.core.config import Settings
from app.database.base import Base
from app.memory import sample_seed
from app.memory.purchase_graph import DATA_CATEGORY, PURPOSE
from app.memory.recall import (
    DatabasePreferenceRecaller,
    HouseholdMemory,
    HouseholdRecall,
    RecallConsent,
    RememberedItem,
    household_memory_for_person,
)
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService
from app.voice.prompts import BACKEND_INSTRUCTIONS
from app.voice.service import build_session_config
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from tests.shopping_fakes import World

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_households.json"


def product(product_id: str, name: str, pack_size: str, brand: str = "Coca-Cola") -> Product:
    return Product(
        product_id=product_id,
        sku_id=f"sku-{product_id}",
        spin_id=f"spin-{product_id}",
        name=name,
        brand=brand,
        pack_size=pack_size,
        price=Price(mrp=50, offer_price=45),
        available=True,
    )


COKE = [
    product("z1", "Diet Coke Soft Drink Can", "330 ml"),
    product("z2", "Diet Coke Soft Drink Can", "6 x 330 ml"),
    product("z3", "Coke Zero Soft Drink Can", "330 ml"),
]


class Searcher:
    def __init__(self, products: list[Product]) -> None:
        self.products = products

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=self.products)


class FakeMemory:
    def __init__(self, memory: HouseholdMemory | Exception) -> None:
        self.memory = memory
        self.calls: list[str] = []

    async def recall(self, household_id: str, item: str) -> HouseholdRecall:
        raise AssertionError("search annotation must not use the recall tool path")

    async def household_memory(self, household_id: str) -> HouseholdMemory:
        self.calls.append(household_id)
        if isinstance(self.memory, Exception):
            raise self.memory
        return self.memory


def remembered_coke(store_id: str = "zepto") -> HouseholdMemory:
    return HouseholdMemory(
        consent=RecallConsent(saved_preferences="off", purchase_history="on"),
        items=[
            RememberedItem(
                source="purchase_history",
                name="Diet Coke Soft Drink Can 330 ml",
                store_id=store_id,  # type: ignore[arg-type]
                last_bought="2026-06-21",
                times=2,
            )
        ],
    )


def toolbox(reader: object, swiggy: list[Product], zepto: list[Product] | None = None) -> VoiceToolbox:
    world = World()
    return VoiceToolbox(
        Searcher(swiggy),
        world.baskets,
        world.approvals,
        "swiggy-home",
        Searcher(zepto) if zepto is not None else None,
        "zepto-home" if zepto is not None else None,
        household_id="verified-household",
        preference_recall=reader,  # type: ignore[arg-type]
    )


async def search(box: VoiceToolbox) -> dict:
    return (await box.execute("search_products", json.dumps({"query": "Diet Coke"}))).model_output


@pytest.mark.asyncio
async def test_search_marks_only_the_remembered_size_with_one_lookup() -> None:
    reader = FakeMemory(remembered_coke())
    output = await search(toolbox(reader, [], COKE))

    marks = {(r["name"], r["pack_size"]): r.get("remembered") for r in output["results"]}
    assert marks[("Diet Coke Soft Drink Can", "330 ml")] == {
        "source": "purchase_history",
        "last_bought": "2026-06-21",
        "times": 2,
    }
    assert marks[("Diet Coke Soft Drink Can", "6 x 330 ml")] is None
    assert marks[("Coke Zero Soft Drink Can", "330 ml")] is None
    assert reader.calls == ["verified-household"]
    assert "not current need or stock" in output["note"]


@pytest.mark.asyncio
async def test_screen_products_carry_the_remembered_source_for_the_best_pick_badge() -> None:
    box = toolbox(FakeMemory(remembered_coke()), [], COKE)
    outcome = await box.execute("search_products", json.dumps({"query": "Diet Coke"}))
    assert outcome.ui_event is not None
    marks = {(p["name"], p["pack_size"]): p.get("remembered") for p in outcome.ui_event["products"]}
    assert marks == {
        ("Diet Coke Soft Drink Can", "330 ml"): "purchase_history",
        ("Diet Coke Soft Drink Can", "6 x 330 ml"): None,
        ("Coke Zero Soft Drink Can", "330 ml"): None,
    }
    # Money reaches the screen as backend ₹ strings, next to the rupee numbers.
    price = outcome.ui_event["products"][0]["price"]
    assert (price["offer_display"], price["mrp_display"], price["saving_display"]) == ("₹45", "₹50", "₹5")


@pytest.mark.asyncio
async def test_cross_store_match_is_labelled() -> None:
    output = await search(toolbox(FakeMemory(remembered_coke("zepto")), COKE[:1]))
    (result,) = output["results"]
    assert result["store_id"] == "swiggy_instamart"
    assert result["remembered"]["bought_on"] == "Zepto"


@pytest.mark.asyncio
async def test_no_consent_or_failure_means_no_annotation_and_search_still_works() -> None:
    empty = HouseholdMemory(consent=RecallConsent(saved_preferences="off", purchase_history="off"), items=[])
    for reader in (FakeMemory(empty), FakeMemory(RuntimeError("db down")), None):
        output = await search(toolbox(reader, COKE))
        assert output["status"] == "ok"
        assert len(output["results"]) == 3
        assert not any("remembered" in result for result in output["results"])
        assert "remembered" not in output["note"]


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed(factory: async_sessionmaker[AsyncSession], external_id: str) -> None:
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(external_id)
        await sample_seed.seed_sample_household(
            session, actor, sample_seed.load_sample_households(FIXTURE), "USR-001"
        )


@pytest.mark.asyncio
async def test_household_memory_uses_recall_consent_and_scoping(factory) -> None:
    await seed(factory, "user-a")
    async with factory() as session:
        memory = await household_memory_for_person(session, "user-a")
        other = await household_memory_for_person(session, "user-b")
    assert memory.consent == RecallConsent(saved_preferences="on", purchase_history="on")
    names = {item.name for item in memory.items}
    assert "Fortune cooking oil" in names
    assert "AXE Dark Temptation Deodorant Body Spray 150 ml" in names
    assert {item.store_id for item in memory.items if item.source == "purchase_history"} == {"swiggy_instamart"}
    assert other.items == [] and other.consent.purchase_history == "off"

    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id("user-a")
        await MemoryService(session).set_permission(
            actor, DATA_CATEGORY, PURPOSE, MemoryPermissionUpsert(allowed=False, retention_days=0)
        )
    reader = DatabasePreferenceRecaller(factory)
    without_history = await reader.household_memory("user-a")
    assert without_history.consent.purchase_history == "off"
    assert {item.source for item in without_history.items} == {"confirmed_preference"}


@pytest.mark.asyncio
async def test_seeded_purchase_annotates_matching_search(factory) -> None:
    await seed(factory, "user-a")
    box = VoiceToolbox(
        Searcher([product("s1", "AXE Dark Temptation Deodorant Body Spray", "150 ml", "AXE"),
                  product("s2", "AXE Dark Temptation Deodorant Body Spray", "215 ml", "AXE")]),
        World().baskets,
        World().approvals,
        "swiggy-home",
        household_id="user-a",
        preference_recall=DatabasePreferenceRecaller(factory),
    )
    output = (await box.execute("search_products", json.dumps({"query": "AXE deo"}))).model_output
    marks = [result.get("remembered") for result in output["results"]]
    assert marks[0] == {"source": "purchase_history", "last_bought": "2026-11-08", "times": 1}
    assert marks[1] is None


def test_session_config_appends_consented_memory_to_backend_only() -> None:
    settings = Settings(_env_file=None, openai_api_key="test")
    config = build_session_config(settings, VOICE_TOOL_DEFINITIONS, None, remembered_coke())
    backend = config["delegation"]["responses"]["instructions"]
    assert backend.startswith(BACKEND_INSTRUCTIONS)
    assert "## Household memory (consented)" in backend
    assert "- Bought: Diet Coke Soft Drink Can 330 ml, Zepto, bought 2 times, last 2026-06-21" in backend
    assert "Household memory (consented)" not in config["instructions"]
    plain = build_session_config(settings, VOICE_TOOL_DEFINITIONS)
    assert plain["delegation"]["responses"]["instructions"] == BACKEND_INSTRUCTIONS


class RecordingHub:
    def start(self, session_id: str, executor: object, *, owner_id: str, events_token: str) -> None:
        self.executor = executor


class Profiles:
    def __init__(self, language: str) -> None:
        self.language = language

    async def read(self, user_id: str):
        from app.user_profile import UserProfile

        return UserProfile(first_name="Moin", address_form="masculine", language=self.language)  # type: ignore[arg-type]


async def create(reader: object, language: str) -> tuple[dict, object]:
    import httpx
    from pydantic import SecretStr

    from app.voice.service import LiveSessionService

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"session": {"id": "s1"}, "transport": {"type": "webrtc", "sdp": "a"}})

    world = World()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = LiveSessionService(
            Settings(_env_file=None, openai_api_key=SecretStr("sk-test")),
            Searcher([]), RecordingHub(), http,  # type: ignore[arg-type]
            baskets=world.baskets, approvals=world.approvals,
            preference_recall=reader,  # type: ignore[arg-type]
            profile_reader=Profiles(language),  # type: ignore[arg-type]
        )
        live = await service.create("v=0", "addr", household_id="verified-household")
    return json.loads(seen[0].content)["session"], live


@pytest.mark.asyncio
async def test_live_session_injects_memory_for_the_verified_user_and_sets_caption_script() -> None:
    reader = FakeMemory(remembered_coke())
    session, live = await create(reader, "hinglish")
    assert reader.calls == ["verified-household"]
    assert "Diet Coke Soft Drink Can 330 ml" in session["delegation"]["responses"]["instructions"]
    assert live.assistant_transcript_script == "roman"  # type: ignore[attr-defined]

    _, hindi = await create(FakeMemory(RuntimeError("db down")), "hindi")
    assert hindi.assistant_transcript_script == "native"  # type: ignore[attr-defined]
