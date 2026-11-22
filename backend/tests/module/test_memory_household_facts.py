"""Household facts in use: diet/keep-out/avoid filtering, usual quantities, ordering and the
'what do you know about me' summary, through VoiceToolbox on SQLite with fake stores."""

import argparse
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.database.base import Base
from app.memory import demo_seed
from app.memory.household import CONSENT_TYPES, PreferenceSpec
from app.memory.household_rules import HouseholdRules, Rule, food_decision, usual_quantity
from app.memory.household_service import HouseholdPreferenceService
from app.memory.recall import DatabasePreferenceRecaller
from app.memory.remembered import memory_summary_block
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService
from app.memory.summary import summarize_memory
from app.voice.toolbox import VoiceToolbox
from tests.shopping_fakes import World

HOUSEHOLD = "house-facts"


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'facts.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def product(name: str, brand: str, pack: str, price: float, pid: str, eta: str = "10") -> Product:
    return Product(
        product_id=pid, sku_id=f"sku-{pid}", spin_id=f"spin-{pid}", name=name, brand=brand,
        pack_size=pack, price=Price(mrp=price, offer_price=price), available=True,
        delivery_value=eta, delivery_unit="mins",
    )


class Searcher:
    def __init__(self, products: list[Product]) -> None:
        self.products = products

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=self.products)


async def seed(factory, tmp_path: Path, answers: dict) -> None:
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(HOUSEHOLD)
        for memory_type in CONSENT_TYPES:
            await MemoryService(session).set_permission(
                actor, memory_type, "personalize_search", MemoryPermissionUpsert(allowed=True)
            )
    path = tmp_path / "home.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    namespace = argparse.Namespace(
        file=str(path), user_id=HOUSEHOLD, dry_run=False, apply=True, remove_seeded=False, turn_on_memory=False
    )
    async with factory() as session:
        await demo_seed.run(namespace, session)


def box(factory, swiggy: list[Product], zepto: list[Product] | None = None, world: World | None = None) -> VoiceToolbox:
    world = world or World()
    return VoiceToolbox(
        Searcher(swiggy), world.baskets, world.approvals, "addr-s",
        Searcher(zepto or []), "addr-z", HOUSEHOLD,
        preference_recall=DatabasePreferenceRecaller(factory),
    )


async def call(toolbox: VoiceToolbox, name: str, **arguments: object) -> dict:
    return (await toolbox.execute(name, json.dumps(arguments))).model_output


SNACKS = [
    product("Haldiram's Peanut Chikki", "Haldiram's", "200 g", 60, "chikki"),
    product("Licious Chicken Seekh Kebab", "Licious", "250 g", 199, "kebab"),
    product("Everest Chicken Masala", "Everest", "100 g", 70, "masala"),
    product("Lay's Classic Salted Potato Chips", "Lay's", "52 g", 20, "lays"),
    product("Britannia Good Day Cashew Cookies", "Britannia", "200 g", 40, "goodday"),
    product("Cadbury Dairy Milk Chocolate", "Cadbury", "50 g", 45, "choc"),
]


async def test_diet_keep_outs_and_avoid_filter_search(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {"diet": "vegetarian", "allergies": ["peanuts"], "avoid": ["chocolate"]})
    result = await call(box(factory, SNACKS), "search_products", query="snacks")

    names = [entry["name"] for entry in result["results"]]
    assert "Haldiram's Peanut Chikki" not in names  # keep-out
    assert "Licious Chicken Seekh Kebab" not in names  # diet
    assert "Cadbury Dairy Milk Chocolate" not in names  # avoid
    assert result["hidden_by_household_rules"] == 3
    masala = next(entry for entry in result["results"] if entry["name"] == "Everest Chicken Masala")
    assert masala["avoided"] is True and masala["avoided_because"] == "vegetarian household"
    assert result["ingredient_check"] == ["peanuts"]
    assert "check the pack label" in result["note"]


async def test_asking_for_the_avoided_thing_keeps_it_flagged(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {"diet": "vegetarian", "allergies": ["peanuts"]})
    result = await call(box(factory, SNACKS), "search_products", query="peanut chikki")
    chikki = next(entry for entry in result["results"] if entry["name"] == "Haldiram's Peanut Chikki")
    assert chikki["avoided_because"] == "keeps out peanuts"


def test_jain_eggetarian_and_safe_names() -> None:
    jain = HouseholdRules([Rule("diet", "jain")])
    assert food_decision(jain, "Lay's Potato Chips", "chips") == {"drop": True}
    assert food_decision(jain, "Jain Pav Bhaji Masala", "masala") is None
    egg = HouseholdRules([Rule("diet", "eggetarian")])
    assert food_decision(egg, "Farm Fresh Eggs 6 pcs", "eggs") is None
    assert food_decision(egg, "Chicken Nuggets", "nuggets") == {"drop": True}
    veg = HouseholdRules([Rule("diet", "vegetarian")])
    assert food_decision(veg, "Eggless Chocolate Cake", "cake") is None
    assert food_decision(veg, "Egg Noodles", "noodles") == {"drop": True}
    nuts = HouseholdRules([Rule("avoid_ingredient", "tree nuts")])
    assert food_decision(nuts, "Good Day Cashew Cookies", "cookies") == {"drop": True}
    assert food_decision(nuts, "Nut-free granola", "granola") is None


MILK = [
    product("Mother Dairy Toned Milk", "Mother Dairy", "1 L", 54, "md", eta="8"),
    product("Amul Taaza Toned Milk", "Amul", "1 L", 56, "amul", eta="12"),
    product("Nandini Toned Milk", "Nandini", "1 L", 50, "nandini", eta="30"),
]


async def test_usual_brand_ranks_first_and_quantity_defaults_add(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {
        "usual_buys": [{"category": "milk", "brand": "Amul", "product": "Amul Taaza Toned Milk", "size": "1 L", "quantity": 2}],
    })
    toolbox = box(factory, MILK)
    result = await call(toolbox, "search_products", query="milk")
    first = result["results"][0]
    assert first["name"] == "Amul Taaza Toned Milk"
    assert first["remembered"] == {"source": "confirmed_preference"}
    assert first["usual_quantity"] == 2
    assert "usual_quantity" not in result["results"][1]

    added = await call(toolbox, "add_to_basket", option_id=first["option_id"], quantity=None)
    assert added["quantity_added"] == 2 and added["quantity_from"] == "household_usual"
    assert added["items"][0]["quantity"] == 2

    other = await call(toolbox, "add_to_basket", option_id=result["results"][1]["option_id"], quantity=None)
    assert "quantity_from" not in other
    explicit = await call(toolbox, "add_to_basket", option_id=first["option_id"], quantity=1)
    assert "quantity_from" not in explicit


async def test_cheapest_style_orders_by_price(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {"value_preference": "cheapest"})
    result = await call(box(factory, MILK), "search_products", query="toned milk")
    assert [entry["name"] for entry in result["results"]] == [
        "Nandini Toned Milk", "Mother Dairy Toned Milk", "Amul Taaza Toned Milk",
    ]
    await seed(factory, tmp_path, {"value_preference": "balanced"})
    result = await call(box(factory, MILK), "search_products", query="toned milk")
    assert result["results"][0]["name"] == "Mother Dairy Toned Milk"  # provider order


def test_usual_quantity_needs_one_matching_rule() -> None:
    milk = MILK[1]
    rules = HouseholdRules([Rule("usual_quantity", "3", "milk"), Rule("usual_quantity", "2", "eggs")])
    assert usual_quantity(rules, "milk", milk) == 3
    assert usual_quantity(HouseholdRules([Rule("usual_quantity", "3", "milk"), Rule("prefer_brand", "Amul", "milk", 2)]), "milk", milk) is None


async def test_session_summary_puts_food_rules_first(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {
        "avoid": {"products": [f"thing{index}" for index in range(9)]},
        "diet": "jain",
        "allergies": ["sesame"],
    })
    memory = await DatabasePreferenceRecaller(factory).household_memory(HOUSEHOLD)
    block = memory_summary_block(memory)
    lines = [line for line in block.splitlines() if line.startswith("- ")]
    assert lines[0].startswith("- Diet: Jain food") or lines[1].startswith("- Diet: Jain food")
    assert "- Keep out: sesame" in lines[:2]


async def test_summary_groups_sources_and_voice_answer(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {
        "household": {"people": 2, "pets": "cat"},
        "diet": "eggetarian",
        "usual_buys": [{"category": "bread", "brand": "Harvest Gold"}],
        "avoid": {"stores": ["swiggy_instamart"]},
        "preferred_store": "zepto",
        "delivery_notes": ["Leave with the guard"],
        "language": "hinglish",
    })
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(HOUSEHOLD)
        service = HouseholdPreferenceService(session)
        await service.save_stated(actor, PreferenceSpec(kind="avoid_brand", value="Brand Y", user_words="Brand Y kabhi nahi"))
        await service.propose(actor, PreferenceSpec(kind="prefer_pack_size", item="curd", value="400 g"), origin="inferred")
        summary = await summarize_memory(session, HOUSEHOLD)

    groups = {group.id: group for group in summary.groups}
    assert list(groups) == ["home", "food", "usual_buys", "avoid", "shopping_style"]
    assert [item.label for item in groups["home"].items][:2] == ["Reply language: Hinglish", "How to address you: neutral word forms"]
    assert {item.managed_by for item in groups["home"].items} == {"profile", "memory"}
    avoid = {item.label: item.source for item in groups["avoid"].items}
    assert avoid == {"Avoid Brand Y": "in_passing", "Never order from Swiggy Instamart": "told"}
    assert [item.label for item in groups["shopping_style"].items] == ["Shopping: order from Zepto"]
    assert [(item.label, item.source) for item in summary.suggestions] == [("Curd: 400 g pack", "repeated_choice")]
    assert summary.consent.saved_preferences == "on" and summary.consent.purchase_history == "off"

    spoken = await call(box(factory, MILK), "summarize_my_memory")
    assert spoken["status"] == "ok"
    assert [item["group"] for item in spoken["top"]] == ["Food", "Avoid", "Avoid", "Usual buys", "Home"]
    assert spoken["top"][0] == {"group": "Food", "summary": "Diet: Vegetarian, eggs are fine", "source": "told"}
    assert spoken["more_on_screen"] == summary.total - 5
    assert spoken["pending_suggestions"] == 1
    assert "baaki screen par" in spoken["note"]
    outcome = await box(factory, MILK).execute("summarize_my_memory", "{}")
    assert outcome.ui_event == {"type": "memory.summary", "total": summary.total}


async def test_summary_when_nothing_saved(factory) -> None:
    async with factory() as session:
        await MemoryService(session).actor_for_external_id(HOUSEHOLD)
    spoken = await call(box(factory, MILK), "summarize_my_memory")
    assert spoken["status"] == "nothing_saved"


DRINKS = [
    product("Coca-Cola Diet Coke Soft Drink Can", "Coca-Cola", "300 ml", 40, "diet"),
    product("Coca-Cola Original Soft Drink", "Coca-Cola", "750 ml", 40, "coke"),
    product("Sprite Lime Soft Drink", "Sprite", "750 ml", 40, "sprite"),
    product("Coke Zero Sugar Can", "Coca-Cola", "300 ml", 40, "zero"),
]


async def test_avoid_phrases_name_product_kinds(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {
        "avoid": [{"item": "sugary cold drinks", "reason": "prefers diet"}, {"item": "pork products"}],
        "pack_size_rule": "best value per unit",
    })
    drinks = await call(box(factory, DRINKS), "search_products", query="cold drink")
    assert [entry["name"] for entry in drinks["results"]] == ["Coca-Cola Diet Coke Soft Drink Can", "Coke Zero Sugar Can"]
    meats = await call(box(factory, [
        product("Pork Sausages", "Brand", "250 g", 200, "pork"),
        product("Chicken Breast Boneless", "Brand", "500 g", 250, "chicken"),
        product("Hamdard Rooh Afza", "Hamdard", "750 ml", 180, "rooh"),
    ]), "search_products", query="meat")
    assert sorted(entry["name"] for entry in meats["results"]) == ["Chicken Breast Boneless", "Hamdard Rooh Afza"]


async def test_best_unit_value_orders_by_price_per_unit(factory, tmp_path: Path) -> None:
    await seed(factory, tmp_path, {"pack_size_rule": "best value per unit"})
    result = await call(box(factory, [
        product("Maggi Masala Noodles", "Maggi", "70 g", 15, "one"),
        product("Maggi Masala Noodles 4 Pack", "Maggi", "280 g", 50, "four"),
        product("Maggi Masala Noodles 12 Pack", "Maggi", "840 g", 168, "twelve"),
    ]), "search_products", query="maggi noodles")
    assert [entry["name"] for entry in result["results"]][0] == "Maggi Masala Noodles 4 Pack"
