"""Failure-mode scenarios for the delegated voice backend brain (docs/FAILURE_MODES.md).

Same harness as ``run_voice_delegation``: the real backend model through the
OpenAI Responses API with the exact delegation instructions and voice tools,
demo catalogues and in-memory repositories. Here a store, the bill or the
order tap is made to fail on purpose, and each turn is checked against what the
catalogue row says she must (and must not) say or do. DNS is restricted to the
OpenAI host; no Swiggy or Zepto call is possible, and no order can be placed
(the "tap" goes to a fake gateway that never succeeds).

Usage (from backend/):
    uv run --with-requirements requirements.txt python -m evaluation.run_failure_scenarios \
        [--scenario ID ...] [--report PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import AsyncOpenAI

from app.chat.service import ORDER_UPDATES_HEADER
from app.commerce.demo import DemoZeptoQuoteProvider
from app.commerce.swiggy.errors import SwiggyProviderError, SwiggyRateLimited
from app.commerce.zepto.errors import ZeptoAddressInvalid, ZeptoProviderError, ZeptoRateLimited
from app.commerce.swiggy.models import ProductSearchResult
from app.core.config import get_settings
from app.shopping.checkout import CheckoutService
from app.shopping.errors import ProviderUnavailable
from app.shopping.models import Basket, Quote
from app.shopping.order_confirmation import OrderResult
from app.voice.prompts import VOICE_PROMPT_VERSION
from app.voice.service import build_session_config
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from evaluation.run_voice_delegation import (
    HOUSEHOLD_ID,
    MAX_TOOL_ROUNDS,
    NetworkGuard,
    build_toolbox,
    check_turn,
)
from tests.shopping_fakes import InMemoryCheckoutAttempts


SCENARIOS_PATH = Path(__file__).with_name("failure_scenarios.json")
DEFAULT_REPORT_PATH = Path(__file__).with_name("failure_scenarios_report.json")
DELEGATION_SCENARIOS = Path(__file__).with_name("voice_delegation_scenarios.json")

NOT_FOUND_PATTERN = re.compile(
    r"nahi mila|nahi mil (?:raha|rahi)|not found|couldn't find|could not find|available nahi|"
    r"stock mein nahi|out of stock|nahi hai (?:zepto|swiggy)|मिला नहीं|नहीं मिला",
    re.IGNORECASE,
)
PAID_CLAIM_PATTERN = re.compile(
    r"payment (?:ho gaya|ho gayi|ho chuka|ho chuki|successful|done|complete)|paid ho gaya|pay ho gaya|"
    r"paise (?:kat|chale) gaye",
    re.IGNORECASE,
)
SECRET_ASK_PATTERN = re.compile(r"\b(?:pin|otp|cvv|card number)\b", re.IGNORECASE)
NEGATION = re.compile(r"\bnahi\b|\bnahin\b|\bnot\b|\bmat\b|\bna\b|नहीं|\bnever\b", re.IGNORECASE)


def _errors(kind: str) -> Exception:
    return {
        "zepto_down": ZeptoProviderError("Zepto MCP request failed."),
        "swiggy_down": SwiggyProviderError("Swiggy MCP request failed."),
        "zepto_rate_limited": ZeptoRateLimited("Zepto is temporarily rate-limiting this connection."),
        "swiggy_rate_limited": SwiggyRateLimited("Swiggy is rate-limiting.", retry_after_seconds=3 * 3600),
        "zepto_address_invalid": ZeptoAddressInvalid("The selected Zepto address is no longer saved in the account."),
    }[kind]


class FailingSearcher:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.calls += 1
        raise self.error


class FailingBill:
    """The Zepto bill preview fails the way the totalItems incident did."""

    name = "zepto"

    async def quote(self, basket: Basket) -> Quote:
        raise ProviderUnavailable(
            "Zepto's bill preview could not be verified because the item count was invalid. "
            "Nothing was ordered. Ref 1a2b3c4d."
        )


class TimingOutGateway:
    """The single Zepto order call times out after dispatch: the outcome is unknown."""

    def __init__(self) -> None:
        self.calls = 0

    async def confirm_cod(self, basket: Basket) -> object:
        self.calls += 1
        raise TimeoutError("Zepto did not answer the order call")


def install_faults(toolbox: VoiceToolbox, faults: dict[str, str]) -> None:
    targets = []
    for target in toolbox.search.targets:
        kind = faults.get(target.provider)
        targets.append(replace(target, searcher=FailingSearcher(_errors(kind))) if kind else target)
    toolbox.search.targets = targets


async def seed_basket(toolbox: VoiceToolbox, query: str, quantity: int) -> None:
    """Put one demo Zepto item in the basket without the model (setup, not under test)."""

    found = await toolbox.execute("search_products", json.dumps({"query": query}))
    option = next(
        result["option_id"]
        for result in found.model_output["results"]
        if result.get("store_id") == "zepto" and "option_id" in result
    )
    added = await toolbox.execute("add_to_basket", json.dumps({"option_id": option, "quantity": quantity}))
    assert added.model_output["status"] == "added", added.model_output


async def tap_with_unknown_outcome(toolbox: VoiceToolbox) -> TimingOutGateway:
    """The user's tap on the card the model prepared, with the order call timing out."""

    confirmations = toolbox.order.confirmations
    assert confirmations is not None
    card = next(iter(confirmations._items.values()))  # the card seeded for this scenario
    approvals = toolbox.basket.approvals
    gateway = TimingOutGateway()
    checkout = CheckoutService(
        approvals.baskets,
        approvals.approvals,
        InMemoryCheckoutAttempts(approvals.baskets, approvals.approvals),  # type: ignore[arg-type]
        DemoZeptoQuoteProvider(),
        gateway,
    )
    result = await confirmations.place(card.household_id, card.id, checkout)
    assert result.status == "outcome_unknown", result
    return gateway


async def run_turn(
    client: AsyncOpenAI,
    delegation: dict[str, Any],
    toolbox: VoiceToolbox,
    user_text: str,
    previous: str | None,
    developer_notes: list[str],
) -> tuple[str, list[dict[str, Any]], str]:
    calls: list[dict[str, Any]] = []
    toolbox.user_words.add(user_text)
    request_input: list[dict[str, Any]] = []
    if developer_notes:
        request_input.append({"role": "developer", "content": ORDER_UPDATES_HEADER + "\n" + "\n".join(developer_notes)})
    request_input.append({"role": "user", "content": user_text})
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
            try:
                arguments: Any = json.loads(item.arguments)
            except json.JSONDecodeError:
                arguments = item.arguments
            calls.append(
                {
                    "name": item.name,
                    "arguments": arguments,
                    "status": outcome.model_output.get("status"),
                    "model_output": outcome.model_output,
                    "ui_event_type": (outcome.ui_event or {}).get("type"),
                    "ui_prefill": (outcome.ui_event or {}).get("prefill"),
                }
            )
            request_input.append(
                {"type": "function_call_output", "call_id": item.call_id, "output": json.dumps(outcome.model_output, ensure_ascii=False)}
            )
    raise RuntimeError(f"Model kept calling tools for more than {MAX_TOOL_ROUNDS} rounds.")


def _claims(pattern: re.Pattern[str], reply: str) -> list[str]:
    sentences = re.split(r"(?<=[.?!।])\s+", reply)
    return [s for s in sentences if pattern.search(s) and not NEGATION.search(s)]


def check_failure_turn(expect: dict[str, Any], reply: str, calls: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if expect.get("no_not_found_claim") and NOT_FOUND_PATTERN.search(reply):
        failures.append(f"Reported a connection problem as not found/unavailable: {reply!r}.")
    if expect.get("no_paid_claim") and _claims(PAID_CLAIM_PATTERN, reply):
        failures.append(f"Claims the payment went through: {_claims(PAID_CLAIM_PATTERN, reply)}.")
    if expect.get("no_secret_request") and SECRET_ASK_PATTERN.search(reply) and not NEGATION.search(reply):
        failures.append("Asked for a PIN/OTP/CVV/card number.")
    if expect.get("no_basket_change"):
        changed = [c["name"] for c in calls if c["name"] in {"add_to_basket", "set_basket_quantity"}]
        if changed:
            failures.append(f"Changed the basket unprompted after a failure: {changed}.")
    for name in expect.get("max_one_call", []):
        count = sum(1 for c in calls if c["name"] == name)
        if count > 1:
            failures.append(f"{name} was retried {count} times in one turn.")
    return failures


async def run_scenario(client: AsyncOpenAI, delegation: dict[str, Any], scenario: dict[str, Any], catalogues: dict[str, Any]) -> dict[str, Any]:
    toolbox = build_toolbox("demo", catalogues, scenario)
    if scenario.get("seed"):
        await seed_basket(toolbox, scenario["seed"]["query"], scenario["seed"]["quantity"])
    if scenario.get("seed_card"):
        # The card the model would prepare on "order kar do" (prepare_order itself is
        # covered by run_voice_delegation); this scenario is about what comes after the tap.
        prepared = await toolbox.execute("prepare_order", json.dumps({"store": "zepto"}))
        assert prepared.model_output["status"] == "awaiting_tap", prepared.model_output
    install_faults(toolbox, scenario.get("search_faults", {}))
    if scenario.get("bill_fault"):
        toolbox.basket.approvals.providers = lambda name: FailingBill()  # type: ignore[assignment]
    previous: str | None = None
    session_calls: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    order_calls = 0
    for turn in scenario["turns"]:
        notes: list[str] = []
        if turn.get("before") == "tap_unknown":
            gateway = await tap_with_unknown_outcome(toolbox)
            order_calls += gateway.calls
            notes = toolbox.order.take_updates()
        elif turn.get("before") == "payment_unknown":
            notes = [OrderResult("conf_qa", "payment_unknown", "Zepto", 23500, payment_method="upi_reserve", checkout_id="chk_qa").assistant_note()]
        try:
            reply, calls, previous = await run_turn(client, delegation, toolbox, turn["user"], previous, notes)
        except Exception as exc:  # noqa: BLE001 - recorded in the report
            turns.append({"user": turn["user"], "error": f"{type(exc).__name__}: {exc}", "failures": ["turn errored"]})
            break
        session_calls.extend(calls)
        expect = turn.get("expect", {})
        facts, failures = check_turn(expect, reply, calls, session_calls)
        failures += check_failure_turn(expect, reply, calls)
        turns.append({"user": turn["user"], "developer_notes": notes, "reply": reply, "tool_calls": calls, "facts": facts, "failures": failures})
    if order_calls > 1:
        turns[-1]["failures"].append(f"The order call was made {order_calls} times.")
    return {
        "id": scenario["id"],
        "row": scenario["row"],
        "title": scenario["title"],
        "passed": all(not t["failures"] for t in turns) and len(turns) == len(scenario["turns"]),
        "turns": turns,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", help="Scenario id to run (repeatable).")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not configured.")
    guard = NetworkGuard(urlparse(settings.openai_base_url).hostname or "api.openai.com")
    guard.install()
    delegation = build_session_config(settings, VOICE_TOOL_DEFINITIONS)["delegation"]["responses"]
    catalogues = json.loads(DELEGATION_SCENARIOS.read_text(encoding="utf-8"))["catalogues"]
    data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = [s for s in data["scenarios"] if not args.scenario or s["id"] in args.scenario]
    client = AsyncOpenAI(api_key=api_key, base_url=settings.openai_base_url)

    previous_report: dict[str, Any] = json.loads(args.report.read_text(encoding="utf-8")) if args.report.exists() else {}
    runs: list[dict[str, Any]] = previous_report.get("runs", [])
    for scenario in scenarios:
        result = await run_scenario(client, delegation, scenario, catalogues)
        result["run"] = 1 + sum(1 for run in runs if run["id"] == scenario["id"])
        result["finished_at"] = datetime.now(UTC).isoformat()
        runs.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'} {scenario['id']} ({scenario['row']}, run {result['run']})")
        for index, turn in enumerate(result["turns"], 1):
            for failure in turn["failures"]:
                print(f"    turn {index}: {failure}")
    latest = {run["id"]: run for run in runs}
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": delegation["model"],
        "prompt_version": VOICE_PROMPT_VERSION,
        "network": {"allowed_host": guard.allowed_host, "blocked_lookups": sorted(set(guard.blocked))},
        "summary": {sid: {"row": run["row"], "passed": run["passed"], "run": run["run"]} for sid, run in latest.items()},
        "runs": runs,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
