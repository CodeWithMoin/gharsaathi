"""Voice/typed memory write tools through VoiceToolbox, on SQLite with fake stores."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.database.base import Base
from app.database.models import MemoryChoiceSignal, MemoryItem, MemoryStatus
from app.memory.choice_signals import propose_from_choices
from app.memory.household import CONSENT_TYPES, PreferenceSpec, sensitive_category, stated_signal
from app.memory.household_service import (
    HouseholdPreferenceService,
    MemoryPolicyError,
    check_policy,
)
from app.memory.recall import DatabasePreferenceRecaller
from app.memory.remembered import memory_summary_block
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import World

HOUSEHOLD = "house-a"


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def consent(factory, household: str = HOUSEHOLD, allowed: bool = True) -> None:
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(household)
        for memory_type in CONSENT_TYPES:
            await MemoryService(session).set_permission(
                actor, memory_type, "personalize_search", MemoryPermissionUpsert(allowed=allowed)
            )


def product(name: str, brand: str, pack: str, price: float, pid: str) -> Product:
    return Product(
        product_id=pid, sku_id=f"sku-{pid}", spin_id=f"spin-{pid}", name=name, brand=brand,
        pack_size=pack, price=Price(mrp=price, offer_price=price), available=True,
    )


class Searcher:
    def __init__(self, products: list[Product]) -> None:
        self.products = products
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        return ProductSearchResult(products=self.products)


MILK = [
    product("Amul Taaza Toned Milk", "Amul", "1 L", 56, "amul"),
    product("Mother Dairy Toned Milk", "Mother Dairy", "1 L", 58, "md"),
    product("Cadbury Dairy Milk Chocolate", "Cadbury", "50 g", 40, "choc"),
]
ATTA = [product("Aashirvaad Shudh Chakki Atta", "Aashirvaad", "5 kg", 272, "atta")]


def make_box(factory, household: str = HOUSEHOLD, swiggy=None, zepto=None, world=None) -> VoiceToolbox:
    world = world or World()
    return VoiceToolbox(
        swiggy or Searcher(MILK), world.baskets, world.approvals, "addr-s",
        zepto or Searcher(ATTA), "addr-z", household,
        preference_recall=DatabasePreferenceRecaller(factory),
    )


async def call(box: VoiceToolbox, name: str, **arguments: object) -> dict:
    return (await box.execute(name, json.dumps(arguments))).model_output


def remember(kind: str, item: str | None, value: str, words: str, mode: str = "stated") -> dict:
    return {"kind": kind, "item": item, "value": value, "user_words": words, "mode": mode}


async def memories(factory, status: MemoryStatus | None = None) -> list[MemoryItem]:
    async with factory() as session:
        statement = select(MemoryItem)
        if status is not None:
            statement = statement.where(MemoryItem.status == status)
        return list((await session.execute(statement)).scalars())


async def test_explicit_needs_read_back_and_yes(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    proposed = await call(
        box, "remember_preference",
        **remember("prefer_brand", "milk", "Amul Taaza", "yaad rakhna doodh hamesha Amul Taaza", "explicit"),
    )
    assert proposed["status"] == "needs_confirmation"
    assert proposed["memory"]["summary"] == "milk: always Amul Taaza"
    assert await memories(factory, MemoryStatus.CONFIRMED) == []

    confirmed = await call(box, "confirm_memory", memory_ref=proposed["memory"]["memory_ref"], decision="confirm")
    assert confirmed["status"] == "confirmed"
    (active,) = await memories(factory, MemoryStatus.CONFIRMED)
    assert active.value["brand"] == "Amul Taaza" and active.value["origin"] == "explicit"


async def test_without_confirmation_nothing_becomes_active(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    proposed = await call(
        box, "remember_preference",
        **remember("prefer_brand", "milk", "Amul Taaza", "yaad rakhna Amul Taaza", "explicit"),
    )
    rejected = await call(box, "confirm_memory", memory_ref=proposed["memory"]["memory_ref"], decision="reject")
    assert rejected["status"] == "rejected"
    assert await memories(factory, MemoryStatus.CONFIRMED) == []
    assert await call(box, "confirm_memory", memory_ref="m99", decision="confirm") == {
        "status": "not_found", "message": "Unknown memory_ref. Use one returned by a memory tool.",
    }


async def test_stated_habit_is_saved_at_once_with_acknowledgement(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    saved = await call(
        box, "remember_preference",
        **remember("prefer_brand", "milk", "Amul Taaza", "hum hamesha Amul Taaza lete hain"),
    )
    assert saved["status"] == "saved"
    assert "reply language" in saved["message"] and "forget" in saved["message"]
    (active,) = await memories(factory, MemoryStatus.CONFIRMED)
    assert active.value["origin"] == "stated"
    again = await call(
        box, "remember_preference",
        **remember("prefer_brand", "milk", "Amul Taaza", "hamesha Amul Taaza"),
    )
    assert again["status"] == "already_saved"


@pytest.mark.parametrize(
    "words",
    ["is baar Amul de do", "abhi ke liye Amul", "this time get Amul", "Amul wala de do"],
)
async def test_one_off_or_habitless_choice_is_not_saved(factory, words: str) -> None:
    await consent(factory)
    output = await call(make_box(factory), "remember_preference", **remember("prefer_brand", "milk", "Amul", words))
    assert output["status"] in {"not_saved_one_off", "not_saved_no_habit"}
    assert await memories(factory) == []


async def test_update_supersedes_and_forget_undoes_to_the_previous(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    await call(box, "remember_preference", **remember("prefer_brand", "milk", "Amul Taaza", "hamesha Amul Taaza"))
    updated = await call(
        box, "update_preference", memory_ref=None,
        **remember("prefer_brand", "milk", "Mother Dairy", "nahi, Mother Dairy wala"),
    )
    assert updated["status"] == "saved"
    assert updated["replaced"]["summary"] == "milk: always Amul Taaza"
    (active,) = await memories(factory, MemoryStatus.CONFIRMED)
    assert active.value["brand"] == "Mother Dairy"
    (old,) = await memories(factory, MemoryStatus.SUPERSEDED)
    assert active.supersedes_memory_id == old.id

    # "Galat hai, bhool jao" right after: undo restores Amul Taaza.
    forgotten = await call(box, "forget_preference", scope="one", memory_ref=updated["memory"]["memory_ref"], confirm_token=None)
    assert forgotten["status"] == "forgotten"
    assert forgotten["restored"]["summary"] == "milk: always Amul Taaza"
    (restored,) = await memories(factory, MemoryStatus.CONFIRMED)
    assert restored.id == old.id


async def test_explicit_update_waits_for_yes(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    await call(box, "remember_preference", **remember("prefer_brand", "milk", "Amul Taaza", "hamesha Amul Taaza"))
    listing = await call(box, "list_preferences")
    ref = listing["memories"][0]["memory_ref"]
    pending = await call(
        box, "update_preference", memory_ref=ref,
        **remember("prefer_brand", "milk", "Mother Dairy", "ab Amul nahi, Mother Dairy yaad rakhna", "explicit"),
    )
    assert pending["status"] == "needs_confirmation"
    assert [m.value["brand"] for m in await memories(factory, MemoryStatus.CONFIRMED)] == ["Amul Taaza"]
    await call(box, "confirm_memory", memory_ref=pending["memory"]["memory_ref"], decision="confirm")
    assert [m.value["brand"] for m in await memories(factory, MemoryStatus.CONFIRMED)] == ["Mother Dairy"]
    assert len(await memories(factory, MemoryStatus.SUPERSEDED)) == 1


async def test_forget_one_and_forget_all_needs_confirmation(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    await call(box, "remember_preference", **remember("avoid_store", None, "zepto", "Zepto se kabhi order mat karna", "explicit"))
    proposal = await call(box, "list_preferences")
    await call(box, "confirm_memory", memory_ref=proposal["memories"][0]["memory_ref"], decision="confirm")
    await call(box, "remember_preference", **remember("avoid_product", None, "chocolate", "chocolate mat dikhana"))

    assert (await call(box, "forget_preference", scope="one", memory_ref=None, confirm_token=None))["status"] == "needs_ref"
    first = await call(box, "forget_preference", scope="all", memory_ref=None, confirm_token=None)
    assert first["status"] == "needs_confirmation" and first["count"] == 2
    assert len(await memories(factory, MemoryStatus.CONFIRMED)) == 2
    invented = await call(box, "forget_preference", scope="all", memory_ref=None, confirm_token="123.fake")
    assert invented["status"] == "error"
    done = await call(box, "forget_preference", scope="all", memory_ref=None, confirm_token=first["confirm_token"])
    assert done == {"status": "forgotten_all", "deleted": 2, "message": done["message"]}
    assert await memories(factory, MemoryStatus.CONFIRMED) == []

    box2 = make_box(factory)
    await call(box2, "remember_preference", **remember("avoid_product", None, "chocolate", "chocolate mat dikhana"))
    ref = (await call(box2, "list_preferences"))["memories"][0]["memory_ref"]
    # An explicit/older memory is deleted outright.
    async with factory() as session:
        item = (await session.execute(select(MemoryItem).where(MemoryItem.status == MemoryStatus.CONFIRMED))).scalar_one()
        item.value = {**item.value, "origin": "explicit"}
        await session.commit()
    assert (await call(box2, "forget_preference", scope="one", memory_ref=ref, confirm_token=None))["status"] == "forgotten"
    assert await memories(factory, MemoryStatus.CONFIRMED) == []


@pytest.mark.parametrize(
    ("kind", "item", "value", "words"),
    [
        ("avoid_product", None, "sugar", "diabetes hai, sugar kabhi mat dikhana"),
        ("prefer_brand", "atta", "Aashirvaad", "navratri mein hamesha Aashirvaad"),
        ("rule", None, "prefer_cheapest", "salary kam hai, hamesha sasta dikhana"),
        ("prefer_brand", "milk", "Amul", "meri wife hamesha Amul leti hai"),
        ("prefer_pack_size", "diapers", "Large", "beta 2 saal ka hai, hamesha Large"),
    ],
)
async def test_sensitive_categories_are_refused_in_code(factory, kind, item, value, words) -> None:
    await consent(factory)
    output = await call(make_box(factory), "remember_preference", **remember(kind, item, value, words))
    assert output["status"] == "not_allowed"
    assert await memories(factory) == []
    # The service refuses too, whatever the caller.
    with pytest.raises(MemoryPolicyError):
        check_policy(PreferenceSpec(kind=kind, item=item, value=value, user_words=words))


def test_policy_and_habit_signals_leave_shopping_words_alone() -> None:
    for text in ("Samsung tablet", "kidney beans rajma", "poori atta", "Mother Dairy", "Package of 6", "aage se Amul"):
        assert sensitive_category(text) is None, text
    assert stated_signal("hum hamesha Amul hi lete hain") == "habitual"
    assert stated_signal("Zepto se mat mangao, late aata hai") == "habitual"
    assert stated_signal("500 ml wala hi lena") == "habitual"
    assert stated_signal("is baar Amul de do") == "one_off"
    assert stated_signal("Amul de do") == "none"


async def test_consent_off_refuses_and_saves_nothing(factory) -> None:
    await consent(factory, allowed=False)
    box = make_box(factory)
    output = await call(box, "remember_preference", **remember("prefer_brand", "milk", "Amul", "hamesha Amul"))
    assert output["status"] == "memory_off" and "Settings" in output["message"]
    listing = await call(box, "list_preferences")
    assert listing["memory"] == "off"
    assert await memories(factory) == []


async def test_memory_is_scoped_to_the_verified_household(factory) -> None:
    await consent(factory)
    await consent(factory, "house-b")
    a, b = make_box(factory), make_box(factory, "house-b")
    saved = await call(a, "remember_preference", **remember("prefer_brand", "milk", "Amul", "hamesha Amul"))
    assert (await call(b, "list_preferences"))["memories"] == []
    # house-b cannot reach house-a's memory, even with a ref it never received.
    output = await call(b, "forget_preference", scope="one", memory_ref=saved["memory"]["memory_ref"], confirm_token=None)
    assert output["status"] == "needs_ref"
    assert len(await memories(factory, MemoryStatus.CONFIRMED)) == 1
    injected = await call(
        a, "remember_preference", household_id="house-b", **remember("prefer_brand", "milk", "Ghee", "hamesha")
    )
    assert injected["status"] == "error"


async def test_avoid_store_skips_that_store_and_blocks_old_options(factory) -> None:
    await consent(factory)
    zepto = Searcher(ATTA)
    box = make_box(factory, swiggy=Searcher(ATTA), zepto=zepto)
    before = await call(box, "search_products", query="atta")
    zepto_option = next(r["option_id"] for r in before["results"] if r["store_id"] == "zepto")

    saved = await call(box, "remember_preference", **remember("avoid_store", None, "zepto", "Zepto se kabhi order mat karna"))
    assert saved["status"] == "saved"
    after = await call(box, "search_products", query="atta chahiye")
    assert {r["store_id"] for r in after["results"]} == {"swiggy_instamart"}
    assert after["skipped_stores"] == [{"store": "Zepto", "household_rule": "never order from Zepto"}]
    assert zepto.queries == ["atta"]

    blocked = await call(box, "add_to_basket", option_id=zepto_option, quantity=1)
    assert blocked["status"] == "blocked_by_household_rule"


async def test_prefer_annotates_and_avoid_product_hides_unless_asked(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    await call(box, "remember_preference", **remember("prefer_brand", "milk", "Amul", "hum hamesha Amul hi lete hain"))
    await call(box, "remember_preference", **remember("avoid_product", None, "chocolate", "mujhe chocolate mat dikhana"))

    milk = await call(box, "search_products", query="milk")
    names = {r["name"]: r for r in milk["results"] if r["store_id"] == "swiggy_instamart"}
    assert names["Amul Taaza Toned Milk"]["preferred"] is True
    assert "preferred" not in names["Mother Dairy Toned Milk"]
    assert "Cadbury Dairy Milk Chocolate" not in names
    assert milk["hidden_by_household_rules"] == 1
    assert "remembered" not in names["Mother Dairy Toned Milk"]

    asked = await call(box, "search_products", query="chocolate")
    chocolate = next(r for r in asked["results"] if r["name"].startswith("Cadbury"))
    assert chocolate["avoided"] is True


async def test_active_rules_appear_in_the_session_memory_summary(factory) -> None:
    await consent(factory)
    box = make_box(factory)
    await call(box, "remember_preference", **remember("avoid_store", None, "zepto", "Zepto se mat mangao"))
    await call(box, "remember_preference", **remember("rule", None, "prefer_cheapest", "hamesha sasta wala pehle"))
    memory = await DatabasePreferenceRecaller(factory).household_memory(HOUSEHOLD)
    block = memory_summary_block(memory)
    assert "- Never order from: Zepto" in block
    assert "- Rule: Show the cheapest option first" in block


async def test_repeated_choices_become_one_pending_suggestion_offered_once(factory) -> None:
    await consent(factory)
    for _ in range(3):
        box = make_box(factory, zepto=Searcher([]))
        search = await call(box, "search_products", query="Amul Taaza milk 1 litre")
        amul = next(r["option_id"] for r in search["results"] if r["name"].startswith("Amul"))
        added = await call(box, "add_to_basket", option_id=amul, quantity=1)
        assert added["status"] == "added" and "memory_suggestion" not in added
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryChoiceSignal)) == 3
        actor = await MemoryService(session).actor_for_external_id(HOUSEHOLD)
        created = await propose_from_choices(session, actor)
        assert [(m.status, m.value["brand"], m.value["pack_size"]) for m in created] == [
            (MemoryStatus.PROPOSED, "Amul", "1 L")
        ]
        assert await propose_from_choices(session, actor) == []
    assert await memories(factory, MemoryStatus.CONFIRMED) == []

    box = make_box(factory, zepto=Searcher([]))
    search = await call(box, "search_products", query="milk")
    amul = next(r["option_id"] for r in search["results"] if r["name"].startswith("Amul"))
    first = await call(box, "add_to_basket", option_id=amul, quantity=1)
    suggestion = first["memory_suggestion"]
    assert suggestion["suggestion"] is True and suggestion["summary"] == "milk: always Amul, 1 L"
    second = await call(box, "add_to_basket", option_id=amul, quantity=1)
    assert "memory_suggestion" not in second

    confirmed = await call(box, "confirm_memory", memory_ref=suggestion["memory_ref"], decision="confirm")
    assert confirmed["status"] == "confirmed"
    (active,) = await memories(factory, MemoryStatus.CONFIRMED)
    assert active.value["origin"] == "inferred"


async def test_rejected_suggestion_is_not_proposed_again(factory) -> None:
    await consent(factory)
    for _ in range(3):
        box = make_box(factory, zepto=Searcher([]))
        search = await call(box, "search_products", query="milk")
        amul = next(r["option_id"] for r in search["results"] if r["name"].startswith("Amul"))
        await call(box, "add_to_basket", option_id=amul, quantity=1)
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(HOUSEHOLD)
        (proposal,) = await propose_from_choices(session, actor)
        await HouseholdPreferenceService(session).reject(actor, proposal.id)
        assert await propose_from_choices(session, actor) == []


async def test_no_signals_without_consent(factory) -> None:
    await consent(factory, allowed=False)
    box = make_box(factory, zepto=Searcher([]))
    search = await call(box, "search_products", query="milk")
    await call(box, "add_to_basket", option_id=search["results"][0]["option_id"], quantity=1)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryChoiceSignal)) == 0
