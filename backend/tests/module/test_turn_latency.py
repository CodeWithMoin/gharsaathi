"""Turn-latency behaviour: store fan-out deadlines, cooldown short-circuit,
typed-chat search overlap, backend reasoning effort and content-free timings."""

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.commerce.swiggy.errors import SwiggyRateLimited
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.swiggy.service import SwiggyService
from app.commerce.zepto.service import ZeptoService
from app.core.config import Settings
from app.voice.service import LiveSessionService, build_session_config
from app.voice.sideband import SessionChannel, SidebandRunner
from app.voice.tools import MultiProviderShoppingToolExecutor, ProviderSearchTarget, ToolOutcome


def product(product_id: str) -> Product:
    return Product(
        product_id=product_id, sku_id=product_id, spin_id=product_id, name=f"Atta {product_id}",
        pack_size="5 kg", price=Price(mrp=300, offer_price=265), available=True,
    )


class Sleepy:
    def __init__(self, seconds: float, prefix: str) -> None:
        self.seconds = seconds
        self.prefix = prefix
        self.finished = 0

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        await asyncio.sleep(self.seconds)
        self.finished += 1
        return ProductSearchResult(products=[product(f"{self.prefix}1")])


def executor(swiggy: Sleepy, zepto: Sleepy, soft: float = 0.05, hard: float = 1.0) -> MultiProviderShoppingToolExecutor:
    return MultiProviderShoppingToolExecutor(
        [
            ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", swiggy, "a", True),
            ProviderSearchTarget("zepto", "Zepto", zepto, "z", True),
        ],
        provider_timeout=soft,
        provider_hard_timeout=hard,
    )


@pytest.mark.asyncio
async def test_a_slow_store_does_not_hold_back_the_fast_one() -> None:
    swiggy, zepto = Sleepy(0.0, "s"), Sleepy(0.5, "z")
    started = time.perf_counter()
    outcome = await executor(swiggy, zepto).execute("search_products", '{"query": "atta"}')
    assert time.perf_counter() - started < 0.3
    output = outcome.model_output
    assert output["status"] == "ok" and output["stores_checked"] == ["Swiggy Instamart"]
    assert {"status": "store_unavailable", "reason": "timeout", "store": "Zepto"} in output["provider_statuses"]
    assert "store_unavailable" in output["note"]
    # The slow search keeps running in the background (so a provider cache fills).
    await asyncio.sleep(0.6)
    assert zepto.finished == 1


@pytest.mark.asyncio
async def test_with_no_answer_yet_the_search_waits_past_the_soft_deadline() -> None:
    outcome = await executor(Sleepy(0.2, "s"), Sleepy(0.25, "z")).execute("search_products", '{"query": "atta"}')
    output = outcome.model_output
    assert output["stores_checked"] == ["Swiggy Instamart"]  # first answer at 0.2 s; Zepto then past the deadline
    outcome = await executor(Sleepy(0.1, "s"), Sleepy(0.12, "z"), soft=0.2).execute("search_products", '{"query": "atta"}')
    assert outcome.model_output["stores_checked"] == ["Swiggy Instamart", "Zepto"]
    assert "provider_statuses" not in outcome.model_output


@pytest.mark.asyncio
async def test_healthy_search_payload_omits_redundant_fields() -> None:
    outcome = await executor(Sleepy(0, "s"), Sleepy(0, "z"), soft=1).execute("search_products", '{"query": "atta"}')
    first = outcome.model_output["results"][0]
    assert not {"add_supported", "mrp_paise", "saving_paise"} & set(first)
    assert (first["price_paise"], first["price"], first["mrp"], first["saving"]) == (26500, "₹265", "₹300", "₹35")
    assert "total_variants" not in outcome.model_output


class CoolingClient:
    def __init__(self) -> None:
        self.cooling = False

    async def cooldown_remaining(self) -> int:
        return 30 if self.cooling else 0


class SlowAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def search_products(self, query: str, address_id: str, offset: int = 0) -> ProductSearchResult:
        self.calls += 1
        await asyncio.sleep(0.3)
        return ProductSearchResult(products=[product("s1")])


@pytest.mark.asyncio
async def test_swiggy_cooldown_answers_before_waiting_for_the_provider_lock() -> None:
    client, adapter = CoolingClient(), SlowAdapter()
    service = SwiggyService(None, client, adapter)  # type: ignore[arg-type]
    first = asyncio.create_task(service.search("atta", "addr", 0))
    await asyncio.sleep(0.01)
    client.cooling = True
    started = time.perf_counter()
    with pytest.raises(SwiggyRateLimited) as raised:
        await service.search("dahi", "addr", 0)
    assert time.perf_counter() - started < 0.1
    assert raised.value.retry_after_seconds == 30
    await first
    # A cached result is still served during the cooldown, with no provider call.
    assert (await service.search("atta", "addr", 0)).products[0].product_id == "s1"
    assert adapter.calls == 1


class FakeZeptoClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.session_generation = 1

    async def prepare_session(self) -> int:
        self.calls.append("prepare")
        return self.session_generation

    async def call_tool(self, name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.calls.append(name)
        return {"content": []}


@pytest.mark.asyncio
async def test_zepto_prewarm_selects_the_address_once() -> None:
    client = FakeZeptoClient()
    service = ZeptoService(None, client)  # type: ignore[arg-type]
    await service.prewarm("addr")
    await service.prewarm("addr")
    assert client.calls == ["prepare", "select_saved_address", "prepare"]


class PrewarmingZepto:
    def __init__(self) -> None:
        self.addresses: list[str] = []

    async def prewarm(self, address_id: str) -> None:
        self.addresses.append(address_id)

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(products=[])


@pytest.mark.asyncio
async def test_voice_start_prewarms_zepto_only_with_an_address() -> None:
    zepto = PrewarmingZepto()
    service = LiveSessionService(Settings(openai_api_key=""), Sleepy(0, "s"), None, zepto_searcher=zepto)  # type: ignore[arg-type]
    service._prewarm_zepto(None)
    service._prewarm_zepto("zepto-home")
    await asyncio.gather(*service._prewarms)
    assert zepto.addresses == ["zepto-home"]


def test_backend_reasoning_effort_is_a_setting() -> None:
    low = build_session_config(Settings(live_backend_reasoning_effort="low"))["delegation"]["responses"]
    assert low["reasoning"] == {"effort": "low"}
    unset = build_session_config(Settings(live_backend_reasoning_effort=""))["delegation"]["responses"]
    assert "reasoning" not in unset
    assert Settings.model_fields["live_backend_reasoning_effort"].default == "low"
    tier = build_session_config(Settings(live_backend_service_tier="priority"))["delegation"]["responses"]
    assert tier["service_tier"] == "priority"
    assert "service_tier" not in low


class TwoSearchResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            calls = [
                SimpleNamespace(type="function_call", name="search_products", arguments=json.dumps({"query": q}), call_id=q)
                for q in ("atta", "dahi")
            ]
            return SimpleNamespace(id="r1", output=calls, output_text="")
        return SimpleNamespace(id="r2", output=[], output_text="Options screen par hain.")


@pytest.mark.asyncio
async def test_typed_chat_runs_a_rounds_searches_together_and_keeps_call_order() -> None:
    from app.chat.service import ChatTurnService
    from app.shopping.approval import ApprovalService
    from app.shopping.basket import BasketService
    from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
    from app.voice.sideband import SidebandHub

    settings = Settings(openai_api_key="", live_backend_reasoning_effort="low")
    baskets, approvals = InMemoryBasketRepository(), InMemoryApprovalRepository()
    voice = LiveSessionService(
        settings, Sleepy(0.2, "s"), SidebandHub(settings),
        baskets=BasketService(baskets, approvals),
        approvals=ApprovalService(baskets, approvals, lambda name: None),  # type: ignore[arg-type,return-value]
    )
    responses = TwoSearchResponses()
    started = time.perf_counter()
    result = await ChatTurnService(settings, voice, responses=responses).run_turn("u1", None, "atta aur dahi", "addr")
    assert time.perf_counter() - started < 0.35  # two 0.2 s searches overlapped
    assert [event["item_key"] for event in result.ui_events] == ["atta", "dahi"]
    assert [item["call_id"] for item in responses.requests[1]["input"]] == ["atta", "dahi"]
    assert all(request["reasoning"] == {"effort": "low"} for request in responses.requests)


class FakeConnection:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self.events:
            yield json.dumps(event)
            for _ in range(10):
                await asyncio.sleep(0)
        yield json.dumps({"type": "session.closed"})


class SecretExecutor:
    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        return ToolOutcome(model_output={"status": "ok", "secret": "SECRET-RESULT"})


@pytest.mark.asyncio
async def test_sideband_stage_timings_are_logged_without_content(caplog: pytest.LogCaptureFixture) -> None:
    def event(event_id: str, inner: dict[str, Any]) -> dict[str, Any]:
        return {"type": "response.event", "event_id": event_id, "delegation_id": "d1", "event": inner}

    call = {"type": "function_call", "call_id": "c1", "name": "search_products", "arguments": '{"query": "SECRET-QUERY"}'}
    events = [
        event("e1", {"type": "response.created"}),
        event("e2", {"type": "response.output_item.done", "item": call}),
        event("e3", {"type": "response.completed"}),
        event("e4", {"type": "response.output_text.delta", "delta": "SECRET-REPLY"}),
        event("e5", {"type": "response.completed"}),
    ]
    connection = FakeConnection(events)
    with caplog.at_level(logging.WARNING):
        await SidebandRunner(connection, SecretExecutor(), SessionChannel().publish, log_event_types=True).run()  # type: ignore[arg-type]
    lines = [record.getMessage() for record in caplog.records]
    tool = [line for line in lines if line.startswith("event=voice_tool_timing")]
    turn = [line for line in lines if line.startswith("event=voice_delegation_timing")]
    assert tool and "name=search_products" in tool[0] and "total_ms=" in tool[0]
    assert turn and "rounds=2" in turn[0]
    for key in ("first_call_ms=", "tool_wait_ms=", "continuation_first_event_ms=", "first_text_ms=", "total_ms="):
        assert key in turn[0]
    assert not any("SECRET" in line for line in lines)
