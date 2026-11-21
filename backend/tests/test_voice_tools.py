import asyncio
import json
from typing import Any

import pytest

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyProviderError,
    SwiggyRateLimited,
)
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.voice.sideband import FINISHED, SessionChannel, SidebandRunner
from app.voice.tools import ShoppingToolExecutor


def product(name: str, offer: float, mrp: float, available: bool = True) -> Product:
    return Product(
        product_id=f"p_{name}",
        sku_id=f"sku_{name}",
        spin_id=f"spin_{name}",
        name=name,
        brand="Fortune",
        pack_size="1 L",
        price=Price(mrp=mrp, offer_price=offer),
        available=available,
    )


class FakeSearcher:
    def __init__(self, result: ProductSearchResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, str, int]] = []

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        self.calls.append((query, address_id, offset))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


OIL = ProductSearchResult(
    products=[product("Sunlite Oil", 154, 180), product("Rice Bran Oil", 170, 170, False)]
)


@pytest.mark.asyncio
async def test_search_returns_provider_prices_and_ui_products() -> None:
    searcher = FakeSearcher(OIL)
    outcome = await ShoppingToolExecutor(searcher, "addr_home").execute(
        "search_products", json.dumps({"query": "  Fortune   oil 1 litre "})
    )
    assert searcher.calls == [("Fortune oil 1 litre", "addr_home", 0)]
    first = outcome.model_output["results"][0]
    assert first["price_rupees"] == 154
    assert first["mrp_rupees"] == 180
    assert first["saving_rupees"] == 26
    assert "mrp_rupees" not in outcome.model_output["results"][1]
    assert outcome.model_output["results"][1]["available"] is False
    assert outcome.ui_event is not None
    assert len(outcome.ui_event["products"]) == 2


@pytest.mark.asyncio
async def test_search_without_address_does_not_call_provider() -> None:
    searcher = FakeSearcher(OIL)
    outcome = await ShoppingToolExecutor(searcher, None).execute(
        "search_products", json.dumps({"query": "atta"})
    )
    assert searcher.calls == []
    assert outcome.model_output["status"] == "not_connected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments", ["not json", json.dumps({"query": "a"}), json.dumps({"q": "atta"})]
)
async def test_invalid_model_arguments_are_rejected(arguments: str) -> None:
    searcher = FakeSearcher(OIL)
    outcome = await ShoppingToolExecutor(searcher, "addr_home").execute(
        "search_products", arguments
    )
    assert searcher.calls == []
    assert outcome.model_output["status"] == "error"


@pytest.mark.asyncio
async def test_unknown_tool_is_refused() -> None:
    searcher = FakeSearcher(OIL)
    outcome = await ShoppingToolExecutor(searcher, "addr_home").execute(
        "place_order", "{}"
    )
    assert searcher.calls == []
    assert outcome.model_output["status"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SwiggyAuthenticationRequired("Connect"), "not_connected"),
        (SwiggyProviderError("boom"), "error"),
    ],
)
async def test_provider_failures_become_honest_results(error: Exception, status: str) -> None:
    outcome = await ShoppingToolExecutor(FakeSearcher(error), "addr_home").execute(
        "search_products", json.dumps({"query": "atta"})
    )
    assert outcome.model_output["status"] == status
    assert "results" not in outcome.model_output


@pytest.mark.asyncio
async def test_rate_limit_is_not_misreported_as_unclear_availability() -> None:
    outcome = await ShoppingToolExecutor(
        FakeSearcher(SwiggyRateLimited("limited", retry_after_seconds=45)), "addr_home"
    ).execute("search_products", json.dumps({"query": "Amul milk 1 litre"}))

    assert outcome.model_output["status"] == "rate_limited"
    assert outcome.model_output["retry_after_seconds"] == 45
    assert "No stock result" in outcome.model_output["message"]
    assert "results" not in outcome.model_output


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
            await asyncio.sleep(0)
        # Give scheduled tool and continuation tasks time to run.
        for _ in range(20):
            await asyncio.sleep(0)
        yield json.dumps({"type": "session.closed"})


def nested(delegation_id: str, event: dict[str, Any], event_id: str) -> dict[str, Any]:
    return {
        "type": "response.event",
        "event_id": event_id,
        "delegation_id": delegation_id,
        "event": event,
    }


FUNCTION_CALL = {
    "type": "response.output_item.done",
    "item": {
        "type": "function_call",
        "call_id": "call_1",
        "name": "search_products",
        "arguments": json.dumps({"query": "Fortune oil"}),
    },
}


@pytest.mark.asyncio
async def test_sideband_runs_call_returns_output_then_continues_once() -> None:
    connection = FakeConnection(
        [
            nested("del_1", FUNCTION_CALL, "e1"),
            nested("del_1", FUNCTION_CALL, "e1"),  # replayed duplicate
            nested("del_1", {"type": "response.completed", "response": {"output": []}}, "e2"),
        ]
    )
    channel = SessionChannel()
    searcher = FakeSearcher(OIL)
    await SidebandRunner(
        connection, ShoppingToolExecutor(searcher, "addr_home"), channel.publish
    ).run()

    assert searcher.calls == [("Fortune oil", "addr_home", 0)]
    assert [event["type"] for event in connection.sent] == [
        "response.item.create",
        "response.create",
    ]
    item = connection.sent[0]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_1"
    assert json.loads(item["output"])["status"] == "ok"
    assert [event["type"] for event in channel.history] == ["tool.started", "tool.results"]


@pytest.mark.asyncio
async def test_sideband_ignores_non_function_items_and_does_not_continue() -> None:
    connection = FakeConnection(
        [
            nested("del_1", {"type": "response.output_item.done", "item": {"type": "message"}}, "e1"),
            nested("del_1", {"type": "response.completed"}, "e2"),
        ]
    )
    searcher = FakeSearcher(OIL)
    await SidebandRunner(
        connection, ShoppingToolExecutor(searcher, "addr_home"), SessionChannel().publish
    ).run()
    assert searcher.calls == []
    assert connection.sent == []


def test_channel_finish_is_published_once() -> None:
    channel = SessionChannel()
    channel.finish()
    channel.finish()
    assert channel.history == [FINISHED]
