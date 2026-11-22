import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.database.base import Base
from app.database.models import MemorySubject, PurchaseOrderNode
from app.memory import sample_seed
from app.memory.purchase_graph import DATA_CATEGORY, PURPOSE
from app.memory.recall import (
    DatabasePreferenceRecaller,
    HouseholdRecall,
    PreferenceCandidate,
    RecallConsent,
    item_terms,
)
from app.memory.repository import MemoryRepository
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService
from app.voice.memory_tools import MEMORY_TOOL_DEFINITIONS, MemoryToolExecutor
from app.voice.prompts import BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from tests.shopping_fakes import World

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_households.json"


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recall.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def person(factory: async_sessionmaker[AsyncSession], external_id: str) -> MemorySubject:
    async with factory() as session:
        return await MemoryService(session).actor_for_external_id(external_id)


async def seed(factory: async_sessionmaker[AsyncSession], external_id: str, sample: str = "USR-001") -> None:
    actor = await person(factory, external_id)
    async with factory() as session:
        await sample_seed.seed_sample_household(
            session, actor, sample_seed.load_sample_households(FIXTURE), sample
        )


async def recall(executor: MemoryToolExecutor, **arguments: object) -> dict:
    return (await executor.execute("recall_household_preferences", json.dumps(arguments))).model_output


def test_fixture_is_fictional_and_minimal() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for household in data["households"]:
        assert set(household) == {"sample_id", "first_name", "preferred_languages", "memories", "orders"}
        for order in household["orders"]:
            assert set(order) <= {"source_id", "origin", "ordered_at_local", "status", "items"}


def test_tool_schema_is_strict_and_identity_free() -> None:
    (tool,) = MEMORY_TOOL_DEFINITIONS
    assert tool["strict"] is True
    assert tool["parameters"]["required"] == ["item"]
    assert tool["parameters"]["additionalProperties"] is False
    assert tool in VOICE_TOOL_DEFINITIONS


def test_item_terms_drop_order_words_and_add_aliases() -> None:
    assert item_terms("deo order karna hai") >= {"deo", "deodorant"}
    assert "order" not in item_terms("deo order karna hai")
    assert item_terms("order karna hai") == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    ["not json", json.dumps({}), json.dumps({"item": "x"}), json.dumps({"item": "order karna hai"}),
     json.dumps({"item": "deodorant", "household_id": "someone-else"})],
)
async def test_invalid_arguments_are_rejected(raw: str) -> None:
    executor = MemoryToolExecutor(None, "household-a")
    outcome = await executor.execute("recall_household_preferences", raw)
    assert outcome.model_output["status"] == "error"


@pytest.mark.asyncio
async def test_purchase_history_candidate_is_labelled(factory) -> None:
    await seed(factory, "user-a")
    output = await recall(MemoryToolExecutor(DatabasePreferenceRecaller(factory), "user-a"), item="deodorant")
    assert output["status"] == "ok"
    assert output["consent"] == {"saved_preferences": "on", "purchase_history": "on"}
    (candidate,) = output["candidates"]
    assert candidate == {
        "source": "purchase_history",
        "product_name": "AXE Dark Temptation Deodorant Body Spray 150 ml",
        "store": "Swiggy Instamart",
        "last_purchased_on": "2026-11-08",
        "purchase_count": 1,
    }
    assert "wahi" in output["note"] and "not current stock" in output["note"]


@pytest.mark.asyncio
async def test_confirmed_preference_comes_before_history(factory) -> None:
    await seed(factory, "user-a")
    output = await recall(MemoryToolExecutor(DatabasePreferenceRecaller(factory), "user-a"), item="cooking oil")
    sources = [candidate["source"] for candidate in output["candidates"]]
    assert sources == ["confirmed_preference", "purchase_history"]
    assert output["candidates"][0]["brand"] == "Fortune"
    assert output["candidates"][0]["pack_size"] == "1 litre"
    assert output["candidates"][1]["product_name"] == "Fortune Sunlite Oil 1 L"


@pytest.mark.asyncio
async def test_history_off_returns_only_confirmed_preferences(factory) -> None:
    await seed(factory, "user-a")
    actor = await person(factory, "user-a")
    async with factory() as session:
        await MemoryService(session).set_permission(
            actor, DATA_CATEGORY, PURPOSE, MemoryPermissionUpsert(allowed=False, retention_days=0)
        )
    executor = MemoryToolExecutor(DatabasePreferenceRecaller(factory), "user-a")
    oil = await recall(executor, item="oil")
    assert oil["consent"]["purchase_history"] == "off"
    assert {candidate["source"] for candidate in oil["candidates"]} == {"confirmed_preference"}
    assert "history import" in oil["note"]
    deo = await recall(executor, item="deodorant")
    assert deo["status"] == "no_memory" and deo["candidates"] == []


@pytest.mark.asyncio
async def test_preference_permission_off_hides_confirmed_preferences(factory) -> None:
    await seed(factory, "user-a")
    actor = await person(factory, "user-a")
    async with factory() as session:
        for memory_type in ("brand_preference", "pack_size_preference", "quantity_preference", "product_avoidance"):
            await MemoryService(session).set_permission(
                actor, memory_type, "personalize_search", MemoryPermissionUpsert(allowed=False)
            )
    output = await recall(MemoryToolExecutor(DatabasePreferenceRecaller(factory), "user-a"), item="cooking oil")
    assert output["consent"]["saved_preferences"] == "off"
    assert [candidate["source"] for candidate in output["candidates"]] == ["purchase_history"]


@pytest.mark.asyncio
async def test_recall_is_scoped_to_the_verified_household(factory) -> None:
    await seed(factory, "user-a")
    await person(factory, "user-b")
    output = await recall(MemoryToolExecutor(DatabasePreferenceRecaller(factory), "user-b"), item="deodorant")
    assert output["status"] == "no_memory"
    assert output["consent"] == {"saved_preferences": "off", "purchase_history": "off"}


@pytest.mark.asyncio
async def test_unknown_household_gets_empty_result_without_creating_records(factory) -> None:
    output = await recall(MemoryToolExecutor(DatabasePreferenceRecaller(factory), "never-seen"), item="milk")
    assert output["status"] == "no_memory"
    assert "kaunsa lena hai" in output["note"]
    async with factory() as session:
        assert await MemoryRepository(session).find_person("never-seen") is None


@pytest.mark.asyncio
async def test_recall_failure_degrades_to_no_memory() -> None:
    class Broken:
        async def recall(self, household_id: str, item: str) -> HouseholdRecall:
            raise RuntimeError("database down")

    output = await recall(MemoryToolExecutor(Broken(), "user-a"), item="milk")
    assert output["status"] == "no_memory"


class FakeRecaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def recall(self, household_id: str, item: str) -> HouseholdRecall:
        self.calls.append((household_id, item))
        return HouseholdRecall(
            consent=RecallConsent(saved_preferences="off", purchase_history="on"),
            candidates=[
                PreferenceCandidate(
                    source="purchase_history",
                    product_name="AXE Dark Temptation Deodorant Body Spray 150 ml",
                    store="Zepto",
                    last_purchased_on="2026-11-08",
                    purchase_count=2,
                )
            ],
        )


class NoSearch:
    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        raise AssertionError("recall must not search a store")


@pytest.mark.asyncio
async def test_toolbox_routes_recall_with_session_identity() -> None:
    world = World()
    recaller = FakeRecaller()
    box = VoiceToolbox(
        NoSearch(), world.baskets, world.approvals, "addr", household_id="verified-household",
        preference_recall=recaller,
    )
    outcome = await box.execute("recall_household_preferences", json.dumps({"item": "deodorant"}))
    assert recaller.calls == [("verified-household", "deodorant")]
    assert outcome.model_output["candidates"][0]["store"] == "Zepto"
    assert outcome.ui_event is None


class MilkSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        return ProductSearchResult(
            products=[
                Product(product_id="m1", sku_id="m1", spin_id="m1", name="Amul Taaza Milk",
                        pack_size="1 L", price=Price(mrp=70, offer_price=68), available=True)
            ]
        )


@pytest.mark.asyncio
async def test_toolbox_without_recaller_reports_no_memory_and_searches_in_the_same_call() -> None:
    world = World()
    searcher = MilkSearch()
    box = VoiceToolbox(searcher, world.baskets, world.approvals, "addr", household_id="h")
    outcome = await box.execute("recall_household_preferences", json.dumps({"item": "milk"}))
    assert outcome.model_output["status"] == "no_memory"
    assert searcher.queries == ["milk"]
    search = outcome.model_output["search"]
    assert search["status"] == "ok" and search["results"][0]["option_id"] == "opt1"
    assert "Do not search this item again" in outcome.model_output["note"]
    assert outcome.ui_event is not None and outcome.ui_event["type"] == "tool.results"
    added = await box.execute("add_to_basket", json.dumps({"option_id": "opt1", "quantity": 1}))
    assert added.model_output["status"] == "added"


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_removal_is_sample_only(factory) -> None:
    await seed(factory, "user-a")
    await seed(factory, "user-a")
    actor = await person(factory, "user-a")
    async with factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(PurchaseOrderNode).where(PurchaseOrderNode.subject_id == actor.id)
        )
        assert count == 3
        removed = await sample_seed.remove_sample_data(session, actor)
    assert removed == {"memories": 1, "orders": 3}
    output = await recall(MemoryToolExecutor(DatabasePreferenceRecaller(factory), "user-a"), item="oil")
    assert output["candidates"] == []


def test_seed_refuses_in_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sample_seed, "get_settings", lambda: SimpleNamespace(commerce_mode="live"))
    with pytest.raises(sample_seed.SeedRefused):
        sample_seed.require_demo_mode()


def test_prompts_describe_recall_before_search() -> None:
    assert "## Household memory (recall before search)" in BACKEND_INSTRUCTIONS
    assert "recall_household_preferences" in BACKEND_INSTRUCTIONS
    assert "wahi" in BACKEND_INSTRUCTIONS and "Kaunsa brand" in BACKEND_INSTRUCTIONS
    assert "deo order karna hai" in LIVE_INSTRUCTIONS
