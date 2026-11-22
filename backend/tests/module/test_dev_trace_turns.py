"""DEV_TRACE through the real typed-chat loop and the voice sideband (fakes at the edges)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.chat.service import ChatTurnService
from app.core.config import Settings
from app.devtools.trace import read_traces
from app.voice.service import LiveSessionService
from app.voice.sideband import SessionChannel, SidebandHub, SidebandRunner
from app.voice.toolbox import ToolOutcome
from app.devtools.trace import start_trace
from tests.module.test_chat_turn import AXE, FakeProfiles, FakeResponses, FakeSearcher, call
from tests.module.test_turn_latency import FakeConnection
from tests.shopping_fakes import World


def traced_settings(tmp_path: Path, enabled: bool = True) -> Settings:
    return Settings(_env_file=None, openai_api_key=SecretStr(""), dev_trace=enabled, dev_trace_dir=tmp_path / "traces")


def chat(config: Settings, responses: FakeResponses) -> ChatTurnService:
    world = World()
    voice = LiveSessionService(
        config, FakeSearcher(AXE), SidebandHub(config), baskets=world.baskets, approvals=world.approvals,
        profile_reader=FakeProfiles(None),
    )
    return ChatTurnService(config, voice, responses=responses)


async def test_typed_turn_writes_one_redacted_trace(tmp_path: Path) -> None:
    config = traced_settings(tmp_path)
    responses = FakeResponses(
        [
            [call("search_products", "c1", query="AXE Dark Temptation deodorant")],
            [call("add_to_basket", "c2", option_id="opt1", quantity=1, store_id=None)],
            "AXE Dark Temptation 150 ml add ho gaya. SECRET-REPLY",
        ]
    )
    await chat(config, responses).run_turn("user_t", None, "SECRET-MESSAGE deo chahiye", "addr_home")
    [record] = read_traces(config.dev_trace_dir)
    assert record["channel"] == "typed" and record["outcome"] == "ok"
    assert record["totals"]["rounds"] == 3 and record["totals"]["tools"] == 2
    first = record["rounds"][0]["tools"][0]
    assert first["name"] == "search_products" and first["args"] == {"query": "AXE Dark Temptation deodorant"}
    assert first["status"] == "ok" and first["ms"] >= 0
    assert record["rounds"][1]["tools"][0]["name"] == "add_to_basket"
    text = json.dumps(record)
    assert "SECRET-MESSAGE" not in text and "SECRET-REPLY" not in text and "AXE Dark Temptation 150" not in text


async def test_typed_turn_writes_nothing_when_off(tmp_path: Path) -> None:
    config = traced_settings(tmp_path, enabled=False)
    await chat(config, FakeResponses(["Namaste"])).run_turn("user_t", None, "hi", "addr_home")
    assert not (tmp_path / "traces").exists()


class Executor:
    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        return ToolOutcome(model_output={"status": "ok", "options": [{"name": "SECRET-RESULT"}]})


async def test_voice_delegation_writes_rounds_tools_and_tokens(tmp_path: Path) -> None:
    config = traced_settings(tmp_path)

    def event(event_id: str, inner: dict[str, Any]) -> dict[str, Any]:
        return {"type": "response.event", "event_id": event_id, "delegation_id": "d1", "event": inner}

    item = {"type": "function_call", "call_id": "c1", "name": "search_products", "arguments": '{"query": "atta 5 kg"}'}
    usage = {"input_tokens": 900, "output_tokens": 12, "input_tokens_details": {"cached_tokens": 800}}
    events = [
        event("e1", {"type": "response.created"}),
        event("e2", {"type": "response.output_item.done", "item": item}),
        event("e3", {"type": "response.completed", "response": {"usage": usage}}),
        event("e4", {"type": "response.output_text.delta", "delta": "SECRET-REPLY"}),
        event("e5", {"type": "response.completed", "response": {"usage": {"input_tokens": 950, "output_tokens": 20}}}),
    ]
    await SidebandRunner(
        FakeConnection(events), Executor(), SessionChannel().publish,  # type: ignore[arg-type]
        trace_factory=lambda: start_trace(config, "voice", session_id="sess_abc"),
    ).run()
    [record] = read_traces(config.dev_trace_dir)
    assert record["channel"] == "voice" and record["outcome"] == "completed" and record["session_id"] == "sess_abc"
    assert [r["round"] for r in record["rounds"]] == [1, 2]
    assert record["rounds"][0]["tokens"] == {"input": 900, "output": 12, "cached": 800}
    assert record["rounds"][0]["tools"][0]["args"] == {"query": "atta 5 kg"}
    assert record["rounds"][0]["tools"][0]["counts"] == {"options": 1}
    assert record["totals"]["tokens"] == {"input": 1850, "output": 32, "cached": 800}
    assert "SECRET" not in json.dumps(record)
