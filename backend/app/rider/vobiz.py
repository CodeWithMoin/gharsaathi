"""Vobiz: answer-URL XML, webhook signatures, per-call stream URLs and the stream protocol.

Sources (Vobiz docs, read 2026-11-22; cited in docs/RIDER_ASSIST.md):
* <Stream> element: https://www.vobiz.ai/docs/xml/stream
* Stream events (start/media/playedStream/clearedAudio; playAudio/checkpoint/clearAudio/stop):
  https://www.vobiz.ai/docs/xml/stream/stream-events
* Audio formats (inbound μ-law 8 kHz or L16 8/16 kHz; outbound μ-law 8 kHz or L16 8/16/24 kHz):
  https://www.vobiz.ai/docs/xml/stream/audio-formats
* Webhook signatures (HMAC-SHA256 over base URL + nonce with the auth token):
  https://www.vobiz.ai/docs/concepts/validating-callbacks
* extraHeaders cannot authenticate the socket (they arrive only in ``start``), so the
  per-call secret rides in the stream URL: https://www.vobiz.ai/docs/integrations/deepgram
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

from app.rider.telephony import MULAW_PLAN, AudioPlan, ProtocolError, StreamStart, TelephonyEvent, l16_plan

STREAM_PATH = "/api/rider/stream"
ANSWER_PATH = "/api/rider/vobiz/answer"
AFTER_STREAM_PATH = "/api/rider/vobiz/after-stream"
# A stream URL is valid this long after the answer URL issued it.
STREAM_TOKEN_TTL_SECONDS = 120
CALL_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
TOKEN = re.compile(r"^(\d{10})x([0-9a-f]{48})$")
MIN_SECRET_LENGTH = 32

APOLOGY = {
    "hinglish": (
        "hi-IN",
        "Maaf kijiye, assistant abhi jawab nahi de pa rahi. Ghar walon ko bata diya gaya hai. "
        "Kripya thodi der mein dobara call kijiye.",
    ),
    "hindi": (
        "hi-IN",
        "Maaf kijiye, sahayak abhi jawab nahi de pa rahi. Ghar walon ko bata diya gaya hai. "
        "Kripya thodi der mein dobara call kijiye.",
    ),
    "english": (
        "en-IN",
        "Sorry, the assistant can't answer right now. The household has been told. "
        "Please call again in a little while.",
    ),
}
UNAVAILABLE = {
    "hinglish": ("hi-IN", "Maaf kijiye, abhi koi call nahi utha pa raha. Kripya thodi der baad dobara call kijiye."),
    "hindi": ("hi-IN", "Maaf kijiye, abhi koi call nahi utha pa raha. Kripya thodi der baad dobara call kijiye."),
    "english": ("en-IN", "Sorry, nobody can take this call right now. Please call again later."),
}


# ---- answer URL --------------------------------------------------------------


def base_url(public_url: str, path: str) -> str:
    """``https://host`` + path, no query: what Vobiz signs."""

    parts = urlsplit(public_url.strip().rstrip("/"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + path, "", ""))


def ws_origin(public_url: str) -> str:
    parts = urlsplit(public_url.strip().rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def valid_public_url(public_url: str) -> bool:
    parts = urlsplit(public_url.strip())
    return parts.scheme == "https" and bool(parts.netloc) and not parts.query and "@" not in parts.netloc


def signature_valid(url: str, auth_token: str, headers: Mapping[str, str]) -> bool:
    """V3 or V2 (HMAC-SHA256, base64) with the account auth token. Fails closed."""

    if not auth_token:
        return False
    lowered = {key.lower(): value for key, value in headers.items()}
    base = base_url(url, "") if "://" in url else url
    key = auth_token.encode()
    checks = (
        ("x-vobiz-signature-v3", "x-vobiz-signature-v3-nonce", "."),
        ("x-vobiz-signature-v2", "x-vobiz-signature-v2-nonce", ""),
    )
    for signature_header, nonce_header, joiner in checks:
        signature = lowered.get(signature_header, "")
        nonce = lowered.get(nonce_header, "")
        if not signature or not nonce:
            continue
        expected = base64.b64encode(
            hmac.new(key, f"{base}{joiner}{nonce}".encode(), hashlib.sha256).digest()
        ).decode()
        if hmac.compare_digest(signature.strip(), expected):
            return True
    return False


def _token_mac(secret: str, call_id: str, expiry: int) -> str:
    message = f"rider-stream:{call_id}:{expiry}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:48]


def stream_token(secret: str, call_id: str, *, now: float | None = None) -> str:
    expiry = int((time.time() if now is None else now) + STREAM_TOKEN_TTL_SECONDS)
    return f"{expiry:010d}x{_token_mac(secret, call_id, expiry)}"


def stream_token_valid(secret: str, call_id: str, token: str, *, now: float | None = None) -> bool:
    if len(secret) < MIN_SECRET_LENGTH or not CALL_ID.match(call_id or ""):
        return False
    match = TOKEN.match(token or "")
    if match is None:
        return False
    expiry = int(match.group(1))
    if expiry < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(match.group(2), _token_mac(secret, call_id, expiry))


STREAM_PARAM = "s"


def stream_url(public_url: str, secret: str, call_id: str, *, now: float | None = None) -> str:
    """``wss://host/api/rider/stream?s=<call id>.<signed token>`` (one parameter, so the
    XML needs no ``&``; the path stays exactly ``/api/rider/stream`` for the ALB rule)."""

    return f"{ws_origin(public_url)}{STREAM_PATH}?{STREAM_PARAM}={call_id}.{stream_token(secret, call_id, now=now)}"


def split_stream_param(value: str | None) -> tuple[str, str]:
    call_id, _, token = (value or "").rpartition(".")
    return call_id, token


def _speak(language: str, text: str) -> str:
    return f'<Speak language="{escape(language)}">{escape(text)}</Speak>'


def answer_xml(stream: str, after_stream: str, *, timeout_seconds: int) -> str:
    """Bidirectional μ-law stream; when it ends, Vobiz asks ``after_stream`` what to do."""

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
        f'  <Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound" '
        f'contentType="audio/x-mulaw;rate=8000" streamTimeout="{int(timeout_seconds)}">'
        f"{escape(stream)}</Stream>\n"
        f'  <Redirect method="POST">{escape(after_stream)}</Redirect>\n'
        "</Response>\n"
    )


def after_stream_xml(*, apologise: bool, language: str) -> str:
    speak = ""
    if apologise:
        speak = "  " + _speak(*APOLOGY.get(language, APOLOGY["hinglish"])) + "\n"
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{speak}  <Hangup/>\n</Response>\n'


def unavailable_xml(language: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  '
        + _speak(*UNAVAILABLE.get(language, UNAVAILABLE["hinglish"]))
        + "\n  <Hangup/>\n</Response>\n"
    )


# ---- stream protocol -----------------------------------------------------------


def parse_extra_headers(value: Any) -> dict[str, str]:
    """``"{X-VH-key: value, X-VH-other: v2}"`` (not JSON) → {"key": "value", ...}."""

    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, str):
        body = value.strip().removeprefix("{").removesuffix("}")
        items = [part.split(":", 1) for part in body.split(",") if ":" in part]  # type: ignore[assignment]
    else:
        return {}
    out: dict[str, str] = {}
    for key, item in items:
        name = str(key).strip().strip('"')
        name = name[5:] if name.lower().startswith("x-vh-") else name
        if name:
            out[name[:64]] = str(item).strip().strip('"')[:256]
    return out


def _plan(media_format: Any) -> AudioPlan:
    if not isinstance(media_format, dict):
        raise ProtocolError("start without mediaFormat.")
    encoding = str(media_format.get("encoding", "")).lower()
    try:
        rate = int(media_format.get("sampleRate"))
    except (TypeError, ValueError) as exc:
        raise ProtocolError("start without a sample rate.") from exc
    if encoding in {"audio/x-mulaw", "audio/pcmu", "mulaw"} and rate == 8000:
        return MULAW_PLAN
    if encoding in {"audio/x-l16", "l16"} and rate in {8000, 16000}:
        return l16_plan(rate)
    raise ProtocolError("Unsupported stream audio format.")


class VobizAdapter:
    name = "vobiz"
    # Vobiz recommends 20–60 ms playAudio chunks for responsive barge-in.
    frame_ms = 40

    def parse(self, raw: str | bytes) -> TelephonyEvent:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            raise ProtocolError("Not JSON.") from exc
        if not isinstance(message, dict):
            raise ProtocolError("Not an event object.")
        kind = message.get("event")
        if kind == "start":
            start = message.get("start")
            if not isinstance(start, dict):
                raise ProtocolError("start without a start object.")
            call_id, stream_id = start.get("callId"), start.get("streamId")
            if not isinstance(call_id, str) or not isinstance(stream_id, str) or not stream_id:
                raise ProtocolError("start without callId/streamId.")
            return TelephonyEvent(
                "start",
                start=StreamStart(
                    call_id=call_id[:64],
                    stream_id=stream_id[:64],
                    audio=_plan(start.get("mediaFormat")),
                    extra=parse_extra_headers(message.get("extra_headers")),
                ),
            )
        if kind == "media":
            media = message.get("media")
            if not isinstance(media, dict) or not isinstance(media.get("payload"), str):
                raise ProtocolError("media without a payload.")
            if media.get("track", "inbound") != "inbound":
                return TelephonyEvent("ignored")
            try:
                audio = base64.b64decode(media["payload"], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProtocolError("media payload is not base64.") from exc
            return TelephonyEvent("media", audio=audio)
        if kind == "playedStream":
            name = message.get("name")
            return TelephonyEvent("played", name=name[:64] if isinstance(name, str) else None)
        if kind == "clearedAudio":
            return TelephonyEvent("cleared")
        if kind == "dtmf":
            return TelephonyEvent("dtmf")
        return TelephonyEvent("ignored")

    def play(self, stream_id: str, plan: AudioPlan, audio: bytes) -> str:
        return json.dumps(
            {
                "event": "playAudio",
                "streamId": stream_id,
                "media": {
                    "contentType": "audio/x-mulaw" if plan.outbound_encoding == "mulaw" else "audio/x-l16",
                    "sampleRate": plan.outbound_rate,
                    "payload": base64.b64encode(audio).decode("ascii"),
                },
            }
        )

    def checkpoint(self, stream_id: str, name: str) -> str:
        return json.dumps({"event": "checkpoint", "streamId": stream_id, "name": name})

    def clear(self, stream_id: str) -> str:
        return json.dumps({"event": "clearAudio", "streamId": stream_id})

    def stop(self, stream_id: str) -> str | None:
        return json.dumps({"event": "stop", "streamId": stream_id})
