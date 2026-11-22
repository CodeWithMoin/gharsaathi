import asyncio
import json
from typing import Any

import pytest

from app.voice.sideband import (
    MAX_CONTINUATION_ROUNDS,
    MAX_SEARCHES_PER_DELEGATION,
    SessionChannel,
    SidebandRunner,
)
from app.voice.tools import ToolOutcome


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
            for _ in range(5):
                await asyncio.sleep(0)
        for _ in range(20):
            await asyncio.sleep(0)
        yield json.dumps({"type": "session.closed"})


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        self.calls.append(name)
        return ToolOutcome(model_output={"status": "ok"})


def call(delegation: str, call_id: str, name: str, event_id: str) -> dict[str, Any]:
    return {
        "type": "response.event",
        "event_id": event_id,
        "delegation_id": delegation,
        "event": {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"},
        },
    }


def done(delegation: str, event_id: str) -> dict[str, Any]:
    return {"type": "response.event", "event_id": event_id, "delegation_id": delegation,
            "event": {"type": "response.completed"}}


def outputs(connection: FakeConnection) -> dict[str, dict[str, Any]]:
    return {
        event["item"]["call_id"]: json.loads(event["item"]["output"])
        for event in connection.sent
        if event["type"] == "response.item.create"
    }


async def run(events: list[dict[str, Any]]) -> tuple[FakeConnection, RecordingExecutor]:
    connection = FakeConnection(events)
    executor = RecordingExecutor()
    await SidebandRunner(connection, executor, SessionChannel().publish).run()  # type: ignore[arg-type]
    return connection, executor


def test_caps_are_small() -> None:
    assert (MAX_CONTINUATION_ROUNDS, MAX_SEARCHES_PER_DELEGATION) == (6, 2)


@pytest.mark.asyncio
async def test_third_search_in_a_delegation_is_not_run() -> None:
    events = [
        call("d1", "c1", "search_products", "e1"),
        call("d1", "c2", "search_products", "e2"),
        call("d1", "c3", "search_products", "e3"),
        call("d1", "c4", "view_basket", "e4"),
        done("d1", "e5"),
        call("d2", "c5", "search_products", "e6"),
        done("d2", "e7"),
    ]
    connection, executor = await run(events)

    assert executor.calls == ["search_products", "search_products", "view_basket", "search_products"]
    results = outputs(connection)
    assert results["c3"]["status"] == "limit_reached"
    assert "results you already have" in results["c3"]["message"]
    assert results["c5"]["status"] == "ok"  # another delegation has its own budget
    assert [event["type"] for event in connection.sent].count("response.create") == 2


@pytest.mark.asyncio
async def test_no_continuation_past_the_round_cap() -> None:
    events: list[dict[str, Any]] = []
    for round_number in range(MAX_CONTINUATION_ROUNDS + 2):
        events.append(call("d1", f"c{round_number}", "view_basket", f"call{round_number}"))
        events.append(done("d1", f"done{round_number}"))
    connection, executor = await run(events)

    continues = [event for event in connection.sent if event["type"] == "response.create"]
    assert len(continues) == MAX_CONTINUATION_ROUNDS
    assert len(executor.calls) == MAX_CONTINUATION_ROUNDS
    results = outputs(connection)
    capped = [results[f"c{n}"] for n in range(MAX_CONTINUATION_ROUNDS, MAX_CONTINUATION_ROUNDS + 2)]
    assert all(result["status"] == "limit_reached" for result in capped)
    assert "Answer the user now" in capped[0]["message"]
