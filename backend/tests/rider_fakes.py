"""In-memory fakes for the rider assist bridge: an Exotel stream and OpenAI Realtime."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.rider.orders import ActiveOrder
from app.rider.store import DeliveryNotes, RiderCallSummary


class Closed(Exception):
    pass


class FakeExotel:
    """Plays the Exotel side: queued events in, every sent message recorded."""

    def __init__(self, *, rate: int = 16000, echo_marks: bool = True) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None
        self.echo_marks = echo_marks
        self.rate = rate

    def start(self, *, to: str = "+918047491899", caller: str = "+919876543210") -> None:
        self.incoming.put_nowait(json.dumps({"event": "connected"}))
        self.incoming.put_nowait(
            json.dumps(
                {
                    "event": "start",
                    "sequence_number": "1",
                    "stream_sid": "MZ1",
                    "start": {
                        "stream_sid": "MZ1",
                        "call_sid": "CA1",
                        "account_sid": "AC1",
                        "from": caller,
                        "to": to,
                        "custom_parameters": {},
                        "media_format": {"encoding": "audio/x-raw", "sample_rate": str(self.rate), "bit_rate": "16"},
                    },
                }
            )
        )

    def media(self, byte_count: int = 3200) -> None:
        payload = base64.b64encode(b"\x10\x00" * (byte_count // 2)).decode()
        self.incoming.put_nowait(json.dumps({"event": "media", "stream_sid": "MZ1", "media": {"chunk": "1", "timestamp": "0", "payload": payload}}))

    def stop(self, reason: str = "callended") -> None:
        self.incoming.put_nowait(json.dumps({"event": "stop", "stream_sid": "MZ1", "stop": {"reason": reason}}))

    async def receive_text(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise Closed()
        return item

    async def send_text(self, data: str) -> None:
        if self.closed_with is not None:
            raise Closed()
        message = json.loads(data)
        self.sent.append(message)
        if self.echo_marks and message.get("event") == "mark":
            self.incoming.put_nowait(json.dumps({"event": "mark", "stream_sid": "MZ1", "mark": message["mark"]}))

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code
        self.incoming.put_nowait(None)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message.get("event") == kind]


def audio_delta(response_id: str, item_id: str, millis: int = 300) -> dict[str, Any]:
    pcm = b"\x20\x00" * (24 * millis)
    return {
        "type": "response.output_audio.delta",
        "response_id": response_id,
        "item_id": item_id,
        "delta": base64.b64encode(pcm).decode(),
    }


def spoken(response_id: str, text: str, *, calls: list[tuple[str, dict[str, Any]]] = ()) -> list[dict[str, Any]]:
    """One assistant response: audio, its transcript, then response.done with tool calls."""

    item = f"item_{response_id}"
    events: list[dict[str, Any]] = []
    if text:
        events += [
            audio_delta(response_id, item),
            {"type": "response.output_audio.done", "response_id": response_id, "item_id": item},
            {"type": "response.output_audio_transcript.done", "response_id": response_id, "transcript": text},
        ]
    output = [
        {"type": "function_call", "call_id": f"call_{response_id}_{index}", "name": name, "arguments": json.dumps(arguments)}
        for index, (name, arguments) in enumerate(calls)
    ]
    events.append({"type": "response.done", "response": {"id": response_id, "status": "completed", "output": output}})
    return events


def rider_says(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "input_audio_buffer.speech_started"},
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "in", "transcript": text},
    ]


@dataclass
class FakeRealtime:
    """Emits the next scripted turn each time the bridge asks for a response."""

    turns: list[list[dict[str, Any]]]
    sent: list[dict[str, Any]] = field(default_factory=list)
    incoming: asyncio.Queue = field(default_factory=asyncio.Queue)
    closed: bool = False

    async def send(self, message: str) -> None:
        event = json.loads(message)
        self.sent.append(event)
        if event.get("type") == "response.create" and self.turns:
            for item in self.turns.pop(0):
                self.incoming.put_nowait(json.dumps(item))

    async def recv(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise Closed()
        return item

    async def close(self) -> None:
        self.closed = True
        self.incoming.put_nowait(None)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.sent if event.get("type") == kind]

    def tool_outputs(self) -> list[dict[str, Any]]:
        return [
            json.loads(event["item"]["output"])
            for event in self.of("conversation.item.create")
            if event["item"]["type"] == "function_call_output"
        ]


class FakeOrders:
    def __init__(self, orders: list[ActiveOrder] | None = None, *, fail: bool = False) -> None:
        self.orders = orders or []
        self.fail = fail
        self.calls = 0

    async def active_orders(self, user_id: str) -> list[ActiveOrder]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("database down")
        return self.orders


@dataclass
class _Saved:
    id: str


class FakeRecorder:
    def __init__(self) -> None:
        self.calls: dict[str, RiderCallSummary] = {}
        self.history: list[tuple[str, RiderCallSummary]] = []

    async def add_call(self, user_id: str, call: RiderCallSummary) -> _Saved:
        call_id = f"call-{len(self.calls) + 1}"
        self.calls[call_id] = call
        self.history.append(("add", call))
        return _Saved(call_id)

    async def update_call(self, user_id: str, call_id: str, call: RiderCallSummary) -> bool:
        self.calls[call_id] = call
        self.history.append(("update", call))
        return True

    @property
    def last(self) -> RiderCallSummary:
        return list(self.calls.values())[-1]


def notes_getter(notes: DeliveryNotes):
    async def read() -> DeliveryNotes:
        return notes

    return read


ORDER = ActiveOrder(
    store="Zepto",
    status="out for delivery",
    items_count=3,
    payment="cash_on_delivery",
    address_label="Home",
    address="B-402, Lake View Apartments, Sector 12",
    placed_at=datetime(2026, 11, 22, 8, 0, tzinfo=UTC),
)
