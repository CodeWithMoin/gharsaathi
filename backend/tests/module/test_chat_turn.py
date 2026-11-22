import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.chat.service import (
    MAX_HISTORY_MESSAGES,
    MAX_TOOL_ROUNDS,
    ChatProviderError,
    ChatTimeout,
    ChatTurnService,
    HistoryMessage,
    merge_history,
)
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.core.config import Settings
from app.user_profile import UserProfile
from app.voice.prompts import BACKEND_INSTRUCTIONS
from app.voice.service import LiveSessionService
from app.voice.sideband import SidebandHub
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS
from tests.shopping_fakes import World

USER = "user_chat_a"
SWIGGY = "swiggy_instamart"


def product(name: str, pack: str, offer: float) -> Product:
    slug = f"{name}-{pack}".lower().replace(" ", "-")
    return Product(
        product_id=f"p-{slug}",
        sku_id=f"sku-{slug}",
        spin_id=f"spin-{slug}",
        name=name,
        brand=name.split()[0],
        pack_size=pack,
        price=Price(mrp=offer + 50, offer_price=offer),
        available=True,
    )


AXE = [product("AXE Dark Temptation", "150 ml", 199), product("AXE Dark Temptation", "215 ml", 299)]


class FakeSearcher:
    def __init__(self, products: list[Product]) -> None:
        self.products = products
        self.queries: list[str] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.queries.append(query)
        return ProductSearchResult(products=self.products)


class FakeProfiles:
    def __init__(self, profile: UserProfile | None = None) -> None:
        self.profile = profile

    async def read(self, user_id: str) -> UserProfile | None:
        return self.profile


def call(name: str, call_id: str, **arguments: Any) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


class FakeResponses:
    """Scripted Responses API: each entry is a list of function calls or a reply string."""

    def __init__(self, script: list[list[SimpleNamespace] | str]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        step = self.script.pop(0) if self.script else "Theek hai."
        response_id = f"resp_{len(self.requests)}"
        if isinstance(step, str):
            return SimpleNamespace(id=response_id, output=[SimpleNamespace(type="message")], output_text=step)
        return SimpleNamespace(id=response_id, output=step, output_text="")

    def outputs(self, request_index: int) -> list[dict[str, Any]]:
        return [json.loads(item["output"]) for item in self.requests[request_index]["input"]]


def settings() -> Settings:
    return Settings(_env_file=None, openai_api_key=SecretStr(""))


def service(
    responses: FakeResponses,
    world: World,
    swiggy: FakeSearcher,
    zepto: FakeSearcher | None = None,
    profile: UserProfile | None = None,
) -> ChatTurnService:
    config = settings()
    voice = LiveSessionService(
        config,
        swiggy,
        SidebandHub(config),
        baskets=world.baskets,
        approvals=world.approvals,
        zepto_searcher=zepto,
        profile_reader=FakeProfiles(profile),
    )
    return ChatTurnService(config, voice, responses=responses)


async def turn(chat: ChatTurnService, message: str, history: list[HistoryMessage] | None = None, **kwargs: Any):
    kwargs.setdefault("swiggy_address_id", "addr_home")
    return await chat.run_turn(USER, None, message, history or [], **kwargs)


async def test_search_then_add_runs_voice_tools_and_returns_voice_ui_events() -> None:
    world, searcher = World(), FakeSearcher(AXE)
    responses = FakeResponses(
        [
            [call("search_products", "c1", query="AXE Dark Temptation deodorant")],
            [call("add_to_basket", "c2", option_id="opt1", quantity=1, store_id=None)],
            "AXE Dark Temptation 150 ml Instamart basket mein add ho gaya.",
        ]
    )
    chat = service(responses, world, searcher)

    result = await turn(chat, "AXE Dark sabse chhota add karo")

    assert result.reply == "AXE Dark Temptation 150 ml Instamart basket mein add ho gaya."
    assert [event["type"] for event in result.ui_events] == ["tool.results", "basket.updated"]
    results, updated = result.ui_events
    assert results["query"] == "AXE Dark Temptation deodorant"
    assert {item["provider"] for item in results["products"]} == {SWIGGY}
    assert updated["provider"] == SWIGGY
    assert updated["basket"]["item_count"] == 1
    basket = await world.baskets.active(USER, SWIGGY)
    assert [(line.pack_size, line.unit_price_paise) for line in basket.lines] == [("150 ml", 19900)]
    first = responses.requests[0]
    assert first["instructions"] == BACKEND_INSTRUCTIONS
    assert first["tools"] == VOICE_TOOL_DEFINITIONS
    assert first["input"][-1] == {"role": "user", "content": "AXE Dark sabse chhota add karo"}
    assert responses.outputs(1)[0]["status"] == "ok"
    assert responses.requests[1]["previous_response_id"] == "resp_1"
    assert responses.outputs(2)[0]["status"] == "added"


async def test_option_ids_from_an_earlier_message_can_be_added_in_the_next() -> None:
    world, searcher = World(), FakeSearcher(AXE)
    responses = FakeResponses(
        [
            [call("search_products", "c1", query="AXE Dark Temptation deodorant")],
            "150 ml ya 215 ml?",
            [call("add_to_basket", "c2", option_id="opt1", quantity=1, store_id=None)],
            "150 ml add ho gaya.",
        ]
    )
    chat = service(responses, world, searcher)
    await turn(chat, "AXE Dark chahiye")

    second = await turn(chat, "sabse chhota")

    assert [event["type"] for event in second.ui_events] == ["basket.updated"]
    context = responses.requests[2]["input"]
    assert context[:2] == [
        {"role": "user", "content": "AXE Dark chahiye"},
        {"role": "assistant", "content": "150 ml ya 215 ml?"},
    ]
    assert context[2]["role"] == "developer" and '"option_id": "opt1"' in context[2]["content"]
    assert searcher.queries == ["AXE Dark Temptation deodorant"]


async def test_a_new_address_drops_old_options() -> None:
    world, searcher = World(), FakeSearcher(AXE)
    responses = FakeResponses(
        [
            [call("search_products", "c1", query="AXE deodorant")],
            "Kaunsa size?",
            [call("add_to_basket", "c2", option_id="opt1", quantity=1, store_id=None)],
            "Dobara search karti hoon.",
        ]
    )
    chat = service(responses, world, searcher)
    await turn(chat, "AXE chahiye")

    result = await turn(chat, "pehla wala", swiggy_address_id="addr_office")

    assert responses.outputs(3)[0]["status"] == "error"
    assert all(item["role"] != "developer" for item in responses.requests[2]["input"])
    assert result.ui_events == []


async def test_more_than_two_searches_in_one_turn_are_refused_without_running() -> None:
    world, searcher = World(), FakeSearcher(AXE)
    responses = FakeResponses(
        [
            [
                call("search_products", "c1", query="atta"),
                call("search_products", "c2", query="dal"),
                call("search_products", "c3", query="doodh"),
            ],
            "Atta aur dal mile; doodh ke liye phir likhiye.",
        ]
    )
    chat = service(responses, world, searcher)

    result = await turn(chat, "atta, dal aur doodh")

    assert searcher.queries == ["atta", "dal"]
    statuses = [output["status"] for output in responses.outputs(1)]
    assert statuses == ["ok", "ok", "error"]
    assert "Search limit" in responses.outputs(1)[2]["message"]
    assert [event["type"] for event in result.ui_events] == ["tool.results", "tool.results"]


async def test_tool_rounds_are_capped_and_the_last_call_must_answer_in_text() -> None:
    world, searcher = World(), FakeSearcher(AXE)
    looping = [[call("view_basket", f"c{index}", store=None)] for index in range(MAX_TOOL_ROUNDS + 1)]
    responses = FakeResponses([*looping])
    chat = service(responses, world, searcher)

    result = await turn(chat, "basket dikhao")

    assert len(responses.requests) == MAX_TOOL_ROUNDS + 1
    assert [request["tool_choice"] for request in responses.requests] == ["auto"] * MAX_TOOL_ROUNDS + ["none"]
    assert result.reply


async def test_unknown_tool_is_rejected_and_never_executed(caplog: pytest.LogCaptureFixture) -> None:
    world, searcher = World(), FakeSearcher(AXE)
    responses = FakeResponses([[call("place_order", "c1", store="zepto")], "Order main nahi kar sakti."])
    chat = service(responses, world, searcher)

    with caplog.at_level(logging.INFO, logger="app.chat.service"):
        result = await turn(chat, "order kar do")

    assert responses.outputs(1) == [{"status": "error", "message": "Unknown tool place_order."}]
    assert "tools=unknown" in caplog.text
    assert result.ui_events == []
    assert searcher.queries == []


async def test_bill_with_two_stores_asks_and_with_a_store_emits_the_voice_approval_event() -> None:
    world = World()
    swiggy, zepto = FakeSearcher(AXE), FakeSearcher(AXE)
    responses = FakeResponses(
        [
            [call("search_products", "c1", query="AXE Dark deodorant")],
            [
                call("add_to_basket", "c2", option_id="opt1", quantity=1, store_id=None),
                call("add_to_basket", "c3", option_id="opt3", quantity=1, store_id=None),
            ],
            "Dono baskets mein add ho gaya.",
            [call("request_approval", "c4", store=None)],
            "Kis store ka bill chahiye, Instamart ya Zepto?",
            [call("request_approval", "c5", store=SWIGGY)],
            "Instamart ka bill screen par hai.",
        ]
    )
    chat = service(responses, world, swiggy, zepto)
    addresses = {"swiggy_address_id": "addr_home", "zepto_address_id": "addr_z"}
    await turn(chat, "AXE Dark dono stores par add karo", **addresses)

    ask = await turn(chat, "bill bana do", **addresses)
    bill = await turn(chat, "Instamart", **addresses)

    assert responses.outputs(4)[0]["status"] == "needs_store"
    assert ask.ui_events == []
    (event,) = bill.ui_events
    assert event["type"] == "basket.approval_requested"
    assert event["provider"] == SWIGGY
    request = event["request"]
    assert request["basket"]["provider"] == SWIGGY
    assert isinstance(request["approval"]["id"], str) and isinstance(request["approval"]["fingerprint"], str)
    assert isinstance(request["approval"]["payable"]["paise"], int)
    assert isinstance(request["quote"]["charges"], list)


async def test_a_failing_tool_reports_tool_error_like_voice() -> None:
    class BrokenSearcher(FakeSearcher):
        async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
            raise RuntimeError("boom")

    world = World()
    responses = FakeResponses([[call("search_products", "c1", query="atta")], "Search nahi ho paya."])
    chat = service(responses, world, BrokenSearcher([]))

    result = await turn(chat, "atta chahiye")

    assert responses.outputs(1) == [{"status": "error", "message": "The tool failed unexpectedly."}]
    assert [event["type"] for event in result.ui_events] == ["tool.error"]


async def test_session_profile_is_added_to_the_same_backend_instructions() -> None:
    world, searcher = World(), FakeSearcher(AXE)
    responses = FakeResponses(["Namaste Aarav."])
    profile = UserProfile(first_name="Aarav", address_form="masculine", language="hinglish")
    chat = service(responses, world, searcher, profile=profile)

    await turn(chat, "hello")

    instructions = responses.requests[0]["instructions"]
    assert instructions.startswith(BACKEND_INSTRUCTIONS)
    assert "Address form for the user: masculine" in instructions


async def test_model_failure_and_timeout_are_typed_errors() -> None:
    class Failing(FakeResponses):
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            raise RuntimeError("upstream 500")

    class Hanging(FakeResponses):
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    world, searcher = World(), FakeSearcher(AXE)
    with pytest.raises(ChatProviderError):
        await turn(service(Failing([]), world, searcher), "atta")

    hanging = service(Hanging([]), world, searcher)
    hanging.settings.chat_turn_timeout_seconds = 0.01
    with pytest.raises(ChatTimeout):
        await turn(hanging, "atta")


async def test_missing_api_key_is_a_configuration_error() -> None:
    from app.chat.service import ChatConfigurationError

    config = settings()
    world = World()
    voice = LiveSessionService(config, FakeSearcher(AXE), SidebandHub(config), baskets=world.baskets, approvals=world.approvals)
    with pytest.raises(ChatConfigurationError):
        await ChatTurnService(config, voice).run_turn(USER, None, "atta", [])


async def test_diagnostic_log_is_content_free(caplog: pytest.LogCaptureFixture) -> None:
    world, searcher = World(), FakeSearcher(AXE)
    responses = FakeResponses([[call("search_products", "c1", query="secret masala")], "Mil gaya."])
    chat = service(responses, world, searcher)

    with caplog.at_level(logging.INFO, logger="app.chat.service"):
        await turn(chat, "secret masala chahiye")

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "rounds=1 tools=search_products" in logged
    assert "masala" not in logged and "Mil gaya" not in logged


def test_history_is_bounded_merged_and_drops_the_current_message() -> None:
    saved = [HistoryMessage("user" if index % 2 == 0 else "assistant", f"m{index}") for index in range(20)]
    recent = [HistoryMessage("user", "m19-new"), HistoryMessage("assistant", "reply")]

    merged = merge_history(saved + [HistoryMessage("user", "now")], recent, "now")

    assert len(merged) == MAX_HISTORY_MESSAGES
    assert merged[-2:] == recent
    assert HistoryMessage("user", "now") not in merged


def test_history_respects_the_character_budget() -> None:
    saved = [HistoryMessage("user", "x" * 4_000), HistoryMessage("assistant", "y" * 4_000)]

    assert merge_history(saved, [], "next") == [HistoryMessage("assistant", "y" * 4_000)]
