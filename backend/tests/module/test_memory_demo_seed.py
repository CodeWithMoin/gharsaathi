"""Manual owner memory seed (app.memory.demo_seed) on SQLite: dry run, apply, re-run, remove."""

import argparse
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import MemoryItem, MemoryStatus, MemorySubject
from app.memory import demo_seed
from app.memory.demo_seed import SEED_MARKER, SeedError, parse_answers
from app.memory.household import CONSENT_TYPES, PreferenceSpec
from app.memory.household_service import HouseholdPreferenceService
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService
from app.memory.summary import summarize_memory
from app.user_profile import get_profile_record

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "docs" / "demo-memory.example.yaml"
OWNER = "user_owner_123456"

ANSWERS = {
    "household": {"people": 4, "kids": True, "baby": False, "pets": ["dog"]},
    "diet": "Jain",
    "allergies": ["peanuts"],
    "usual_buys": [
        {"category": "milk", "brand": "Amul", "product": "Amul Taaza Toned Milk", "size": "1 L", "quantity": 2},
        {"category": "atta", "size": "5 kg"},
        {"category": "eggs", "quantity": 1},
    ],
    "avoid": {"products": ["chocolate"], "brands": ["Brand X"], "stores": []},
    "value_preference": "cheapest",
    "preferred_store": "zepto",
    "delivery_notes": ["Ring the bell once"],
    "language": "english",
    "address_form": "feminine",
}


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def owner(factory, consent: bool = True, external_id: str = OWNER) -> None:
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(external_id)
        for memory_type in CONSENT_TYPES:
            await MemoryService(session).set_permission(
                actor, memory_type, "personalize_search", MemoryPermissionUpsert(allowed=consent)
            )


def args(**overrides) -> argparse.Namespace:
    base = {"file": None, "user_id": None, "dry_run": False, "apply": False, "remove_seeded": False, "turn_on_memory": False}
    return argparse.Namespace(**{**base, **overrides})


def write(tmp_path: Path, answers: dict, name: str = "home.json") -> str:
    import json

    path = tmp_path / name
    path.write_text(json.dumps(answers), encoding="utf-8")
    return str(path)


async def live_memories(factory) -> list[MemoryItem]:
    async with factory() as session:
        rows = (await session.execute(select(MemoryItem))).scalars().all()
        return [row for row in rows if row.status in {MemoryStatus.CONFIRMED, MemoryStatus.PROPOSED}]


async def run(factory, namespace: argparse.Namespace) -> list[str]:
    async with factory() as session:
        return await demo_seed.run(namespace, session)


def test_example_file_is_valid_and_placeholder_only() -> None:
    answers = demo_seed.load_file(EXAMPLE)
    items = demo_seed.wanted_items(answers)
    assert len(items) >= 10
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "Example" in text and "example-ingredient" in text


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(factory, tmp_path: Path) -> None:
    await owner(factory)
    lines = await run(factory, args(file=write(tmp_path, ANSWERS), dry_run=True))

    assert lines[0] == "user=user_o…56"
    assert "Dry run: nothing written." in lines
    assert lines[2].startswith("create 14, update 0, unchanged 0, remove 0; profile fields to change: 2")
    assert any("Diet: Jain food" in line for line in lines)
    assert await live_memories(factory) == []
    async with factory() as session:
        assert await get_profile_record(session, OWNER) is None


@pytest.mark.asyncio
async def test_apply_is_idempotent_and_told(factory, tmp_path: Path) -> None:
    await owner(factory)
    path = write(tmp_path, ANSWERS)
    await run(factory, args(file=path, apply=True))
    first = await live_memories(factory)
    assert len(first) == 14
    assert {memory.source_conversation_id for memory in first} == {SEED_MARKER}
    assert all(memory.status == MemoryStatus.CONFIRMED for memory in first)

    again = await run(factory, args(file=path, apply=True))
    assert again[2].startswith("create 0, update 0, unchanged 14, remove 0; profile fields to change: 0")
    assert len(await live_memories(factory)) == 14

    async with factory() as session:
        summary = await summarize_memory(session, OWNER)
        profile = await get_profile_record(session, OWNER)
    assert profile is not None and (profile.language, profile.address_form) == ("english", "feminine")
    items = [item for group in summary.groups for item in group.items]
    assert {item.source for item in items if item.managed_by == "memory"} == {"told"}
    groups = {group.id: [item.label for item in group.items] for group in summary.groups}
    assert "Diet: Jain food (no onion, garlic or root vegetables)" in groups["food"]
    assert "Keep out: peanuts" in groups["food"]
    assert "Milk: always Amul, 1 L, usually 2" in groups["usual_buys"]
    assert "Never suggest chocolate" in groups["avoid"]
    assert "Show the cheapest option first" in groups["shopping_style"]
    assert "People at home: 4" in groups["home"]


@pytest.mark.asyncio
async def test_rerun_updates_and_prunes_without_duplicates(factory, tmp_path: Path) -> None:
    await owner(factory)
    await run(factory, args(file=write(tmp_path, ANSWERS), apply=True))
    changed = {
        **ANSWERS,
        "usual_buys": [{**ANSWERS["usual_buys"][0], "quantity": 3}, *ANSWERS["usual_buys"][1:]],
        "avoid": {"products": [], "brands": ["Brand X"]},
    }
    lines = await run(factory, args(file=write(tmp_path, changed, "v2.json"), apply=True))

    assert lines[2].startswith("create 0, update 1, unchanged 12, remove 1")
    live = await live_memories(factory)
    assert len(live) == 13
    milk = [memory for memory in live if memory.value.get("product_category") == "milk"]
    assert len(milk) == 1 and milk[0].value["quantity"] == "3"
    assert milk[0].source_conversation_id == SEED_MARKER
    assert not [memory for memory in live if memory.value.get("product") == "chocolate"]


@pytest.mark.asyncio
async def test_remove_seeded_removes_only_seeded_and_restores_replaced(factory, tmp_path: Path) -> None:
    await owner(factory)
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(OWNER)
        service = HouseholdPreferenceService(session)
        mine, _ = await service.save_stated(
            actor, PreferenceSpec(kind="prefer_store", value="swiggy_instamart", user_words="hamesha Swiggy se mangao")
        )
        other_brand, _ = await service.save_stated(
            actor, PreferenceSpec(kind="prefer_brand", item="milk", value="Mother Dairy", user_words="hamesha Mother Dairy")
        )
        other, _ = await service.save_stated(
            actor, PreferenceSpec(kind="avoid_product", value="soda", user_words="soda kabhi mat dikhana")
        )
    await run(factory, args(file=write(tmp_path, ANSWERS), apply=True))
    live = await live_memories(factory)
    assert {other.id, other_brand.id} <= {memory.id for memory in live}  # a second milk brand is kept
    assert mine.id not in {memory.id for memory in live}  # the one store choice was replaced

    dry = await run(factory, args(remove_seeded=True))
    assert dry[-1] == "Would remove 14 seeded memories. Language and address form are unchanged."
    assert len(await live_memories(factory)) == 16

    done = await run(factory, args(remove_seeded=True, apply=True))
    assert done[-1].startswith("Removed 14 seeded memories.")
    remaining = {memory.id for memory in await live_memories(factory)}
    assert remaining == {other.id, other_brand.id, mine.id}


@pytest.mark.asyncio
async def test_consent_off_refuses_unless_turned_on(factory, tmp_path: Path) -> None:
    await owner(factory, consent=False)
    path = write(tmp_path, ANSWERS)
    with pytest.raises(SeedError, match="Saved preferences are off"):
        await run(factory, args(file=path, apply=True))
    assert await live_memories(factory) == []
    await run(factory, args(file=path, apply=True, turn_on_memory=True))
    assert len(await live_memories(factory)) == 14


@pytest.mark.asyncio
async def test_user_resolution(factory, tmp_path: Path) -> None:
    path = write(tmp_path, ANSWERS)
    with pytest.raises(SeedError, match="users found: none"):
        await run(factory, args(file=path))
    await owner(factory)
    await owner(factory, external_id="user_second_99")
    with pytest.raises(SeedError, match=r"Pass --user-id; users found: user_o…56, user_s…99"):
        await run(factory, args(file=path))
    with pytest.raises(SeedError, match="No signed-in user"):
        await run(factory, args(file=path, user_id="user_typo_0000"))
    lines = await run(factory, args(file=path, user_id="user_second_99"))
    assert lines[0] == "user=user_s…99"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(MemorySubject)) == 2


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"diett": "vegetarian"}, "diett: unknown field"),
        ({"diet": "keto"}, "diet: Input should be 'vegetarian'"),
        ({"usual_buys": [{"category": "milk"}]}, "usual_buys.0: give at least a brand, a product, a size or a quantity"),
        ({"usual_order_times": ["brunch"]}, "usual_order_times: Value error, use early morning"),
        ({"pack_size_rule": "whatever"}, "pack_size_rule: Input should be"),
        ({"avoid": [{"thing": "x"}]}, "each avoid entry is a name or {item, reason}"),
        ({"usual_buys": [{"category": "milk", "quantity": 99}]}, "usual_buys.0.quantity: Input should be less than or equal to 20"),
        ({"avoid": ["diabetes sweets"]}, "never remembers health details"),
        ({"delivery_notes": ["OTP is 1234"]}, "delivery_notes.0: delivery notes can't contain OTPs"),
        ({"preferred_store": "bigbasket"}, "preferred_store: store must be swiggy_instamart or zepto"),
        ({"household": {"people": 40}}, "household.people: Input should be less than or equal to 15"),
        ({"avoid": {"products": ["x1", "x1"]}}, "Two answers for the same thing"),
        ([1, 2], "must be a mapping of topics"),
    ],
)
def test_schema_errors_are_clear(answers, message) -> None:
    with pytest.raises(SeedError) as raised:
        demo_seed.wanted_items(parse_answers(answers))
    assert message in str(raised.value)


def test_bad_yaml_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "home.yaml"
    path.write_text("diet: [unclosed", encoding="utf-8")
    with pytest.raises(SeedError, match="home.yaml is not valid YAML"):
        demo_seed.load_file(path)


def test_owner_style_file_is_accepted_leniently(tmp_path: Path) -> None:
    """The owner's answer shape (free words, {item, reason} avoids, routines) loads as-is."""
    path = tmp_path / "owner.yaml"
    path.write_text(
        """
household:
  people: just me
diet: non-veg
allergies: []
avoid:
  - {item: sugary cold drinks, reason: prefers diet}
  - {item: pork products}
value_preference: balanced
pack_size_rule: best value per unit
substitution_rule: closest alternative, tell me
usual_order_times: [evening, late night]
usual_buys:
  - {category: milk, brand: Amul, product: Taaza toned milk, size: 500 ml}
  - {category: eggs, size: 12 pack}
  - {category: chocolate, brand: Brand A, product: Dark bar, size: 90 g}
  - {category: chocolate, brand: Brand B, product: Dark bar, size: 30 g}
  - {category: dry fruits, product: almonds}
goals: [fitness]
routines:
  - "Morning: milk with oats (example brand 500 ml + example oats)."
""",
        encoding="utf-8",
    )
    items = demo_seed.wanted_items(demo_seed.load_file(path))
    by_kind: dict[str, list[str]] = {}
    for item in items:
        by_kind.setdefault(item.spec.kind, []).append(item.spec.value)
    assert by_kind["household"] == ["1"]
    assert by_kind["diet"] == ["non_vegetarian"]
    assert by_kind["avoid_product"] == ["sugary cold drinks", "pork products"]
    assert sorted(by_kind["rule"]) == ["prefer_best_unit_value", "substitute_closest_and_tell"]
    assert by_kind["prefer_brand"] == ["Amul", "Brand A", "Brand B"]
    assert by_kind["usual_item"] == ["almonds"]
    assert by_kind["prefer_pack_size"] == ["12 pack"]
    assert "Usually orders in the evening, late night" in by_kind["routine"]
    assert by_kind["goal"] == ["fitness"]
    assert all("reason" not in json.dumps(item.value) for item in items)
