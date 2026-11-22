"""Live check of typed chat: the real /api/chat/turn loop with the real backend model.

Runs ``ChatTurnService.run_turn`` in-process (the code behind POST /api/chat/turn)
with the OpenAI Responses API, the voice delegation config and ``VoiceToolbox``.
Commerce is the static demo catalogue plus a few harness products, baskets are
in memory and household memory is a temporary SQLite store seeded from
``tests/fixtures/sample_households.json``. DNS lookups other than the OpenAI API
host are blocked, so no Swiggy or Zepto call is possible.

Usage (from backend/):
    uv run --with-requirements requirements.txt python -m evaluation.run_typed_chat \
        [--scenario ID ...] [--report PATH]
"""

import argparse
import asyncio
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.service import ChatTurnService, ChatTurnResult
from app.commerce.demo import (
    DEMO_ADDRESS_ID,
    DemoQuoteProvider,
    DemoSwiggyService,
    DemoZeptoQuoteProvider,
    DemoZeptoService,
)
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.core.config import get_settings
from app.database.base import Base
from app.memory import sample_seed
from app.memory.recall import DatabasePreferenceRecaller
from app.memory.service import MemoryService
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.models import BasketLine, ProductRef
from app.voice.prompts import VOICE_PROMPT_VERSION
from app.voice.service import LiveSessionService
from app.voice.sideband import SidebandHub
from app.voice.tools import ProductSearcher
from evaluation.run_voice_memory_recall import NetworkGuard

DEFAULT_REPORT_PATH = Path(__file__).with_name("typed_chat_report.json")
MAX_RUNS_PER_SCENARIO = 2

HARNESS_PRODUCTS = [
    ("fx-axe-dark-150", "AXE Dark Temptation Deodorant Body Spray", "AXE", "150 ml", 299, 199),
    ("fx-axe-dark-215", "AXE Dark Temptation Deodorant Body Spray", "AXE", "215 ml", 399, 299),
    ("fx-axe-apollo-150", "AXE Apollo Deodorant Body Spray", "AXE", "150 ml", 299, 199),
    ("fx-diet-coke-300", "Diet Coke Soft Drink Can", "Coca-Cola", "300 ml", 45, 40),
    ("fx-diet-coke-750", "Diet Coke Soft Drink Bottle", "Coca-Cola", "750 ml", 60, 55),
]

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "diet_coke_with_memory",
        "household": "USR-001",
        "user": "Diet Coke chahiye",
        "expect": {"search_query_contains": "coke", "max_questions": 1},
    },
    {
        "id": "deo_asks_one_question",
        "household": "USR-001",
        "user": "deo chahiye",
        "expect": {"no_search": True, "exact_questions": 1},
    },
    {
        "id": "axe_dark_smallest_adds",
        "household": None,
        "user": "AXE Dark sabse chhota add karo",
        "expect": {"added_pack": "150 ml", "added_name_contains": "Dark"},
    },
    {
        "id": "bill_two_stores_asks_store",
        "household": None,
        "seed_both_baskets": True,
        "user": "bill bana do",
        "expect": {"needs_store": True, "no_bill_event": True, "exact_questions": 1},
    },
]


class OverlaySearcher:
    """Harness products first; anything else falls through to the demo store."""

    def __init__(self, base: ProductSearcher, prefix: str) -> None:
        self.base = base
        self.queries: list[str] = []
        self.products = [
            Product(
                product_id=pid,
                sku_id=f"{prefix}-sku-{pid}",
                spin_id=f"{prefix}-spin-{pid}",
                name=name,
                brand=brand,
                pack_size=size,
                price=Price(mrp=mrp, offer_price=offer),
                available=True,
            )
            for pid, name, brand, size, mrp, offer in HARNESS_PRODUCTS
        ]

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        words = {word for word in re.split(r"[^a-z0-9]+", query.lower()) if len(word) > 2}
        matched = [
            product
            for product in self.products
            if words & set(re.split(r"[^a-z0-9]+", f"{product.name} {product.brand}".lower()))
        ]
        if matched:
            return ProductSearchResult(products=matched)
        return await self.base.search(query, address_id, offset)


class RecordingToolbox:
    """Wraps the real VoiceToolbox to record each call's name, arguments and status."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    async def execute(self, name: str, raw_arguments: str) -> Any:
        outcome = await self.inner.execute(name, raw_arguments)
        try:
            arguments: Any = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = raw_arguments
        self.calls.append({"name": name, "arguments": arguments, "model_output": outcome.model_output})
        return outcome


async def build(scenario: dict[str, Any], workdir: Path) -> tuple[ChatTurnService, BasketService, str]:
    settings = get_settings()
    engine = create_async_engine(f"sqlite+aiosqlite:///{workdir / (scenario['id'] + '.db')}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    household_id = f"typed-chat-{scenario['household'] or 'empty'}"
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(household_id)
        if scenario["household"]:
            await sample_seed.seed_sample_household(
                session, actor, sample_seed.load_sample_households(), scenario["household"]
            )
    basket_repo, approval_repo = InMemoryBasketRepository(), InMemoryApprovalRepository()
    quotes = {"swiggy_instamart": DemoQuoteProvider(), "zepto": DemoZeptoQuoteProvider()}
    baskets = BasketService(basket_repo, approval_repo)
    voice = LiveSessionService(
        settings,
        OverlaySearcher(DemoSwiggyService(), "swiggy"),
        SidebandHub(settings),
        baskets=baskets,
        approvals=ApprovalService(basket_repo, approval_repo, lambda name: quotes[name]),
        zepto_searcher=OverlaySearcher(DemoZeptoService(), "zepto"),
        zepto_connection=DemoZeptoService(),
        preference_recall=DatabasePreferenceRecaller(factory),
    )
    return ChatTurnService(settings, voice), baskets, household_id


async def seed_both_baskets(baskets: BasketService, household_id: str) -> None:
    for provider in ("swiggy_instamart", "zepto"):
        await baskets.add_line(
            household_id,
            provider,
            BasketLine(
                ref=ProductRef(provider=provider, product_id="demo-oil", variant_id=f"{provider}-spin-oil", sku_id="demo-sku-oil"),
                name="Fortune Sunflower Oil",
                brand="Fortune",
                pack_size="1 L",
                unit_price_paise=15500,
                mrp_paise=17500,
                quantity=1,
            ),
        )


def check(expect: dict[str, Any], result: ChatTurnResult, calls: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    reply = result.reply
    names = [call["name"] for call in calls]
    questions = reply.count("?")
    if re.search(r"[\u0900-\u097f]", reply):
        failures.append("Reply is not Roman script.")
    if re.search(r"\b(?:raha|gaya|sakta|karta|dunga|lunga|karunga)\s+hoon\b", reply, re.IGNORECASE):
        failures.append("Masculine self-reference.")
    if "max_questions" in expect and questions > expect["max_questions"]:
        failures.append(f"{questions} questions (max {expect['max_questions']}).")
    if "exact_questions" in expect and questions != expect["exact_questions"]:
        failures.append(f"{questions} questions (expected {expect['exact_questions']}).")
    needle = expect.get("search_query_contains")
    if needle and not any(
        call["name"] == "search_products" and needle in str(call["arguments"].get("query", "")).lower()
        for call in calls
    ):
        failures.append(f"No search_products query containing {needle!r} ({names}).")
    if expect.get("no_search") and "search_products" in names:
        failures.append("Searched before the user answered.")
    if "added_pack" in expect:
        adds = [call for call in calls if call["name"] == "add_to_basket" and call["model_output"].get("status") == "added"]
        items = [item for call in adds for item in call["model_output"].get("items", [])]
        if not any(
            item.get("pack_size") == expect["added_pack"] and expect["added_name_contains"] in item.get("name", "")
            for item in items
        ):
            failures.append(f"Expected an added {expect['added_name_contains']} {expect['added_pack']} ({names}).")
        if "basket.updated" not in [event["type"] for event in result.ui_events]:
            failures.append("No basket.updated ui_event.")
    if expect.get("needs_store") and not any(
        call["name"] == "request_approval" and call["model_output"].get("status") == "needs_store" for call in calls
    ):
        failures.append(f"request_approval did not return needs_store ({names}).")
    if expect.get("no_bill_event") and any(event["type"] == "basket.approval_requested" for event in result.ui_events):
        failures.append("A bill was requested without asking which store.")
    return failures


async def run_scenario(scenario: dict[str, Any], workdir: Path) -> dict[str, Any]:
    chat, baskets, household_id = await build(scenario, workdir)
    if scenario.get("seed_both_baskets"):
        await seed_both_baskets(baskets, household_id)
    original = chat.voice.tool_executor
    recorders: list[RecordingToolbox] = []

    def recording_executor(*args: Any) -> RecordingToolbox:
        recorders.append(RecordingToolbox(original(*args)))
        return recorders[-1]

    chat.voice.tool_executor = recording_executor  # type: ignore[method-assign]
    started = datetime.now(UTC)
    try:
        result = await chat.run_turn(household_id, None, scenario["user"], DEMO_ADDRESS_ID, DEMO_ADDRESS_ID)
    except Exception as exc:  # noqa: BLE001 - a live failure is a reported result
        return {"id": scenario["id"], "user": scenario["user"], "passed": False, "error": f"{type(exc).__name__}: {exc}"}
    calls = recorders[0].calls if recorders else []
    failures = check(scenario["expect"], result, calls)
    return {
        "id": scenario["id"],
        "user": scenario["user"],
        "passed": not failures,
        "reply": result.reply,
        "tool_calls": [
            {"name": call["name"], "arguments": call["arguments"], "status": call["model_output"].get("status")}
            for call in calls
        ],
        "ui_event_types": [event["type"] for event in result.ui_events],
        "failures": failures,
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.openai_api_key.get_secret_value().strip():
        raise SystemExit("OPENAI_API_KEY is not configured.")
    guard = NetworkGuard(urlparse(settings.openai_base_url).hostname or "api.openai.com")
    guard.install()

    previous = json.loads(args.report.read_text(encoding="utf-8")) if args.report.exists() else {}
    runs: list[dict[str, Any]] = previous.get("runs", [])
    with tempfile.TemporaryDirectory() as workdir:
        for scenario in SCENARIOS:
            if args.scenario and scenario["id"] not in args.scenario:
                continue
            done = sum(1 for run in runs if run["id"] == scenario["id"])
            if done >= MAX_RUNS_PER_SCENARIO:
                print(f"SKIP {scenario['id']}: already run {done} times")
                continue
            result = await run_scenario(scenario, Path(workdir))
            result["run"] = done + 1
            result["finished_at"] = datetime.now(UTC).isoformat()
            runs.append(result)
            print(f"{'PASS' if result['passed'] else 'FAIL'} {scenario['id']} (run {result['run']})")
            print(f"    user: {scenario['user']}\n    reply: {result.get('reply', result.get('error'))}")
            print(f"    tools: {[(call['name'], call['status']) for call in result.get('tool_calls', [])]}")
            for failure in result.get("failures", []):
                print(f"    FAIL: {failure}")
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": settings.live_backend_model,
        "prompt_version": VOICE_PROMPT_VERSION,
        "blocked_lookups": sorted(set(previous.get("blocked_lookups", [])) | set(guard.blocked)),
        "runs": runs,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
