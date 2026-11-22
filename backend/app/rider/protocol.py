"""Exotel AgentStream (Voicebot applet) WebSocket messages.

Reference: https://developer.exotel.com/docs/agentstream/websocket-protocol .
Incoming: ``connected``, ``start``, ``media``, ``dtmf``, ``mark``, ``stop``.
Outgoing (bidirectional only): ``media``, ``mark``, ``clear``.
Field values arrive as strings or numbers depending on the account; both are accepted.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from app.rider.audio import SUPPORTED_RATES, valid_chunk

DEFAULT_RATE = 8000


class ProtocolError(ValueError):
    """A message that is not a usable Exotel stream event."""


@dataclass(frozen=True)
class StartInfo:
    stream_sid: str
    call_sid: str
    from_number: str
    to_number: str
    sample_rate: int | None
    custom_parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExotelEvent:
    kind: str
    stream_sid: str | None = None
    start: StartInfo | None = None
    audio: bytes = b""
    digit: str | None = None
    mark: str | None = None
    stop_reason: str | None = None


def _text(value: Any, limit: int = 128) -> str:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip()[:limit]
    return ""


def _rate(value: Any) -> int | None:
    try:
        rate = int(str(value).lower().replace("hz", "").strip())
    except (TypeError, ValueError):
        return None
    return rate if rate in SUPPORTED_RATES else None


def parse_event(raw: str | bytes) -> ExotelEvent:
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ProtocolError("Not JSON.") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Not an event object.")
    kind = _text(message.get("event"), 32)
    stream_sid = _text(message.get("stream_sid")) or None
    if kind == "connected":
        return ExotelEvent("connected")
    if kind == "start":
        start = message.get("start")
        if not isinstance(start, dict):
            raise ProtocolError("start without a start object.")
        sid = _text(start.get("stream_sid")) or stream_sid
        if not sid:
            raise ProtocolError("start without a stream_sid.")
        media_format = start.get("media_format") if isinstance(start.get("media_format"), dict) else {}
        params = start.get("custom_parameters")
        custom = (
            {_text(k, 64): _text(v, 256) for k, v in params.items()} if isinstance(params, dict) else {}
        )
        return ExotelEvent(
            "start",
            stream_sid=sid,
            start=StartInfo(
                stream_sid=sid,
                call_sid=_text(start.get("call_sid")),
                from_number=_text(start.get("from"), 32),
                to_number=_text(start.get("to"), 32),
                sample_rate=_rate(media_format.get("sample_rate")),
                custom_parameters=custom,
            ),
        )
    if kind == "media":
        media = message.get("media")
        payload = media.get("payload") if isinstance(media, dict) else None
        if not isinstance(payload, str):
            raise ProtocolError("media without a payload.")
        try:
            audio = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProtocolError("media payload is not base64.") from exc
        return ExotelEvent("media", stream_sid=stream_sid, audio=audio)
    if kind == "dtmf":
        dtmf = message.get("dtmf") if isinstance(message.get("dtmf"), dict) else {}
        return ExotelEvent("dtmf", stream_sid=stream_sid, digit=_text(dtmf.get("digit"), 4) or None)
    if kind == "mark":
        mark = message.get("mark") if isinstance(message.get("mark"), dict) else {}
        return ExotelEvent("mark", stream_sid=stream_sid, mark=_text(mark.get("name"), 64) or None)
    if kind == "stop":
        stop = message.get("stop") if isinstance(message.get("stop"), dict) else {}
        return ExotelEvent("stop", stream_sid=stream_sid, stop_reason=_text(stop.get("reason"), 32) or None)
    raise ProtocolError(f"Unknown event {kind!r}.")


def media_message(stream_sid: str, pcm: bytes) -> str:
    if not valid_chunk(len(pcm)):
        raise ValueError(f"Exotel media chunk of {len(pcm)} bytes breaks the chunk rules.")
    return json.dumps(
        {
            "event": "media",
            "stream_sid": stream_sid,
            "media": {"payload": base64.b64encode(pcm).decode("ascii")},
        }
    )


def mark_message(stream_sid: str, name: str) -> str:
    return json.dumps({"event": "mark", "stream_sid": stream_sid, "mark": {"name": name}})


def clear_message(stream_sid: str) -> str:
    return json.dumps({"event": "clear", "stream_sid": stream_sid})


def sample_rate_from_query(query_string: str) -> int | None:
    values = parse_qs(query_string)
    raw = (values.get("sample-rate") or values.get("sample_rate") or [None])[0]
    return _rate(raw) if raw is not None else None


def resolve_sample_rate(start: StartInfo | None, query_rate: int | None) -> int:
    """The URL's ``?sample-rate=`` wins (it configures Exotel); then ``media_format``."""

    if query_rate is not None:
        return query_rate
    if start is not None and start.sample_rate is not None:
        return start.sample_rate
    return DEFAULT_RATE


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
