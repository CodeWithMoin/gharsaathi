"""Synthetic demo history (app.memory.demo_synth): consistent with the answers, marked, removable,
included in patterns/recall, excluded from provider imports and "my last order". SQLite + fakes."""

import argparse
import re
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commerce.swiggy.models import OrderItem, OrderList, OrderSummary
from app.database.base import Base
from app.database.models import PurchaseOrderNode, PurchaseProductNode
from app.memory import demo_synth
from app.memory.demo_seed import parse_answers
from app.memory.history_reading import HistoryRead
from app.memory.purchase_graph import DATA_CATEGORY, PURPOSE, PurchaseGraphService, is_synthetic_order_id
from app.memory.recall import recall_for_person
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService
from app.memory.summary import summarize_memory

REPO = Path(__file__).resolve().parents[3]
USER = "user_synth_owner_7"
END = date(2026, 11, 21)
# The owner's answer shape, with placeholder-ish values.
ANSWERS = {
    "household": {"people": "just me"},
    "diet": "non-veg",
    "avoid": [{"item": "sugary cold drinks", "reason": "prefers diet"}, {"item": "pork products"}],
    "usual_order_times": ["evening", "late night"],
    "preferred_store": "zepto",
    "usual_buys": [
        {"category": "milk", "brand": "Amul", "product": "Taaza toned milk", "size": "500 ml"},
        {"category": "eggs", "size": "12 pack"},
        {"category": "instant breakfast", "brand": "Yogabar", "product": "chocolate oats"},
        {"category": "instant breakfast", "brand": "Nestle", "product": "Maggi Masala noodles", "size": "4-pack"},
        {"category": "cold drink", "brand": "Coca-Cola", "product": "Diet Coke", "size": "can"},
        {"category": "chocolate", "brand": "Hershey's", "product": "Dark Chocolate Bar", "size": "90 g"},
        {"category": "chocolate", "brand": "Cadbury", "product": "Bournville Rich Cocoa 50% Dark", "size": "30 g"},
        {"category": "dairy drink", "brand": "Amul", "product": "Sweet Lassi", "size": "200 ml"},
        {"category": "dairy drink", "product": "spiced buttermilk (chaas)"},
        {"category": "snacks", "brand": "Max Protein", "product": "protein bar"},
        {"category": "dairy", "brand": "Milky Mist", "product": "Greek yogurt"},
        {"category": "dry fruits", "product": "almonds"},
        {"category": "dry fruits", "product": "cashews"},
        {"category": "snacks", "brand": "Malkist", "product": "Cheese crackers", "size": "1 pack (144-150 g)"},
        {"category": "vegetables", "product": "onions"},
    ],
}


@pytest.fixture(scope="module")
def orders() -> list[demo_synth.SynthOrder]:
    return demo_synth.generate(parse_answers(ANSWERS), months=12, seed=42, end=END)


def names(orders) -> list[str]:
    return [item.name for order in orders for item in order.items]


def dates_of(orders, pattern: str) -> list[date]:
    return sorted({order.at.date() for order in orders for item in order.items if re.search(pattern, item.name)})


def per_week(orders, pattern: str) -> float:
    lines = sum(1 for order in orders for item in order.items if re.search(pattern, item.name))
    return lines / (360 / 7)


# Consistency ----------------------------------------------------------------


def test_nothing_contradicts_diet_or_avoid_list(orders) -> None:
    all_names = names(orders)
    assert all_names
    for name in all_names:
        assert not re.search(r"(?i)\b(pork|ham|bacon|pepperoni|salami)\b", name), name
        assert not re.search(r"(?i)\b(sprite|fanta|pepsi|thums up|original taste)\b", name), name
        if re.search(r"(?i)\bcola\b|\bcoke\b", name):
            assert re.search(r"(?i)diet|zero", name), name
    rules = demo_synth.household_rules(parse_answers(ANSWERS))
    assert all(demo_synth.allowed(rules, name) for name in all_names)


def test_vegetarian_answers_never_get_meat_or_eggs() -> None:
    veg = demo_synth.generate(parse_answers({**ANSWERS, "diet": "vegetarian"}), months=6, seed=7, end=END)
    for name in names(veg):
        assert not re.search(r"(?i)chicken|mutton|fish|egg", name) or re.search(r"(?i)eggless", name), name


def test_cadences_follow_the_routines(orders) -> None:
    assert 2.0 <= per_week(orders, "Taaza") <= 3.5
    assert 2.0 <= per_week(orders, "Diet Coke") <= 4.0
    egg_days = dates_of(orders, "Eggs")
    gaps = [(later - earlier).days for earlier, later in zip(egg_days, egg_days[1:])]
    assert gaps and all(7 <= gap <= 13 for gap in gaps), gaps
    oats = dates_of(orders, "Yogabar")
    assert 10 <= len(oats) <= 15  # monthly
    maggi = dates_of(orders, "Maggi")
    assert 20 <= len(maggi) <= 30  # every ~2 weeks
    assert 0.7 <= per_week(orders, "Protein") <= 1.5  # weekly-ish (each line carries 2-4 bars)
    assert 40 <= len(dates_of(orders, "Greek Yogurt")) <= 62
    assert 8 <= len(dates_of(orders, "Almonds")) <= 14 and 8 <= len(dates_of(orders, "Cashew")) <= 14
    milk_parts = Counter(order.daypart for order in orders for item in order.items if "Taaza" in item.name)
    assert milk_parts["morning"] / sum(milk_parts.values()) > 0.7


def test_chocolates_alternate_and_crackers_ride_night_orders(orders) -> None:
    chocolates = [item.name for order in sorted(orders, key=lambda o: o.at) for item in order.items if "Dark" in item.name]
    brands = ["Hershey" if "Hershey" in name else "Cadbury" for name in chocolates]
    assert brands.count("Hershey") > 10 and brands.count("Cadbury") > 10
    assert sum(1 for left, right in zip(brands, brands[1:]) if left != right) > len(brands) * 0.6
    malkist = [order.daypart for order in orders for item in order.items if "Malkist" in item.name]
    assert malkist and set(malkist) == {"late_night"}


def test_cook_days_and_summer_drinks(orders) -> None:
    onion_weeks = {day.isocalendar()[:2] for day in dates_of(orders, "Onion")}
    assert len(onion_weeks) >= 30
    cook_lines = [order for order in orders if any(item.category == "vegetables" for item in order.items)]
    assert all(order.daypart in {"evening", "afternoon"} for order in cook_lines)
    by_month = Counter(order.at.month for order in orders for item in order.items if item.category == "dairy drinks")
    summer = sum(by_month[month] for month in (4, 5, 6)) / 3
    winter = sum(by_month[month] for month in (12, 1, 2)) / 3
    assert summer >= 2 * winter


def test_variety_switches_and_order_times(orders) -> None:
    assert any("Nandini" in name or "Mother Dairy" in name for name in names(orders))  # out-of-stock switch
    categories = {item.category for order in orders for item in order.items}
    assert {"stationery", "electronics", "cleaning", "personal care"} & categories
    parts = Counter(order.daypart for order in orders)
    assert (parts["evening"] + parts["late_night"]) / len(orders) > 0.6
    assert Counter(order.provider for order in orders)["zepto"] > Counter(order.provider for order in orders)["swiggy"]
    assert all(order.order_id.startswith("synth-") for order in orders)
    assert len({order.order_id for order in orders}) == len(orders)


def test_generation_is_deterministic() -> None:
    answers = parse_answers(ANSWERS)
    first = demo_synth.generate(answers, months=3, seed=1, end=END)
    assert first == demo_synth.generate(answers, months=3, seed=1, end=END)
    assert first != demo_synth.generate(answers, months=3, seed=2, end=END)
    with pytest.raises(demo_synth.SynthRefused, match="--months must be 1 to 24"):
        demo_synth.generate(answers, months=30)


def test_dry_run_report_counts(orders) -> None:
    lines = demo_synth.report(orders)
    assert lines[0].startswith(f"orders {len(orders)}, order lines")
    assert lines[1] == "lines per category per month:"
    assert any(line.strip().startswith("milk") for line in lines)


# Database ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'synth.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def user(factory, history: bool = True):
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(USER)
        if history:
            await MemoryService(session).set_permission(
                actor, DATA_CATEGORY, PURPOSE, MemoryPermissionUpsert(allowed=True, retention_days=400)
            )
        return actor


def real_order(order_id: str, when: datetime, *names: str) -> OrderSummary:
    return OrderSummary(
        order_id=order_id, status="delivered", current_status="delivered", history_status="delivered",
        created_at=when, updated_at=when, item_count=len(names), total_paise=12300, order_type="ZEPTO",
        is_active=False, items=[OrderItem(name=name, quantity=1, item_id=f"pv-{index}") for index, name in enumerate(names)],
        lookup_id=f"lookup-{order_id}",
    )


def args(tmp_path: Path, **overrides) -> argparse.Namespace:
    import json

    path = tmp_path / "answers.json"
    path.write_text(json.dumps(ANSWERS), encoding="utf-8")
    namespace = demo_synth.parse_args(["--file", str(path), "--months", "4", "--end", END.isoformat(), "--user-id", USER])
    for key, value in overrides.items():
        setattr(namespace, key, value)
    return namespace


async def synth_rows(factory) -> list[PurchaseOrderNode]:
    async with factory() as session:
        rows = (await session.execute(select(PurchaseOrderNode))).scalars().all()
        return [row for row in rows if is_synthetic_order_id(row.provider_order_id)]


async def test_dry_run_writes_nothing_and_apply_is_idempotent(factory, tmp_path: Path) -> None:
    await user(factory)
    async with factory() as session:
        dry = await demo_synth.run(args(tmp_path), session)
    assert dry[1] == "Dry run: nothing written. Synthetic orders there now: 0."
    assert await synth_rows(factory) == []

    async with factory() as session:
        first = await demo_synth.run(args(tmp_path, apply=True), session)
    rows = await synth_rows(factory)
    assert rows and first[1].startswith(f"Applied: {len(rows)} new, 0 already there")
    assert all(row.provider_lookup_id is None and row.total_paise == 0 for row in rows)

    async with factory() as session:
        again = await demo_synth.run(args(tmp_path, apply=True), session)
    assert again[1].startswith(f"Applied: 0 new, {len(rows)} already there (updated), 0 stale removed.")
    assert len(await synth_rows(factory)) == len(rows)

    async with factory() as session:  # a shorter window prunes only its own stale orders
        shorter = await demo_synth.run(args(tmp_path, apply=True, months=2), session)
    assert "stale removed" in shorter[1] and not shorter[1].endswith(" 0 stale removed.")


async def test_remove_deletes_only_synthetic(factory, tmp_path: Path) -> None:
    actor = await user(factory)
    async with factory() as session:
        await PurchaseGraphService(session).import_orders(
            actor, OrderList(orders=[real_order("Z-REAL-1", datetime(2026, 11, 20, 20, tzinfo=UTC), "Real Only Thing 1 kg", "Amul Taaza Toned Milk 500 ml")]),
            provider="zepto",
        )
        await demo_synth.run(args(tmp_path, apply=True), session)
    assert await synth_rows(factory)
    async with factory() as session:
        counted = await demo_synth.run(args(tmp_path, remove=True, apply=False, apply_remove=False), session)
    assert counted[1].startswith("Would remove ")
    assert await synth_rows(factory)
    async with factory() as session:
        removed = await demo_synth.run(args(tmp_path, remove=True, apply=False, apply_remove=True), session)
        remaining = (await session.execute(select(PurchaseOrderNode.provider_order_id))).scalars().all()
        products = set((await session.execute(select(PurchaseProductNode.name))).scalars().all())
    assert removed[1].startswith("Removed ")
    assert remaining == ["Z-REAL-1"]
    assert products == {"Real Only Thing 1 kg", "Amul Taaza Toned Milk 500 ml"}


async def test_history_off_and_live_mode_refuse(factory, tmp_path: Path, monkeypatch) -> None:
    await user(factory, history=False)
    async with factory() as session:
        with pytest.raises(demo_synth.SynthRefused, match="Purchase history is off"):
            await demo_synth.run(args(tmp_path, apply=True), session)
        assert await synth_rows(factory) == []
        await demo_synth.run(args(tmp_path, apply=True, turn_on_history=True), session)
    assert await synth_rows(factory)

    monkeypatch.setattr(demo_synth, "get_settings", lambda: SimpleNamespace(commerce_mode="live"))
    async with factory() as session:
        with pytest.raises(demo_synth.SynthRefused, match="--i-understand-synthetic"):
            await demo_synth.run(args(tmp_path), session)
        with pytest.raises(demo_synth.SynthRefused, match="--i-understand-synthetic"):
            await demo_synth.run(args(tmp_path, remove=True, apply_remove=True), session)
        assert (await demo_synth.run(args(tmp_path, i_understand_synthetic=True), session))[1].startswith("Dry run")


class RecordingReader:
    """A provider history reader: returns real orders plus one forged synth- id."""

    def __init__(self, orders: list[OrderSummary]) -> None:
        self.orders = orders
        self.plans = []

    async def read_history(self, plan):
        self.plans.append(plan)
        return HistoryRead(coverage="full_history", orders=list(self.orders), list_calls=1)


async def test_provider_import_ignores_synthetic_orders(factory, tmp_path: Path) -> None:
    actor = await user(factory)
    async with factory() as session:
        await demo_synth.run(args(tmp_path, apply=True), session)
        synth_ids = {row.provider_order_id for row in await synth_rows(factory)}
        now = datetime.now(UTC)
        forged = real_order(next(iter(synth_ids)), now - timedelta(days=1), "Forged Item 1 pc")
        reader = RecordingReader([real_order("Z-REAL-2", now - timedelta(days=2), "Real Item 1 pc"), forged])
        graph = PurchaseGraphService(session)
        result = await graph.sync(actor, reader, provider="zepto")
        (plan,) = reader.plans
        assert not any(is_synthetic_order_id(value) for value in plan.known_ids)
        assert result.imported_orders == 1  # the forged synth- id was not imported
        last = await graph.last_real_order(actor)
        assert last is not None and last.provider_order_id == "Z-REAL-2"
        forged_row = (await session.execute(select(PurchaseOrderNode).where(PurchaseOrderNode.provider_order_id == forged.order_id))).scalar_one()
        assert forged_row.provider_lookup_id is None
        names_in = {name for (name,) in (await session.execute(select(PurchaseProductNode.name))).all()}
        assert "Forged Item 1 pc" not in names_in


async def test_synthetic_history_feeds_patterns_recall_and_summary(factory, tmp_path: Path) -> None:
    actor = await user(factory)
    async with factory() as session:
        await demo_synth.run(args(tmp_path, apply=True, end=datetime.now(UTC).date().isoformat()), session)
        suggestions = await PurchaseGraphService(session).suggestions(actor, limit=10)
        assert any("Taaza" in item.product_name for item in suggestions)
        assert any(evidence.kind in {"repeat_frequency", "repeat_cadence"} for item in suggestions for evidence in item.patterns)
        recall = await recall_for_person(session, USER, "milk")
        assert any(candidate.source == "purchase_history" and "Taaza" in (candidate.product_name or "") for candidate in recall.candidates)
        summary = await summarize_memory(session, USER)
    orders_group = next(group for group in summary.groups if group.id == "from_orders")
    assert {item.source for item in orders_group.items} == {"demo_history"}
    assert {item.source_label for item in orders_group.items} == {"From demo history"}


def test_provider_facing_order_features_never_read_the_purchase_graph() -> None:
    """Tracking, order status, reconciliation, order help and order placement use the stores'
    own order lists; only memory features read the purchase graph, where synth- orders live."""
    backend = REPO / "backend" / "app"
    paths = [
        *sorted((backend / "tracking").rglob("*.py")),
        *sorted((backend / "support").rglob("*.py")),
        *sorted((backend / "shopping").rglob("*.py")),
        *sorted((backend / "commerce").rglob("*.py")),
        backend / "voice" / "order_status_tools.py",
        backend / "voice" / "order_tools.py",
        backend / "voice" / "express_tools.py",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "PurchaseOrderNode" not in text and "PurchaseGraphService" not in text, path


def test_realistic_mode_groups_purchases_into_a_few_orders_a_week() -> None:
    answers = parse_answers(ANSWERS)
    dense = demo_synth.generate(answers, months=12, seed=42, end=END)
    real = demo_synth.generate(answers, months=12, seed=42, end=END, realistic=True)

    weeks = 12 * 30 / 7
    assert 3 * weeks * 0.8 <= len(real) <= 4 * weeks * 1.05
    assert len(real) < len(dense) / 2
    # Same purchases, just fewer orders: total units are preserved.
    assert sum(i.quantity for o in real for i in o.items) == sum(i.quantity for o in dense for i in o.items)
    assert len({o.order_id for o in real}) == len(real)
    assert real == demo_synth.generate(answers, months=12, seed=42, end=END, realistic=True)
