"""Turns-to-order and single-mutation latency with the real backend model.

Each scenario starts with one user request and answers the assistant's
questions from a short scripted list until the goal is reached (an order card
on screen, or an empty basket), counting user turns, model rounds and wall
time. Stores are the demo catalogue plus harness fixtures; DNS is restricted to
the OpenAI host, the order card is never tapped, so nothing can be ordered.

Usage (from backend/):
    uv run --with-requirements requirements.txt python -m evaluation.run_turns_to_order \
        --label after [--scenario ID ...] [--report PATH]
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.memory.remembered import memory_summary_block
from app.voice.prompts import VOICE_PROMPT_VERSION
from app.voice.service import build_session_config
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from evaluation.run_voice_delegation import NetworkGuard, build_toolbox

SCENARIOS_PATH = Path(__file__).with_name("turns_to_order_scenarios.json")
DEFAULT_REPORT_PATH = Path(__file__).with_name("turns_to_order_report.json")
MAX_ROUNDS = 8
MAX_USER_TURNS = 4
CARD_STATUSES = {"awaiting_tap", "ready"}


async def seed(toolbox: VoiceToolbox, lines: list[dict[str, Any]]) -> None:
    for line in lines:
        searched = await toolbox.search.execute("search_products", json.dumps({"query": line["query"]}))
        option = next(
            result["option_id"]
            for result in searched.model_output["results"]
            if result.get("store_id") == line["store"] and result.get("available")
        )
        added = await toolbox.basket.execute(
            "add_to_basket", json.dumps({"option_id": option, "quantity": line["quantity"]})
        )
        assert added.model_output["status"] == "added", added.model_output


async def basket_lines(toolbox: VoiceToolbox) -> int:
    total = 0
    for store in toolbox.basket.stores.values():
        total += len((await toolbox.basket._active(store)).lines)
    return total


def card_events(outcome: Any) -> list[dict[str, Any]]:
    events = [*getattr(outcome, "extra_ui_events", []), outcome.ui_event]
    return [event for event in events if event and event.get("type") == "order.confirmation_requested"]


async def one_turn(
    client: AsyncOpenAI, delegation: dict[str, Any], toolbox: VoiceToolbox, text: str, previous: str | None
) -> tuple[dict[str, Any], str | None]:
    toolbox.user_words.add(text)
    request_input: list[dict[str, Any]] = [{"role": "user", "content": text}]
    rounds: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    started = time.perf_counter()
    reply = ""
    for _ in range(MAX_ROUNDS):
        mark = time.perf_counter()
        response = await client.responses.create(
            model=delegation["model"],
            instructions=delegation["instructions"],
            tools=delegation["tools"],
            tool_choice=delegation["tool_choice"],
            parallel_tool_calls=delegation["parallel_tool_calls"],
            input=request_input,
            previous_response_id=previous,
            **{key: delegation[key] for key in ("reasoning", "service_tier") if key in delegation},
        )
        entry: dict[str, Any] = {"model_ms": round((time.perf_counter() - mark) * 1000, 1)}
        previous = response.id
        calls = [item for item in response.output if item.type == "function_call"]
        entry["tools"] = [call.name for call in calls]
        rounds.append(entry)
        if not calls:
            reply = (response.output_text or "").strip()
            break
        request_input = []
        statuses = []
        for call in calls:
            outcome = await toolbox.execute(call.name, call.arguments)
            statuses.append(outcome.model_output.get("status"))
            cards.extend(card_events(outcome))
            request_input.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(outcome.model_output, ensure_ascii=False),
                }
            )
        entry["statuses"] = statuses
    return {
        "user": text,
        "reply": reply,
        "rounds": rounds,
        "model_rounds": len(rounds),
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "cards": [card["confirmation"] for card in cards],
    }, previous


async def run_scenario(
    client: AsyncOpenAI, delegation: dict[str, Any], scenario: dict[str, Any], catalogues: dict[str, Any]
) -> dict[str, Any]:
    harness = {"memory": scenario["memory"]} if scenario.get("memory") else {}
    toolbox = build_toolbox(scenario["catalogue"], catalogues, harness)
    if scenario.get("memory"):
        delegation = {
            **delegation,
            "instructions": delegation["instructions"]
            + memory_summary_block(toolbox.memory.recaller.memory),  # type: ignore[union-attr]
        }
    await seed(toolbox, scenario.get("seed_basket", []))
    texts = [*scenario["turns"], *scenario.get("follow_ups", [])][:MAX_USER_TURNS]
    previous: str | None = None
    turns: list[dict[str, Any]] = []
    reached = False
    for text in texts:
        try:
            turn, previous = await one_turn(client, delegation, toolbox, text, previous)
        except Exception as exc:  # noqa: BLE001 - one failed call must not lose the report
            turns.append({"user": text, "error": type(exc).__name__})
            break
        turns.append(turn)
        if scenario["goal"] == "order_card":
            reached = bool(turn["cards"])
        else:
            reached = await basket_lines(toolbox) == 0
        if reached:
            break
    failures: list[str] = []
    card = next((c for turn in reversed(turns) for c in reversed(turn.get("cards", []))), None)
    if reached and card is not None:
        names = " ".join(item["name"] for item in card["items"])
        needle = scenario.get("expect_card_contains")
        if needle and needle.lower() not in names.lower():
            failures.append(f"card items {names!r} do not contain {needle!r}")
        for name, quantity in scenario.get("expect_card_quantity", {}).items():
            match = [item for item in card["items"] if name.lower() in item["name"].lower()]
            if not match or match[0]["quantity"] != quantity:
                failures.append(f"{name} quantity {[m['quantity'] for m in match]} != {quantity}")
    if not reached:
        failures.append(f"goal {scenario['goal']} not reached in {len(turns)} turn(s)")
    target = scenario.get("target_rounds")
    if target and turns and turns[0].get("model_rounds", 0) > target:
        failures.append(f"first turn took {turns[0]['model_rounds']} model rounds (target {target})")
    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "reached": reached,
        "turns_to_goal": len(turns) if reached else None,
        "model_rounds": sum(turn.get("model_rounds", 0) for turn in turns),
        "latency_ms": round(sum(turn.get("total_ms", 0) for turn in turns), 1),
        "passed": not failures,
        "failures": failures,
        "turns": turns,
    }


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    orders = [r for r in results if r["id"].startswith("o")]
    reached = [r for r in orders if r["reached"]]
    return {
        "order_scenarios": len(orders),
        "orders_reached": len(reached),
        "median_turns_to_order": statistics.median(r["turns_to_goal"] for r in reached) if reached else None,
        "median_latency_to_order_ms": statistics.median(r["latency_ms"] for r in reached) if reached else None,
        "median_model_rounds_to_order": statistics.median(r["model_rounds"] for r in reached) if reached else None,
        "per_scenario": {
            r["id"]: {
                "turns": r["turns_to_goal"],
                "model_rounds": r["model_rounds"],
                "latency_ms": r["latency_ms"],
                "passed": r["passed"],
            }
            for r in results
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--label", default="current")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not configured.")
    NetworkGuard(urlparse(settings.openai_base_url).hostname or "api.openai.com").install()
    delegation = build_session_config(settings, VOICE_TOOL_DEFINITIONS)["delegation"]["responses"]
    data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    from evaluation.run_voice_delegation import SCENARIOS_PATH as DELEGATION_PATH

    catalogues = {
        **json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))["catalogues"],
        **data["catalogues"],
    }
    scenarios = [s for s in data["scenarios"] if not args.scenario or s["id"] in args.scenario]
    client = AsyncOpenAI(api_key=api_key, base_url=settings.openai_base_url)
    results = []
    for scenario in scenarios:
        result = await run_scenario(client, delegation, scenario, catalogues)
        results.append(result)
        print(
            f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}: turns={result['turns_to_goal']} "
            f"rounds={result['model_rounds']} latency_ms={result['latency_ms']} {result['failures']}",
            flush=True,
        )
    report: dict[str, Any] = (
        json.loads(args.report.read_text(encoding="utf-8")) if args.report.exists() else {}
    )
    report.setdefault("runs", {})[args.label] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": delegation["model"],
        "prompt_version": VOICE_PROMPT_VERSION,
        "tools": [tool["name"] for tool in delegation["tools"]],
        "summary": summarise(results),
        "results": results,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["runs"][args.label]["summary"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
