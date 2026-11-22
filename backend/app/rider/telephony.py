"""The carrier-specific edge of rider assist, behind one small interface.

The bridge (``bridge.py``) only sees ``TelephonyEvent`` objects and asks the adapter
for outgoing messages. ``vobiz.py`` is the one implementation today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.rider.audio import REALTIME_PCM_RATE


class ProtocolError(ValueError):
    """A message that is not a usable stream event."""


@dataclass(frozen=True)
class AudioPlan:
    """How audio moves between the call and OpenAI Realtime for one stream."""

    inbound_encoding: Literal["mulaw", "l16"]
    inbound_rate: int
    # Realtime session audio format (input and output), per OpenAI's Realtime reference.
    realtime_format: dict[str, Any]
    # What the adapter declares on outgoing audio.
    outbound_encoding: Literal["mulaw", "l16"]
    outbound_rate: int

    @property
    def resample_inbound(self) -> bool:
        return self.inbound_encoding == "l16" and self.inbound_rate != REALTIME_PCM_RATE

    @property
    def outbound_sample_bytes(self) -> int:
        return 1 if self.outbound_encoding == "mulaw" else 2

    @property
    def outbound_bytes_per_ms(self) -> int:
        return self.outbound_rate * self.outbound_sample_bytes // 1000


MULAW_PLAN = AudioPlan("mulaw", 8000, {"type": "audio/pcmu"}, "mulaw", 8000)


def l16_plan(rate: int) -> AudioPlan:
    return AudioPlan(
        "l16", rate, {"type": "audio/pcm", "rate": REALTIME_PCM_RATE}, "l16", REALTIME_PCM_RATE
    )


@dataclass(frozen=True)
class StreamStart:
    call_id: str
    stream_id: str
    audio: AudioPlan
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TelephonyEvent:
    # start | media | played | cleared | dtmf | stop | ignored
    kind: str
    start: StreamStart | None = None
    audio: bytes = b""
    name: str | None = None


class TelephonyAdapter(Protocol):
    name: str
    # Outgoing frame length in milliseconds (barge-in responsiveness).
    frame_ms: int

    def parse(self, raw: str | bytes) -> TelephonyEvent: ...

    def play(self, stream_id: str, plan: AudioPlan, audio: bytes) -> str: ...

    def checkpoint(self, stream_id: str, name: str) -> str: ...

    def clear(self, stream_id: str) -> str: ...

    def stop(self, stream_id: str) -> str | None: ...


def mask_number(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 4:
        return "unknown"
    return "•••• " + digits[-3:]


def same_number(a: str, b: str) -> bool:
    """Compare phone numbers by their last 10 digits (+91 / 0 prefixes vary)."""

    da = "".join(ch for ch in a if ch.isdigit())[-10:]
    db = "".join(ch for ch in b if ch.isdigit())[-10:]
    return bool(da) and da == db
