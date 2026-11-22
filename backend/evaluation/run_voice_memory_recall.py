"""Live check of the delegated voice backend: memory recall before search and user address form.

Drives the real backend model through the OpenAI Responses API with the exact
delegation instructions and voice tools, executing tool calls through
``VoiceToolbox`` backed by demo searchers, in-memory baskets and a temporary
SQLite memory store seeded from ``tests/fixtures/sample_households.json``.
DNS lookups other than the OpenAI API host are blocked, so no Swiggy or Zepto
call is possible.

Usage (from backend/):
    uv run --with-requirements requirements.txt python -m evaluation.run_voice_memory_recall \
        [--scenario ID ...] [--report PATH]
"""

import argparse
import asyncio
import json
import re
import socket
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.user_profile import UserProfile
from app.voice.prompts import BACKEND_INSTRUCTIONS, VOICE_PROMPT_VERSION
from app.voice.service import build_session_config
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from app.voice.tools import ProductSearcher

DEFAULT_REPORT_PATH = Path(__file__).with_name("voice_memory_recall_report.json")
MAX_TOOL_ROUNDS = 8
MASCULINE_SELF = re.compile(r"\b(?:raha|gaya|sakta|karta|dunga|lunga|karunga)\s+hoon\b", re.IGNORECASE)
MASCULINE_USER = re.compile(r"\b(?:sakte|rahe|chahte|karte|dekhte|lete)\s+hain\b", re.IGNORECASE)
FEMININE_USER = re.compile(r"\b(?:sakti|rahi|chahti|karti|dekhti|leti)\s+hain\b", re.IGNORECASE)
NEED_CLAIM = re.compile(r"\b(?:khatam|khatm|finish|out of stock|low hai|chahiye hoga)\b", re.IGNORECASE)

AXE_PRODUCTS = [
    ("fx-axe-dark-150", "AXE Dark Temptation Deodorant Body Spray", "AXE", "150 ml", 299, 199),
    ("fx-axe-dark-215", "AXE Dark Temptation Deodorant Body Spray", "AXE", "215 ml", 399, 299),
    ("fx-axe-apollo-150", "AXE Apollo Deodorant Body Spray", "AXE", "150 ml", 299, 199),
    ("fx-nivea-150", "Nivea Men Fresh Active Deodorant", "Nivea", "150 ml", 280, 225),
]

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "recall_axe_from_history",
        "household": "USR-001",
        "turns": [
            {
                "user": "deo order karna hai",
                "expect": {"recall_before_search": True, "no_search": True, "asks_wahi": True},
            },
            {"user": "haan wahi", "expect": {"tools_required": ["search_products", "add_to_basket"]}},
        ],
    },
    {
        "id": "empty_memory_searches_and_asks_once",
        "household": None,
        "turns": [
            {
                "user": "deo order karna hai",
                "expect": {"tools_required": ["search_products"], "asks_one_pick": True},
            }
        ],
    },
    {
        "id": "masculine_profile_user_forms",
        "household": None,
        "profile": {"first_name": "Aarav", "address_form": "masculine", "language": "hinglish"},
        "turns": [
            {
                "user": "Kya main Instamart aur Zepto dono par price compare kar sakta hoon?",
                "expect": {"masculine_user_forms": True},
            }
        ],
    },
]


@dataclass
class NetworkGuard:
    allowed_host: str
    blocked: list[str] = field(default_factory=list)

    def install(self) -> None:
        original = socket.getaddrinfo

        def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
            name = host.decode() if isinstance(host, bytes) else str(host)
            if name != self.allowed_host:
                self.blocked.append(name)
                raise OSError(f"Harness blocks network access to {name}")
            return original(host, *args, **kwargs)

        socket.getaddrinfo = guarded


class OverlaySearcher:
    """Harness deodorant products first; everything else falls through to the demo store."""

    def __init__(self, base: ProductSearcher, prefix: str) -> None:
        self.base = base
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
            for pid, name, brand, size, mrp, offer in AXE_PRODUCTS
        ]

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        words = {word for word in re.split(r"[^a-z0-9]+", query.lower()) if len(word) > 2}
        matched = [
            product
            for product in self.products
            if words & set(re.split(r"[^a-z0-9]+", f"{product.name} {product.brand}".lower()))
        ]
        if matched:
            return ProductSearchResult(products=matched)
        return await self.base.search(query, address_id, offset)


async def build_toolbox(household: str | None, db_path: Path) -> VoiceToolbox:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    household_id = f"harness-{household or 'empty'}"
    async with factory() as session:
        actor = await MemoryService(session).actor_for_external_id(household_id)
        if household:
            await sample_seed.seed_sample_household(
                session, actor, sample_seed.load_sample_households(), household
            )
    baskets, approvals = InMemoryBasketRepository(), InMemoryApprovalRepository()
    quotes = {"swiggy_instamart": DemoQuoteProvider(), "zepto": DemoZeptoQuoteProvider()}
    return VoiceToolbox(
        OverlaySearcher(DemoSwiggyService(), "swiggy"),
        BasketService(baskets, approvals),
        ApprovalService(baskets, approvals, lambda name: quotes[name]),
        DEMO_ADDRESS_ID,
        OverlaySearcher(DemoZeptoService(), "zepto"),
        DEMO_ADDRESS_ID,
        household_id,
        zepto_connection=DemoZeptoService(),
        preference_recall=DatabasePreferenceRecaller(factory),
    )


async def run_turn(
    client: AsyncOpenAI,
    delegation: dict[str, Any],
    toolbox: VoiceToolbox,
    user_text: str,
    previous: str | None,
) -> tuple[str, list[dict[str, Any]], str]:
    calls: list[dict[str, Any]] = []
    request_input: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.responses.create(
            model=delegation["model"],
            instructions=delegation["instructions"],
            tools=delegation["tools"],
            tool_choice=delegation["tool_choice"],
            parallel_tool_calls=delegation["parallel_tool_calls"],
            input=request_input,
            previous_response_id=previous,
        )
        previous = response.id
        function_calls = [item for item in response.output if item.type == "function_call"]
        if not function_calls:
            return response.output_text.strip(), calls, response.id
        request_input = []
        for item in function_calls:
            outcome = await toolbox.execute(item.name, item.arguments)
            calls.append(
                {
                    "name": item.name,
                    "arguments": json.loads(item.arguments),
                    "status": outcome.model_output.get("status"),
                    "model_output": outcome.model_output,
                }
            )
            request_input.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(outcome.model_output, ensure_ascii=False),
                }
            )
    raise RuntimeError(f"Model kept calling tools for more than {MAX_TOOL_ROUNDS} rounds.")


def check_turn(expect: dict[str, Any], reply: str, calls: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    names = [call["name"] for call in calls]
    lowered = reply.lower()
    if reply.count("?") > 1:
        failures.append("More than one question.")
    if MASCULINE_SELF.search(reply):
        failures.append("Masculine self-reference.")
    if NEED_CLAIM.search(reply):
        failures.append("Claims the item is needed or out of stock at home.")
    if re.search(r"[\u0900-\u097f]", reply):
        failures.append("Reply is not Roman script.")
    if expect.get("recall_before_search"):
        if "recall_household_preferences" not in names:
            failures.append(f"recall_household_preferences not called ({names}).")
        elif "search_products" in names and names.index("search_products") < names.index(
            "recall_household_preferences"
        ):
            failures.append("search_products called before recall.")
    if expect.get("no_search") and "search_products" in names:
        failures.append("Searched before the user answered.")
    if expect.get("asks_wahi"):
        if "wahi" not in lowered or "axe" not in lowered or "?" not in reply:
            failures.append("Did not ask 'AXE ... wahi chahiye?'.")
    if expect.get("asks_one_pick") and reply.count("?") != 1:
        failures.append("Expected exactly one question after showing the options.")
    if expect.get("masculine_user_forms"):
        if not MASCULINE_USER.search(reply):
            failures.append("No masculine user-directed form such as 'aap kar sakte hain'.")
        if FEMININE_USER.search(reply):
            failures.append("Feminine user-directed form despite a masculine profile.")
    for name in expect.get("tools_required", []):
        if name not in names:
            failures.append(f"Expected {name} (called {names}).")
    if "add_to_basket" in expect.get("tools_required", []):
        added = [call for call in calls if call["name"] == "add_to_basket" and call["status"] == "added"]
        if not added:
            failures.append("No successful add.")
    return failures


async def run_scenario(
    client: AsyncOpenAI, delegation: dict[str, Any], scenario: dict[str, Any], workdir: Path
) -> dict[str, Any]:
    if "profile" in scenario:
        profile = UserProfile.model_validate(scenario["profile"])
        delegation = build_session_config(get_settings(), VOICE_TOOL_DEFINITIONS, profile)[
            "delegation"
        ]["responses"]
    toolbox = await build_toolbox(scenario["household"], workdir / f"{scenario['id']}.db")
    previous: str | None = None
    turns: list[dict[str, Any]] = []
    for turn in scenario["turns"]:
        reply, calls, previous = await run_turn(client, delegation, toolbox, turn["user"], previous)
        failures = check_turn(turn["expect"], reply, calls)
        turns.append(
            {
                "user": turn["user"],
                "reply": reply,
                "tool_calls": [{key: call[key] for key in ("name", "arguments", "status")} for call in calls],
                "failures": failures,
            }
        )
    return {
        "id": scenario["id"],
        "passed": all(not turn["failures"] for turn in turns),
        "turns": turns,
        "finished_at": datetime.now(UTC).isoformat(),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    settings = get_settings()
    if settings.commerce_mode != "demo":
        raise SystemExit("Run this harness with COMMERCE_MODE=demo.")
    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not configured.")
    guard = NetworkGuard(urlparse(settings.openai_base_url).hostname or "api.openai.com")
    guard.install()
    delegation = build_session_config(settings, VOICE_TOOL_DEFINITIONS)["delegation"]["responses"]
    assert delegation["instructions"] == BACKEND_INSTRUCTIONS
    client = AsyncOpenAI(api_key=api_key, base_url=settings.openai_base_url)

    previous = json.loads(args.report.read_text(encoding="utf-8")) if args.report.exists() else {}
    runs: list[dict[str, Any]] = previous.get("runs", [])
    with tempfile.TemporaryDirectory() as workdir:
        for scenario in SCENARIOS:
            if args.scenario and scenario["id"] not in args.scenario:
                continue
            result = await run_scenario(client, delegation, scenario, Path(workdir))
            result["run"] = 1 + sum(1 for run in runs if run["id"] == scenario["id"])
            runs.append(result)
            print(f"{'PASS' if result['passed'] else 'FAIL'} {scenario['id']} (run {result['run']})")
            for turn in result["turns"]:
                print(f"    user: {turn['user']}\n    reply: {turn['reply']}")
                print(f"    tools: {[call['name'] for call in turn['tool_calls']]}")
                for failure in turn["failures"]:
                    print(f"    FAIL: {failure}")
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": delegation["model"],
        "prompt_version": VOICE_PROMPT_VERSION,
        "blocked_lookups": sorted(set(guard.blocked)),
        "runs": runs,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
