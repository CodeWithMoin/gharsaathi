"""Per-stage latency benchmark for shopping turns (content-free numbers only).

Offline (default, no network): fake stores with fixed latencies drive the real
search executor, Swiggy service and typed-chat loop, so provider fan-out,
cooldown short-circuit and tool overlap are measured without any model.

Live (``--live``): the real backend model through the Responses API with the
voice delegation config, executing tools through ``VoiceToolbox`` on the demo
catalogue plus harness fixtures (same as ``run_voice_delegation``). DNS is
restricted to the OpenAI host, so no Swiggy/Zepto call is possible. Runs both the
delegation-style loop and the typed ``ChatTurnService`` loop in-process.

Usage (from backend/):
    uv run --with-requirements requirements.txt python -m evaluation.run_turn_latency
    uv run --with-requirements requirements.txt python -m evaluation.run_turn_latency \
        --live --runs 5 --effort low --effort medium [--report PATH]
"""

import argparse
import asyncio
import json
import logging
import statistics
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from app.commerce.demo import DEMO_ADDRESS_ID
from app.commerce.swiggy.errors import SwiggyRateLimited
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.swiggy.service import SwiggyService
from app.core.config import get_settings
from app.voice.tools import MultiProviderShoppingToolExecutor, ProviderSearchTarget


DEFAULT_REPORT_PATH = Path(__file__).with_name("turn_latency_report.json")

CHOCOLATE_FIXTURE = {
    "swiggy_instamart": [
        {"product_id": "fx-choc-amul-150", "name": "Amul Dark Chocolate", "brand": "Amul", "pack_size": "150 g", "mrp": 200, "offer_price": 185},
        {"product_id": "fx-choc-lindt-100", "name": "Lindt Excellence 70% Dark Chocolate", "brand": "Lindt", "pack_size": "100 g", "mrp": 399, "offer_price": 359},
    ],
    "zepto": [
        {"product_id": "fx-choc-amul-150", "name": "Amul Dark Chocolate", "brand": "Amul", "pack_size": "150 g", "mrp": 200, "offer_price": 179},
        {"product_id": "fx-choc-bournville-80", "name": "Cadbury Bournville Rich Cocoa Dark Chocolate", "brand": "Cadbury", "pack_size": "80 g", "mrp": 110, "offer_price": 99},
    ],
}

LIVE_SCENARIOS: list[dict[str, Any]] = [
    {"id": "choc-recall", "catalogue": "chocolate", "turns": ["dark chocolate chahiye", "haan, pehla wala add kar do"]},
    {"id": "choc-specified", "catalogue": "chocolate", "turns": ["Amul Dark Chocolate 150 g Zepto basket mein add kar do"]},
    {"id": "coke-memory", "catalogue": "coke_fixture", "memory_from": "s8-branded-recall-remembered-size", "turns": ["Diet Coke chahiye", "haan wahi"]},
]


# ---------------------------------------------------------------- offline


def _product(product_id: str, name: str) -> Product:
    return Product(
        product_id=product_id, sku_id=f"sku-{product_id}", spin_id=f"spin-{product_id}",
        name=name, brand="Bench", pack_size="1 kg", price=Price(mrp=100, offer_price=90), available=True,
    )


class SleepySearcher:
    def __init__(self, seconds: float, prefix: str) -> None:
        self.seconds = seconds
        self.prefix = prefix

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        await asyncio.sleep(self.seconds)
        return ProductSearchResult(products=[_product(f"{self.prefix}{i}", f"Bench item {i}") for i in range(4)])


def _executor(swiggy_s: float, zepto_s: float) -> MultiProviderShoppingToolExecutor:
    return MultiProviderShoppingToolExecutor(
        [
            ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", SleepySearcher(swiggy_s, "s"), "addr", True),
            ProviderSearchTarget("zepto", "Zepto", SleepySearcher(zepto_s, "z"), "addr", True),
        ]
    )


async def _timed(work: Any) -> float:
    started = time.perf_counter()
    await work
    return round((time.perf_counter() - started) * 1000, 1)


class CoolingClient:
    """Stands in for SwiggyMcpClient: a cooldown starts while one request is in flight."""

    def __init__(self) -> None:
        self.cooling = False

    async def cooldown_remaining(self) -> int:
        return 30 if self.cooling else 0


class CoolingAdapter:
    def __init__(self, client: CoolingClient) -> None:
        self.client = client

    async def search_products(self, query: str, address_id: str, offset: int = 0) -> ProductSearchResult:
        if self.client.cooling:
            raise SwiggyRateLimited("rate limited", 30)
        await asyncio.sleep(2.0)
        self.client.cooling = True
        return ProductSearchResult(products=[])


async def _cooldown_case() -> float:
    client = CoolingClient()
    service = SwiggyService(None, client, CoolingAdapter(client))  # type: ignore[arg-type]
    first = asyncio.create_task(service.search("atta", "addr", 0))
    await asyncio.sleep(0.05)
    client.cooling = True
    started = time.perf_counter()
    try:
        await service.search("dahi", "addr", 0)
    except SwiggyRateLimited:
        pass
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    await first
    return elapsed


class FakeResponses:
    """Round 1 asks for two searches, round 2 answers. No model latency."""

    def __init__(self) -> None:
        self.calls = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls % 2 == 1:
            output = [
                SimpleNamespace(type="function_call", name="search_products", arguments=json.dumps({"query": q}), call_id=f"c{q}")
                for q in ("atta", "dahi")
            ]
            return SimpleNamespace(id=f"r{self.calls}", output=output, output_text="")
        return SimpleNamespace(id=f"r{self.calls}", output=[], output_text="Options screen par hain.")


async def _typed_two_search_round(swiggy_s: float, zepto_s: float) -> float:
    from app.chat.service import ChatTurnService
    from app.shopping.approval import ApprovalService
    from app.shopping.basket import BasketService
    from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
    from app.voice.service import LiveSessionService
    from app.voice.sideband import SidebandHub

    settings = get_settings().model_copy(update={"voice_event_log": False})
    baskets, approvals = InMemoryBasketRepository(), InMemoryApprovalRepository()
    voice = LiveSessionService(
        settings, SleepySearcher(swiggy_s, "s"), SidebandHub(settings),
        baskets=BasketService(baskets, approvals),
        approvals=ApprovalService(baskets, approvals, lambda name: None),  # type: ignore[arg-type,return-value]
        zepto_searcher=SleepySearcher(zepto_s, "z"),
    )
    chat = ChatTurnService(settings, voice, responses=FakeResponses())
    return await _timed(chat.run_turn("bench_user", None, "atta aur dahi dikha do", "addr", "addr"))


async def offline(runs: int) -> dict[str, Any]:
    cases: dict[str, list[float]] = {
        "search_both_healthy_ms (Swiggy 0.7 s, Zepto 1.1 s)": [],
        "search_zepto_slow_ms (Swiggy 0.7 s, Zepto 9 s)": [],
        "swiggy_cooldown_while_busy_ms (in-flight request 2 s)": [],
        "typed_round_two_searches_ms (stores 0.7/1.1 s)": [],
    }
    keys = list(cases)
    for _ in range(runs):
        cases[keys[0]].append(await _timed(_executor(0.7, 1.1).execute("search_products", '{"query": "atta"}')))
        cases[keys[1]].append(await _timed(_executor(0.7, 9.0).execute("search_products", '{"query": "atta"}')))
        cases[keys[2]].append(await _cooldown_case())
        cases[keys[3]].append(await _typed_two_search_round(0.7, 1.1))
    return {name: round(statistics.median(values), 1) for name, values in cases.items()}


# ---------------------------------------------------------------- live


class StageLog(logging.Handler):
    """Collects the content-free event=... timing lines emitted with VOICE_EVENT_LOG."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("event=") and "timing" in message.split()[0]:
            parts = dict(part.split("=", 1) for part in message.split() if "=" in part)
            self.lines.append(parts)


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    details = getattr(usage, "output_tokens_details", None)
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    return {
        "input_tokens": usage.input_tokens,
        "cached_tokens": cached,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": getattr(details, "reasoning_tokens", 0) or 0,
    }


async def _delegation_turn(client: Any, delegation: dict[str, Any], toolbox: Any, text: str, previous: str | None) -> tuple[dict[str, Any], str]:
    toolbox.user_words.add(text)
    request_input: list[dict[str, Any]] = [{"role": "user", "content": text}]
    rounds: list[dict[str, Any]] = []
    started = time.perf_counter()
    for _ in range(8):
        mark = time.perf_counter()
        response = await client.responses.create(
            model=delegation["model"], instructions=delegation["instructions"], tools=delegation["tools"],
            tool_choice=delegation["tool_choice"], parallel_tool_calls=delegation["parallel_tool_calls"],
            input=request_input, previous_response_id=previous,
            **({"reasoning": delegation["reasoning"]} if "reasoning" in delegation else {}),
        )
        model_ms = round((time.perf_counter() - mark) * 1000, 1)
        previous = response.id
        calls = [item for item in response.output if item.type == "function_call"]
        entry: dict[str, Any] = {"model_ms": model_ms, **_usage(response), "tools": [c.name for c in calls]}
        rounds.append(entry)
        if not calls:
            break
        mark = time.perf_counter()
        outcomes = await asyncio.gather(*(toolbox.execute(c.name, c.arguments) for c in calls))
        entry["tool_ms"] = round((time.perf_counter() - mark) * 1000, 1)
        request_input = [
            {"type": "function_call_output", "call_id": c.call_id, "output": json.dumps(o.model_output, ensure_ascii=False)}
            for c, o in zip(calls, outcomes)
        ]
    return {
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "rounds": rounds,
        "reply_chars": len(getattr(response, "output_text", "") or ""),
    }, previous  # type: ignore[return-value]


def _scenario_parts(scenario: dict[str, Any], data: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    catalogues = {**data["catalogues"], "chocolate": CHOCOLATE_FIXTURE}
    source = next((s for s in data["scenarios"] if s["id"] == scenario.get("memory_from")), {})
    harness_scenario = {"memory": source.get("memory")} if source.get("memory") else {}
    return scenario["catalogue"], catalogues, harness_scenario


async def live(runs: int, efforts: list[str], typed: bool) -> dict[str, Any]:
    from openai import AsyncOpenAI

    from app.memory.remembered import memory_summary_block
    from app.voice.service import build_session_config
    from app.voice.toolbox import VOICE_TOOL_DEFINITIONS
    from evaluation.run_voice_delegation import SCENARIOS_PATH, NetworkGuard, build_toolbox

    base = get_settings()
    api_key = base.openai_api_key.get_secret_value().strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not configured.")
    NetworkGuard(urlparse(base.openai_base_url).hostname or "api.openai.com").install()
    client = AsyncOpenAI(api_key=api_key, base_url=base.openai_base_url)
    data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    stage_log = StageLog()
    logging.getLogger("app").addHandler(stage_log)

    results: dict[str, Any] = {}
    for effort in efforts:
        settings = base.model_copy(update={"live_backend_reasoning_effort": effort, "voice_event_log": True})
        has_effort_setting = "live_backend_reasoning_effort" in type(base).model_fields
        delegation = build_session_config(settings, VOICE_TOOL_DEFINITIONS)["delegation"]["responses"]
        if effort and not has_effort_setting:
            delegation = {**delegation, "reasoning": {"effort": effort}}
        label = effort or "default"
        per_turn: dict[str, list[dict[str, Any]]] = {}
        for run in range(runs):
            for scenario in LIVE_SCENARIOS:
                catalogue, catalogues, harness_scenario = _scenario_parts(scenario, data)
                toolbox = build_toolbox(catalogue, catalogues, harness_scenario)
                scenario_delegation = delegation
                if harness_scenario.get("memory"):
                    scenario_delegation = {
                        **delegation,
                        "instructions": delegation["instructions"] + memory_summary_block(toolbox.memory.recaller.memory),  # type: ignore[union-attr]
                    }
                previous: str | None = None
                for index, text in enumerate(scenario["turns"], 1):
                    stage_log.lines.clear()
                    turn, previous = await _delegation_turn(client, scenario_delegation, toolbox, text, previous)
                    turn["tool_stages"] = list(stage_log.lines)
                    per_turn.setdefault(f"delegation:{scenario['id']}:t{index}", []).append(turn)
                if typed:
                    await _typed_scenario(settings, client, scenario, catalogue, catalogues, harness_scenario, stage_log, per_turn, run)
            print(f"effort={label} run {run + 1}/{runs} done", flush=True)
        results[label] = {"turns": per_turn, "summary": _summarise(per_turn)}
    return results


async def _typed_scenario(
    settings: Any, client: Any, scenario: dict[str, Any], catalogue: str, catalogues: dict[str, Any],
    harness_scenario: dict[str, Any], stage_log: StageLog, per_turn: dict[str, list[dict[str, Any]]], run: int,
) -> None:
    from app.chat.service import ChatTurnService
    from app.voice.service import LiveSessionService
    from app.voice.sideband import SidebandHub
    from evaluation.run_voice_delegation import build_toolbox

    toolbox = build_toolbox(catalogue, catalogues, harness_scenario)
    voice = LiveSessionService(
        settings, toolbox.search.targets[0].searcher, SidebandHub(settings),
        baskets=toolbox.basket.baskets, approvals=toolbox.basket.approvals,
        zepto_searcher=toolbox.search.targets[1].searcher,
        preference_recall=toolbox.memory.recaller,
    )
    chat = ChatTurnService(settings, voice, responses=client.responses)
    user = f"bench_typed_{scenario['id']}_{run}"
    for index, text in enumerate(scenario["turns"], 1):
        stage_log.lines.clear()
        started = time.perf_counter()
        await chat.run_turn(user, None, text, DEMO_ADDRESS_ID, DEMO_ADDRESS_ID)
        per_turn.setdefault(f"typed:{scenario['id']}:t{index}", []).append(
            {"total_ms": round((time.perf_counter() - started) * 1000, 1), "tool_stages": list(stage_log.lines)}
        )


def _summarise(per_turn: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, turns in per_turn.items():
        row: dict[str, Any] = {"n": len(turns), "total_ms_median": round(statistics.median(t["total_ms"] for t in turns), 0)}
        if turns and "rounds" in turns[0]:
            row["rounds_median"] = statistics.median(len(t["rounds"]) for t in turns)
            row["model_ms_median"] = round(statistics.median(sum(r["model_ms"] for r in t["rounds"]) for t in turns), 0)
            row["first_round_ms_median"] = round(statistics.median(t["rounds"][0]["model_ms"] for t in turns), 0)
            row["final_round_ms_median"] = round(statistics.median(t["rounds"][-1]["model_ms"] for t in turns), 0)
            row["tool_ms_median"] = round(statistics.median(sum(r.get("tool_ms", 0) for r in t["rounds"]) for t in turns), 0)
            row["reasoning_tokens_median"] = statistics.median(sum(r.get("reasoning_tokens", 0) for r in t["rounds"]) for t in turns)
            row["input_tokens_median"] = statistics.median(sum(r.get("input_tokens", 0) for r in t["rounds"]) for t in turns)
            row["tool_patterns"] = sorted({"/".join(",".join(r["tools"]) or "reply" for r in t["rounds"]) for t in turns})
        summary[key] = row
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--effort", action="append", help="'' for the model default; repeatable.")
    parser.add_argument("--no-typed", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    report: dict[str, Any] = json.loads(args.report.read_text()) if args.report.exists() else {}
    label = args.label or "current"
    if args.live:
        result = await live(args.runs, args.effort if args.effort is not None else [get_settings().live_backend_reasoning_effort if hasattr(get_settings(), "live_backend_reasoning_effort") else ""], not args.no_typed)
        report.setdefault("live", {})[label] = result
        for effort, body in result.items():
            print(f"\n== effort {effort}")
            for key, row in body["summary"].items():
                print(key, json.dumps(row))
    else:
        result = await offline(args.runs)
        report.setdefault("offline", {})[label] = result
        print(json.dumps(result, indent=2))
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
