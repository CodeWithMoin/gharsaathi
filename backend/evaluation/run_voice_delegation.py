"""Scripted multi-turn QA of the delegated voice backend brain.

Drives the real backend model (``settings.live_backend_model``) through the
OpenAI Responses API with the exact delegation instructions and voice tool
definitions, and executes every tool call through ``VoiceToolbox`` backed by
the static demo providers and in-memory repositories. No Swiggy or Zepto
network call is possible: DNS lookups are restricted to the OpenAI API host.

Usage (from backend/):
    uv run --with-requirements requirements.txt python -m evaluation.run_voice_delegation \
        [--scenario ID ...] [--report PATH]
"""

import argparse
import asyncio
import json
import re
import socket
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import AsyncOpenAI

from app.commerce.demo import (
    DEMO_ADDRESS_ID,
    DemoQuoteProvider,
    DemoSwiggyService,
    DemoZeptoQuoteProvider,
    DemoZeptoService,
)
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.core.config import get_settings
from app.memory.recall import (
    HouseholdMemory,
    HouseholdRecall,
    PreferenceCandidate,
    RecallConsent,
    RememberedItem,
    item_terms,
    term_matcher,
)
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.order_confirmation import OrderConfirmations
from app.shopping.models import Basket, Charge, OfferOpportunity, Quote
from app.memory.remembered import memory_summary_block
from app.voice.prompts import BACKEND_INSTRUCTIONS, VOICE_PROMPT_VERSION
from app.voice.service import build_session_config
from app.voice.memory_write_tools import MEMORY_WRITE_TOOL_NAMES
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from app.voice.tools import ProductSearcher
from app.voice.ui_ack import UiAck, apply_ack
from app.commerce.swiggy.models import Address, AddressList, AddressResolution, Pagination
from app.commerce.zepto.models import ConnectionStatus as ZeptoConnectionStatus
from app.tracking.models import StartTrackingRequest, TrackingProvider, TrackingSnapshot, TrackingStatus
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore
from evaluation.memory_db import ScenarioMemoryDatabase
from evaluation.run_realtime_voice import MASCULINE_SELF_FORMS


SCENARIOS_PATH = Path(__file__).with_name("voice_delegation_scenarios.json")
DEFAULT_REPORT_PATH = Path(__file__).with_name("voice_delegation_report.json")
HOUSEHOLD_ID = "qa_voice_delegation"
MAX_TOOL_ROUNDS = 8

MASCULINE_PATTERN = re.compile(
    r"\b(?:raha|gaya|sakta|deta|leta|karta|dunga|lunga)\s+hoon\b|रहा हूँ|गया हूँ|सकता हूँ|करता हूँ|दूँगा|करूँगा",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(
    r"(?:₹|rs\.?\s*|inr\s*)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:rupaye|rupees|rupee|rupay|रुपये|रुपए)",
    re.IGNORECASE,
)
MINUTES_PATTERN = re.compile(r"(\d+)\s*(?:[-–]\s*\d+\s*)?(?:min|minute|minat|मिनट)", re.IGNORECASE)
ADD_CLAIM_PATTERN = re.compile(
    r"add (?:ho gaya|ho gayi|ho gaye|kar diya|kar di|kar diye)|added|daal diya|daal di|जोड़ दिया|ऐड हो गया|add हो गया",
    re.IGNORECASE,
)
ORDER_CLAIM_PATTERN = re.compile(
    r"order (?:place )?(?:ho gaya|kar diya|placed|has been placed|confirm ho gaya)|approve (?:kar diya|ho gaya)|\bapproved\b|ऑर्डर (?:हो गया|कर दिया)",
    re.IGNORECASE,
)
ORDER_REFUSAL_PATTERN = re.compile(
    r"(?:place|order|approve) nahi kar sakti|nahi kar sakti[^.?!।]*order|cannot place|can't place|"
    r"can not place|order place nahi",
    re.IGNORECASE,
)
NEGATION_PATTERN = re.compile(r"\bnahi\b|\bnahin\b|\bnot\b|\bcan't\b|\bcannot\b|नहीं|\bnhi\b", re.IGNORECASE)
FORM_OPEN_CLAIM_PATTERN = re.compile(
    r"(?:form|फ़ॉर्म|फॉर्म)[^.?!]*(?:khul gaya|khula hai|screen par hai|open hai|is open|khul chuka|खुल गया)",
    re.IGNORECASE,
)
PHONE_FILL_CLAIM_PATTERN = re.compile(r"number[^.?!]*(?:bhar diya|daal diya|fill kar diya|filled)", re.IGNORECASE)
SAVED_CLAIM_PATTERN = re.compile(
    r"(?:address )?(?:save ho gaya|save kar diya|saved|add ho gaya|add kar diya)|सेव हो गया", re.IGNORECASE
)
DELIVERY_UNKNOWN_PATTERN = re.compile(
    r"(?:delivery|time|samay|टाइम|डिलीवरी)[^.?!।]*(?:nahi|nahin|not|abhi|pata|नहीं)|"
    r"(?:nahi|not|नहीं)[^.?!।]*(?:delivery|time|डिलीवरी)",
    re.IGNORECASE,
)
DELIVERY_WORD_PATTERN = re.compile(r"delivery|time|samay|minute|min\b|डिलीवरी|मिनट|टाइम", re.IGNORECASE)
SENTENCE_SPLIT = re.compile(r"(?<=[.?!।])\s+")




@dataclass
class NetworkGuard:
    """Allow DNS only for the OpenAI API host; record and block everything else."""

    allowed_host: str
    lookups: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    def install(self) -> None:
        original = socket.getaddrinfo

        def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
            name = host.decode() if isinstance(host, bytes) else str(host)
            self.lookups.append(name)
            if name != self.allowed_host:
                self.blocked.append(name)
                raise OSError(f"QA harness blocks network access to {name}")
            return original(host, *args, **kwargs)

        setattr(socket, "getaddrinfo", guarded)




def _fixture_product(entry: dict[str, Any], prefix: str) -> Product:
    return Product(
        product_id=entry["product_id"],
        sku_id=f"{prefix}-sku-{entry['product_id']}",
        spin_id=f"{prefix}-spin-{entry['product_id']}",
        name=entry["name"],
        brand=entry["brand"],
        pack_size=entry["pack_size"],
        price=Price(mrp=entry["mrp"], offer_price=entry["offer_price"]),
        available=True,
    )


class FixtureOverlaySearcher:
    """Harness-only products searched first; anything else falls through to the demo store."""

    def __init__(self, base: ProductSearcher, products: list[Product]) -> None:
        self.base = base
        self.products = products

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


STORE_LABELS = {"swiggy_instamart": "Swiggy Instamart", "zepto": "Zepto"}


class ScenarioMemory:
    """Harness-only consented household memory, read the same way as the database reader."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.memory = HouseholdMemory(
            consent=RecallConsent(saved_preferences="on", purchase_history="on"),
            items=[RememberedItem.model_validate(item) for item in items],
        )

    async def household_memory(self, household_id: str) -> HouseholdMemory:
        return self.memory

    async def recall(self, household_id: str, item: str) -> HouseholdRecall:
        matches = term_matcher(item_terms(item))
        candidates = [
            PreferenceCandidate(
                source="purchase_history",
                product_name=remembered.name,
                store=STORE_LABELS.get(remembered.store_id or ""),
                last_purchased_on=remembered.last_bought,
                purchase_count=remembered.times,
            )
            if remembered.source == "purchase_history"
            else PreferenceCandidate(
                source="confirmed_preference",
                preference=remembered.preference,
                brand=remembered.brand,
                pack_size=remembered.pack_size,
                summary=remembered.name,
            )
            for remembered in self.memory.items
            if matches(remembered.name)
        ][:4]
        return HouseholdRecall(consent=self.memory.consent, candidates=candidates)


class OfferQuoteProvider(DemoZeptoQuoteProvider):
    """Demo Zepto bill with Zepto-shaped offers: free delivery and a promo applied, plus an
    unconfirmed bank card that the voice tools must not report."""

    async def quote(self, basket: Basket) -> Quote:
        base = await super().quote(basket)
        discount = min(2000, base.item_total_paise // 10)
        charges = [
            Charge(label="Delivery fee", amount_paise=0),
            Charge(label="Packaging fee", amount_paise=500),
            Charge(label="Discount", amount_paise=-discount),
        ]
        return base.model_copy(
            update={
                "charges": charges,
                "payable_paise": base.item_total_paise + sum(c.amount_paise for c in charges),
                "offers": [
                    OfferOpportunity(title="Free Delivery", kind="free_delivery", minimum_order_paise=9900,
                                     remaining_paise=0, unlocked=True, applied=True, provider_confirmed=True),
                    OfferOpportunity(title="Welcome Saver", kind="promo", minimum_order_paise=0,
                                     remaining_paise=0, unlocked=True, applied=True, provider_confirmed=True),
                    OfferOpportunity(title="HDFC card 10% off", kind="bank_offer", minimum_order_paise=0,
                                     remaining_paise=0, unlocked=True, applied=False, provider_confirmed=False),
                ],
            }
        )


class HomeOfficeAddressBook:
    """Two saved addresses per store, in memory; no provider call."""

    def __init__(self) -> None:
        self.selected = {"swiggy_instamart": DEMO_ADDRESS_ID, "zepto": DEMO_ADDRESS_ID}

    async def saved(self, household_id: str, provider: str) -> AddressList:
        return AddressList(
            addresses=[
                Address(id=DEMO_ADDRESS_ID, tag="Home", category="HOME", address_line="12 Sample Street, HSR Layout, Bengaluru"),
                Address(id="demo-office", tag="Office", category="WORK", address_line="Tower B, Outer Ring Road, Bengaluru"),
            ],
            pagination=Pagination(page=1, page_size=10, total=2, total_pages=1),
            resolution=AddressResolution(default_address_id=self.selected[provider], resolution_source="saved_selection"),
        )

    async def select(self, household_id: str, provider: str, address_id: str) -> AddressList | None:
        addresses = await self.saved(household_id, provider)
        if address_id not in {item.id for item in addresses.addresses}:
            return None
        self.selected[provider] = address_id
        addresses.resolution.default_address_id = address_id
        return addresses


class FakeTrackingAdapter:
    """Scripted read-only order status for one scenario; no provider call."""

    provider = TrackingProvider.ZEPTO

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec

    async def fetch_delivery_status(self, *, order_id: str, address_id: str | None) -> TrackingSnapshot:
        status = TrackingStatus(self.spec.get("status", "out_for_delivery"))
        eta = self.spec.get("eta_minutes")
        return TrackingSnapshot(
            status=status,
            provider_status=self.spec.get("provider_status"),
            eta_at=datetime.now(UTC) + timedelta(minutes=eta) if eta is not None else None,
            poll_interval_seconds=600,
            delivered=status == TrackingStatus.DELIVERED,
            cancelled=status == TrackingStatus.CANCELLED,
            order_code=self.spec.get("order_code"),
        )


async def scenario_tracking(spec: dict[str, Any] | None) -> OrderTrackingService:
    """A tracking service on a throwaway directory, with the scenario's placed order watched."""

    service = OrderTrackingService(
        OrderTrackingStore(Path(tempfile.mkdtemp(prefix="qa-tracking-"))),
        {TrackingProvider.ZEPTO: FakeTrackingAdapter(spec or {})},
    )
    if spec:
        await service.ensure(
            HOUSEHOLD_ID,
            StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id=spec.get("order_id", "ZEP-QA-1")),
        )
    return service


class LiveZeptoConnection:
    """A connected live Zepto for the address form only; nothing else is reachable."""

    async def connection_status(self) -> ZeptoConnectionStatus:
        return ZeptoConnectionStatus(connected=True, mode="live")


def build_toolbox(
    catalogue: str,
    catalogues: dict[str, Any],
    scenario: dict[str, Any] | None = None,
    memory_db: ScenarioMemoryDatabase | None = None,
    order_tracking: OrderTrackingService | None = None,
) -> VoiceToolbox:
    scenario = scenario or {}
    swiggy: ProductSearcher = DemoSwiggyService()
    zepto: ProductSearcher = DemoZeptoService()
    if catalogue != "demo":
        overlay = catalogues[catalogue]
        swiggy = FixtureOverlaySearcher(
            swiggy, [_fixture_product(item, "swiggy") for item in overlay["swiggy_instamart"]]
        )
        zepto = FixtureOverlaySearcher(
            zepto, [_fixture_product(item, "zepto") for item in overlay["zepto"]]
        )
    basket_repo = InMemoryBasketRepository()
    approval_repo = InMemoryApprovalRepository()
    quotes = {
        "swiggy_instamart": DemoQuoteProvider(),
        "zepto": OfferQuoteProvider() if scenario.get("quote_offers") else DemoZeptoQuoteProvider(),
    }
    memory: Any = ScenarioMemory(scenario["memory"]) if scenario.get("memory") else None
    if memory_db is not None:
        memory = memory_db.recaller
    approvals = ApprovalService(basket_repo, approval_repo, lambda name: quotes[name])
    # prepare_order only binds a card; the harness never taps it and has no
    # checkout gateway, so no order can be placed.
    return VoiceToolbox(
        swiggy,
        BasketService(basket_repo, approval_repo),
        approvals,
        DEMO_ADDRESS_ID,
        zepto,
        DEMO_ADDRESS_ID,
        HOUSEHOLD_ID,
        zepto_connection=LiveZeptoConnection() if scenario.get("address_form_screen") else DemoZeptoService(),
        preference_recall=memory,
        order_confirmations=OrderConfirmations(basket_repo, approvals),
        address_book=HomeOfficeAddressBook() if scenario.get("address_book") == "home_office" else None,
        # Demo Zepto history is empty and its catalogue has no tools: nothing leaves the process.
        order_tracking=order_tracking,
        order_history=DemoZeptoService(),
    )




async def run_turn(
    client: AsyncOpenAI,
    delegation: dict[str, Any],
    toolbox: VoiceToolbox,
    user_text: str,
    previous_response_id: str | None,
    screen: str | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    calls: list[dict[str, Any]] = []
    toolbox.user_words.add(user_text)
    request_input: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
    previous = previous_response_id
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
            ack_id = (outcome.ui_event or {}).get("ack_id")
            if ack_id and screen:
                # The simulated browser: the form shows (or fails), as page.tsx reports it.
                prefill = (outcome.ui_event or {}).get("prefill") or {}
                ack = UiAck(ack_id=ack_id, form_visible=screen == "visible", reason="shown" if screen == "visible" else "render_failed",
                            filled_fields=sorted(prefill) if screen == "visible" else [])
                outcome.model_output = apply_ack(outcome.model_output, ack)
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
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(outcome.model_output, ensure_ascii=False),
                }
            )
    raise RuntimeError(f"Model kept calling tools for more than {MAX_TOOL_ROUNDS} rounds.")




def _numbers(value: Any) -> set[float]:
    found: set[float] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.add(float(value))
    elif isinstance(value, str):
        found.update(float(match) for match in re.findall(r"\d+(?:\.\d+)?", value.replace(",", "")))
    elif isinstance(value, dict):
        for item in value.values():
            found |= _numbers(item)
    elif isinstance(value, list):
        for item in value:
            found |= _numbers(item)
    return found


def _eta_minutes(value: Any) -> set[int]:
    text = json.dumps(value, ensure_ascii=False)
    pattern = r'"(?:delivery_eta|eta_minutes|last_updated_minutes_ago)": "?(\d+)'
    return {int(match) for match in re.findall(pattern, text)}


def _size_value(pack_size: str | None) -> float:
    if not pack_size:
        return float("inf")
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(ml|l|g|kg)?", pack_size.lower())
    if not match:
        return float("inf")
    number = float(match.group(1))
    unit = match.group(2) or ""
    return number * 1000 if unit in {"l", "kg"} else number


def _search_options(session_calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    options: dict[str, dict[str, Any]] = {}
    for call in session_calls:
        if call["name"] == "search_products" and call["status"] == "ok":
            for result in call["model_output"]["results"]:
                if "option_id" in result:
                    options[result["option_id"]] = result
    return options


def _script(text: str) -> dict[str, Any]:
    letters = [char for char in text if char.isalpha()]
    devanagari = sum(1 for char in letters if "\u0900" <= char <= "\u097f")
    latin = sum(1 for char in letters if char.isascii())
    ratio = devanagari / len(letters) if letters else 0.0
    dominant = "devanagari" if ratio >= 0.6 else "latin" if ratio <= 0.1 else "mixed"
    return {"devanagari_ratio": round(ratio, 3), "latin_letters": latin, "dominant": dominant}


def _claim_sentences(pattern: re.Pattern[str], reply: str) -> list[str]:
    return [
        sentence
        for sentence in SENTENCE_SPLIT.split(reply)
        if pattern.search(sentence) and not NEGATION_PATTERN.search(sentence)
    ]


def check_turn(
    expect: dict[str, Any],
    reply: str,
    calls: list[dict[str, Any]],
    session_calls: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    names = [call["name"] for call in calls]
    adds = [call for call in calls if call["name"] == "add_to_basket"]
    successful_adds = [call for call in adds if call["status"] == "added"]
    changed = [call for call in calls if call["status"] in {"added", "updated"}]
    approvals = [call for call in calls if call["status"] == "awaiting_user_approval"]
    sentences = [sentence for sentence in SENTENCE_SPLIT.split(reply.strip()) if sentence]
    questions = reply.count("?") + reply.count("？")
    tool_numbers = _numbers([call["model_output"] for call in session_calls])
    reply_prices = {
        float(a or b) for a, b in PRICE_PATTERN.findall(reply.replace(",", ""))
    }
    reply_minutes = {int(value) for value in MINUTES_PATTERN.findall(reply)}
    tool_minutes = _eta_minutes([call["model_output"] for call in session_calls])
    masculine = [form for form in MASCULINE_SELF_FORMS if form.lower() in reply.lower()]
    masculine += [match.group(0) for match in MASCULINE_PATTERN.finditer(reply)]
    facts: dict[str, Any] = {
        "tool_names": names,
        "question_count": questions,
        "sentence_count": len(sentences),
        "masculine_self_forms": sorted(set(masculine)),
        "reply_prices": sorted(reply_prices),
        "invented_prices": sorted(price for price in reply_prices if price not in tool_numbers),
        "reply_eta_minutes": sorted(reply_minutes),
        "invented_eta_minutes": sorted(value for value in reply_minutes if value not in tool_minutes),
        "unconfirmed_add_claim": bool(ADD_CLAIM_PATTERN.search(reply)) and not changed,
        "order_claims": _claim_sentences(ORDER_CLAIM_PATTERN, reply),
        "script": _script(reply),
    }

    if questions > expect.get("max_questions", 1):
        failures.append(f"{questions} questions in one turn (max {expect.get('max_questions', 1)}).")
    if facts["masculine_self_forms"]:
        failures.append(f"Masculine self-reference: {facts['masculine_self_forms']}.")
    if facts["invented_prices"]:
        failures.append(f"Prices not returned by any tool: {facts['invented_prices']}.")
    if facts["invented_eta_minutes"]:
        failures.append(f"Delivery minutes not returned by any tool: {facts['invented_eta_minutes']}.")
    if facts["unconfirmed_add_claim"]:
        failures.append("Reply claims an add/change but no basket tool confirmed one this turn.")
    if facts["order_claims"]:
        failures.append(f"Reply claims an order/approval: {facts['order_claims']}.")

    for name in expect.get("tools_required", []):
        if name not in names:
            failures.append(f"Expected tool {name} was not called (called: {names}).")
    if "add_count" in expect and len(adds) != expect["add_count"]:
        failures.append(f"Expected {expect['add_count']} add_to_basket call(s), got {len(adds)}.")
    if adds and len(successful_adds) != len(adds):
        failures.append(f"add_to_basket failed: {[call['model_output'] for call in adds]}.")
    options = _search_options(session_calls)
    for call in successful_adds:
        option = options.get(call["arguments"].get("option_id"), {})
        facts.setdefault("added", []).append(
            {
                "option_id": call["arguments"].get("option_id"),
                "quantity": call["arguments"].get("quantity"),
                "name": option.get("name"),
                "pack_size": option.get("pack_size"),
                "store_id": option.get("store_id"),
                "position": option.get("position"),
                "price": option.get("price"),
            }
        )
        if "add_store" in expect and option.get("store_id") != expect["add_store"]:
            failures.append(f"Added to {option.get('store_id')}, expected {expect['add_store']}.")
        if "add_position" in expect and option.get("position") != expect["add_position"]:
            failures.append(f"Added card {option.get('position')}, expected card {expect['add_position']}.")
        needle = expect.get("add_product_contains")
        if needle and needle.lower() not in str(option.get("name", "")).lower():
            failures.append(f"Added {option.get('name')!r}, expected a product containing {needle!r}.")
        if expect.get("add_smallest") and needle:
            candidates = [
                candidate for candidate in options.values() if needle.lower() in candidate["name"].lower()
            ]
            smallest = min(_size_value(candidate.get("pack_size")) for candidate in candidates)
            if _size_value(option.get("pack_size")) != smallest:
                failures.append(
                    f"Added {option.get('pack_size')}, but the smallest {needle} option seen was {smallest:g}."
                )
    if "max_sentences" in expect and len(sentences) > expect["max_sentences"]:
        failures.append(f"{len(sentences)} sentences, expected at most {expect['max_sentences']}.")
    repeated = [word for word in expect.get("no_option_list", []) if word.lower() in reply.lower()]
    if repeated:
        failures.append(f"Option list repeated after selection (mentions {repeated}).")
    if expect.get("delivery_mentioned") and not DELIVERY_WORD_PATTERN.search(reply):
        failures.append("Delivery time question was not answered.")
    if expect.get("delivery_unknown_stated") and not DELIVERY_UNKNOWN_PATTERN.search(reply):
        failures.append("Did not say that delivery time is not shown.")
    if expect.get("delivery_not_repeated") and DELIVERY_UNKNOWN_PATTERN.search(reply):
        failures.append("Delivery-unknown disclaimer repeated after it was already stated.")
    if "quantity_set" in expect:
        wanted = expect["quantity_set"]
        sets = [call for call in calls if call["name"] == "set_basket_quantity" and call["status"] == "updated"]
        if not any(
            call["model_output"].get("store_id") == wanted["store_id"]
            and any(item["quantity"] == wanted["quantity"] for item in call["model_output"].get("items", []))
            for call in sets
        ):
            failures.append(f"No successful quantity change to {wanted} (calls: {[c['model_output'] for c in calls]}).")
    if "approval_store" in expect and not any(
        call["model_output"].get("store_id") == expect["approval_store"] for call in approvals
    ):
        failures.append(f"No {expect['approval_store']} bill was requested ({[c['model_output'] for c in calls]}).")
    if "no_approval_other_than" in expect:
        others = [call for call in approvals if call["model_output"].get("store_id") != expect["no_approval_other_than"]]
        if others:
            failures.append(f"Bill requested for another store: {others}.")
    if expect.get("no_approval_requested") and approvals:
        failures.append(
            f"Guessed a store and requested a bill: {[call['model_output'].get('store_id') for call in approvals]}."
        )
    if expect.get("asks_question") and questions < 1:
        failures.append("Expected a clarifying question.")
    if "address_fields" in expect:
        forms = [call for call in calls if call["name"] == "open_address_form"]
        arguments = forms[0]["arguments"] if forms else {}
        for key, needle in expect["address_fields"].items():
            if needle.lower() not in str(arguments.get(key) or "").lower():
                failures.append(f"open_address_form {key}={arguments.get(key)!r}, expected to contain {needle!r}.")
        for key in expect.get("address_null_fields", []):
            if forms and arguments.get(key):
                failures.append(f"open_address_form invented {key}={arguments.get(key)!r}.")
    if expect.get("address_listed") and "list_delivery_addresses" not in names:
        failures.append("Did not list the saved addresses first.")
    if "address_selected" in expect:
        picked = [call for call in calls if call["name"] == "select_delivery_address" and call["status"] == "selected"]
        if not picked or picked[-1]["arguments"].get("address_id") != expect["address_selected"]:
            failures.append(f"Expected select_delivery_address {expect['address_selected']}, got {[(c['arguments'], c['status']) for c in calls if c['name'] == 'select_delivery_address']}.")
    if "prefill_equals" in expect:
        forms = [call for call in calls if call["name"] == "open_address_form" and call.get("ui_prefill") is not None]
        if not forms or forms[-1]["ui_prefill"] != expect["prefill_equals"]:
            failures.append(f"Form prefill {forms[-1]['ui_prefill'] if forms else None}, expected {expect['prefill_equals']}.")
    if expect.get("form_claim_needs_ack"):
        visible = any(call["status"] == "form_opened" for call in calls)
        claims = _claim_sentences(FORM_OPEN_CLAIM_PATTERN, reply)
        if claims and not visible:
            failures.append(f"Claimed the form is open without form_visible: {claims}.")
    if expect.get("no_phone_fill_claim") and _claim_sentences(PHONE_FILL_CLAIM_PATTERN, reply):
        failures.append(f"Claimed to fill the phone number: {_claim_sentences(PHONE_FILL_CLAIM_PATTERN, reply)}.")
    if expect.get("no_saved_claim") and _claim_sentences(SAVED_CLAIM_PATTERN, reply):
        failures.append(f"Reply claims the address was saved: {_claim_sentences(SAVED_CLAIM_PATTERN, reply)}.")
    if "add_pack_size" in expect:
        sizes = [item.get("pack_size") for item in facts.get("added", [])]
        if sizes != [expect["add_pack_size"]] * len(sizes) or not sizes:
            failures.append(f"Added pack sizes {sizes}, expected {expect['add_pack_size']}.")
    if expect.get("recall_before_search"):
        if "recall_household_preferences" not in names:
            failures.append(f"recall_household_preferences was not called (called: {names}).")
        elif "search_products" in names and names.index("search_products") < names.index(
            "recall_household_preferences"
        ):
            failures.append("search_products ran before recall_household_preferences.")
    queries = [str(call["arguments"].get("query", "")).lower() for call in calls if call["name"] == "search_products" and isinstance(call["arguments"], dict)]
    if "min_searches" in expect and len(queries) < expect["min_searches"]:
        failures.append(f"Expected at least {expect['min_searches']} searches, got {queries}.")
    cancelled = [word for word in expect.get("search_queries_forbid", []) if any(word in query for query in queries)]
    if cancelled:
        failures.append(f"Searched a cancelled item {cancelled} (queries: {queries}).")
    for name in expect.get("tools_forbidden", []):
        if name in names:
            failures.append(f"Tool {name} must not be called this turn (called: {names}).")
    forbidden = [word for word in expect.get("reply_forbids_any", []) if word.lower() in reply.lower()]
    if forbidden:
        failures.append(f"Reply mentions forbidden {forbidden}.")
    if expect.get("roman_only") and facts["script"]["devanagari_ratio"] > 0:
        failures.append("Reply contains Devanagari in a Roman Hinglish turn.")
    mentions = expect.get("reply_mentions_any")
    if mentions and not any(word.lower() in reply.lower() for word in mentions):
        failures.append(f"Reply does not mention any of {mentions}.")
    memory_statuses = [
        call["status"] for call in calls if call["name"] in MEMORY_WRITE_TOOL_NAMES
    ]
    facts["memory_statuses"] = memory_statuses
    wanted = expect.get("memory_status_any_of")
    if wanted and not set(wanted) & set(memory_statuses):
        failures.append(f"Expected a memory tool status in {wanted}, got {memory_statuses}.")
    if expect.get("no_memory_write") and any(
        call["name"] in MEMORY_WRITE_TOOL_NAMES - {"list_preferences"} and call["status"] in {"saved", "needs_confirmation", "confirmed"}
        for call in calls
    ):
        failures.append(f"A memory was saved or proposed for a one-off choice: {memory_statuses}.")
    if "search_stores_only" in expect:
        stores = {
            result.get("store_id")
            for call in calls
            if call["name"] == "search_products" and call["status"] == "ok"
            for result in call["model_output"]["results"]
        }
        if not stores or not stores <= set(expect["search_stores_only"]):
            failures.append(f"Search results came from {sorted(map(str, stores))}, expected only {expect['search_stores_only']}.")
    if expect.get("suggestion_offered") and not any(
        "memory_suggestion" in call["model_output"] for call in calls
    ):
        failures.append("No memory_suggestion was attached to a basket add.")
    if expect.get("prepared_order") and not any(
        call["name"] == "prepare_order" and call["status"] == "awaiting_tap" for call in calls
    ):
        failures.append(f"No order card was prepared (calls: {[c['model_output'] for c in calls]}).")
    if expect.get("no_order_refusal") and ORDER_REFUSAL_PATTERN.search(reply):
        failures.append("Reply refuses to place orders although the order card is the order path.")
    if expect.get("refuses_order"):
        if approvals and facts["order_claims"]:
            failures.append("Order/approval claimed after an approval request.")
        if not NEGATION_PATTERN.search(reply) and "screen" not in reply.lower():
            failures.append("Reply does not refuse or redirect to the on-screen approval.")
    return facts, failures




async def run_scenario(
    client: AsyncOpenAI,
    delegation: dict[str, Any],
    scenario: dict[str, Any],
    catalogues: dict[str, Any],
) -> dict[str, Any]:
    memory_db: ScenarioMemoryDatabase | None = None
    if "memory_db" in scenario:
        memory_db = await ScenarioMemoryDatabase.create(HOUSEHOLD_ID, scenario["memory_db"])
    tracking = await scenario_tracking(scenario.get("tracking"))
    toolbox = build_toolbox(scenario["catalogue"], catalogues, scenario, memory_db, tracking)
    withheld = set(scenario.get("withhold_tools", []))
    if withheld or scenario.get("memory"):
        delegation = {
            **delegation,
            "tools": [tool for tool in delegation["tools"] if tool["name"] not in withheld],
            "instructions": delegation["instructions"]
            + (
                memory_summary_block(toolbox.memory.recaller.memory)  # type: ignore[union-attr]
                if scenario.get("memory") and scenario.get("memory_summary", True)
                else ""
            ),
        }
    if memory_db is not None:
        # Same session-start summary as LiveSessionService, read from the scenario database.
        delegation = {
            **delegation,
            "instructions": delegation["instructions"]
            + memory_summary_block(await memory_db.recaller.household_memory(HOUSEHOLD_ID)),
        }
    previous: str | None = None
    session_calls: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    for turn in scenario["turns"]:
        try:
            reply, calls, previous = await run_turn(
                client, delegation, toolbox, turn["user"], previous, turn.get("screen", scenario.get("address_form_screen"))
            )
        except Exception as exc:
            turns.append({"user": turn["user"], "error": f"{type(exc).__name__}: {exc}", "failures": ["turn errored"]})
            break
        session_calls.extend(calls)
        facts, failures = check_turn(turn.get("expect", {}), reply, calls, session_calls)
        if memory_db is not None:
            state = await memory_db.state()
            facts["memory_state"] = state
            failures += memory_db.check(turn.get("expect", {}), state)
        turns.append({"user": turn["user"], "reply": reply, "tool_calls": calls, "facts": facts, "failures": failures})
    if memory_db is not None:
        await memory_db.close()
    failed = [turn for turn in turns if turn["failures"]]
    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "catalogue": scenario["catalogue"],
        "passed": not failed and len(turns) == len(scenario["turns"]),
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
    assert delegation["instructions"] == BACKEND_INSTRUCTIONS

    data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = [s for s in data["scenarios"] if not args.scenario or s["id"] in args.scenario]
    client = AsyncOpenAI(api_key=api_key, base_url=settings.openai_base_url)

    previous_report: dict[str, Any] = {}
    if args.report.exists():
        previous_report = json.loads(args.report.read_text(encoding="utf-8"))
    runs: list[dict[str, Any]] = previous_report.get("runs", [])
    for scenario in scenarios:
        result = await run_scenario(client, delegation, scenario, data["catalogues"])
        result["run"] = 1 + sum(1 for run in runs if run["id"] == scenario["id"])
        result["finished_at"] = datetime.now(UTC).isoformat()
        runs.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'} {scenario['id']} (run {result['run']})")
        for index, turn in enumerate(result["turns"], 1):
            for failure in turn["failures"]:
                print(f"    turn {index}: {failure}")

    latest: dict[str, dict[str, Any]] = {run["id"]: run for run in runs}
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": delegation["model"],
        "prompt_version": VOICE_PROMPT_VERSION,
        "tool_names": [tool["name"] for tool in delegation["tools"]],
        "network": {
            "allowed_host": guard.allowed_host,
            "blocked_lookups": sorted(set(previous_report.get("network", {}).get("blocked_lookups", [])) | set(guard.blocked)),
        },
        "summary": {
            scenario_id: {"passed": run["passed"], "run": run["run"]}
            for scenario_id, run in latest.items()
        },
        "runs": runs,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
