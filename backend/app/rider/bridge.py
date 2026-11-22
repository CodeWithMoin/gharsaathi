"""One forwarded call: the carrier's bidirectional stream ↔ OpenAI Realtime (WebSocket).

Carrier side: a ``TelephonyAdapter`` (``vobiz.py``) parses events and formats
playback, checkpoints, clear (barge-in) and stop. Realtime side
(https://developers.openai.com/api/docs/guides/realtime-conversations): μ-law
passthrough (``audio/pcmu``) or 24 kHz PCM, server VAD, function tools handled
here, ``conversation.item.truncate`` after a barge-in. Only a short summary of
the call is kept, never audio.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.rider.audio import REALTIME_PCM_RATE, OutboundChunker, Resampler
from app.rider.prompts import RIDER_PROMPT_VERSION, WRAP_UP, greeting, rider_instructions
from app.rider.telephony import (
    MULAW_PLAN,
    AudioPlan,
    ProtocolError,
    StreamStart,
    TelephonyAdapter,
    TelephonyEvent,
)
from app.rider.safety import redact_secrets, request_flags
from app.rider.store import RiderCallSummary
from app.rider.tools import RIDER_TOOL_DEFINITIONS, OwnerNote, RiderToolbox

logger = logging.getLogger(__name__)

START_TIMEOUT_SECONDS = 10.0
REALTIME_CONNECT_TIMEOUT_SECONDS = 10.0
# No audio from the carrier for this long means the call is gone.
IDLE_SECONDS = 20.0
WRAP_UP_BEFORE_END_SECONDS = 20.0
END_PLAYBACK_TIMEOUT_SECONDS = 8.0
END_CALL_MARK = "end-call"
MAX_TRANSCRIPTS = 40
RIDER_QUOTE_CHARS = 120


class CallSocket(Protocol):
    async def receive_text(self) -> str: ...

    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


class RealtimeConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


RealtimeConnector = Callable[[], Awaitable[RealtimeConnection]]


class RealtimeUnavailable(RuntimeError):
    pass


class CallRecorder(Protocol):
    async def add_call(self, user_id: str, call: RiderCallSummary) -> Any: ...

    async def update_call(self, user_id: str, call_id: str, call: RiderCallSummary) -> bool: ...


@dataclass(frozen=True)
class BridgeConfig:
    model: str
    voice: str
    transcription_model: str
    max_call_seconds: float
    owner_name: str | None
    language: str
    # The call the stream URL was issued for (signed path); any other is refused.
    expected_call_id: str = ""
    # Masked caller number from the signed answer webhook.
    caller: str = "unknown"
    idle_seconds: float = IDLE_SECONDS
    start_timeout_seconds: float = START_TIMEOUT_SECONDS
    connect_timeout_seconds: float = REALTIME_CONNECT_TIMEOUT_SECONDS
    end_playback_timeout_seconds: float = END_PLAYBACK_TIMEOUT_SECONDS


def session_update(config: BridgeConfig, plan: AudioPlan = MULAW_PLAN) -> dict[str, Any]:
    return {
        "type": "session.update",
        "event_id": "rider_session",
        "session": {
            "type": "realtime",
            "model": config.model,
            "output_modalities": ["audio"],
            "instructions": rider_instructions(config.owner_name, config.language),
            "audio": {
                "input": {
                    "format": dict(plan.realtime_format),
                    "transcription": {"model": config.transcription_model},
                    "turn_detection": {
                        "type": "server_vad",
                        "create_response": True,
                        "interrupt_response": True,
                        "silence_duration_ms": 600,
                    },
                },
                "output": {
                    "format": dict(plan.realtime_format),
                    "voice": config.voice,
                },
            },
            "tools": RIDER_TOOL_DEFINITIONS,
            "tool_choice": "auto",
        },
    }


def greeting_request(config: BridgeConfig) -> dict[str, Any]:
    line = greeting(config.owner_name, config.language)
    return {
        "type": "response.create",
        "event_id": "rider_greeting",
        "response": {
            "instructions": f'Say exactly this, nothing more, then wait for the caller: "{line}"'
        },
    }


@dataclass
class CallState:
    started_at: datetime
    caller: str = "unknown"
    rider_lines: list[str] = field(default_factory=list)
    assistant_lines: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    outcome: str | None = None
    call_id: str | None = None


class RiderCallBridge:
    def __init__(
        self,
        socket: CallSocket,
        adapter: TelephonyAdapter,
        connect_realtime: RealtimeConnector,
        toolbox: RiderToolbox,
        config: BridgeConfig,
        recorder: CallRecorder,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.socket = socket
        self.adapter = adapter
        self.connect_realtime = connect_realtime
        self.toolbox = toolbox
        self.config = config
        self.recorder = recorder
        self.clock = clock
        self.now = now
        self.state = CallState(started_at=now(), caller=config.caller)
        self.toolbox.on_notify = self._deliver_note
        self.stream_id = ""
        self.call_id = ""
        self.plan: AudioPlan = MULAW_PLAN
        self.realtime: RealtimeConnection | None = None
        self._send_lock = asyncio.Lock()
        self._done = asyncio.Event()
        self._end_played = asyncio.Event()
        self._end_reason: str | None = None
        self._socket_closed = False
        self._chunker: OutboundChunker | None = None
        self._to_realtime: Resampler | None = None
        # Playback bookkeeping for barge-in (clear + truncate).
        self._speaking_item: str | None = None
        self._speaking_response: str | None = None
        self._item_sent_bytes = 0
        self._item_started_at: float | None = None
        self._dropped_responses: set[str] = set()
        self.sent_chunks: list[int] = []

    # ---- lifecycle -------------------------------------------------------

    async def run(self) -> RiderCallSummary | None:
        start = await self._await_start()
        if start is None:
            await self._close_socket()
            return None
        if self.config.expected_call_id and start.call_id != self.config.expected_call_id:
            logger.warning("event=rider_call_refused reason=call_id_mismatch")
            await self._close_socket(1008)
            return None
        self.stream_id = start.stream_id
        self.call_id = start.call_id
        self.plan = start.audio
        frame = self.plan.outbound_bytes_per_ms * self.adapter.frame_ms
        self._chunker = OutboundChunker(frame, self.plan.outbound_sample_bytes)
        if self.plan.resample_inbound:
            self._to_realtime = Resampler(self.plan.inbound_rate, REALTIME_PCM_RATE)
        logger.info(
            "event=rider_call_start provider=%s audio=%s/%s prompt=%s caller=%s",
            self.adapter.name,
            self.plan.inbound_encoding,
            self.plan.inbound_rate,
            RIDER_PROMPT_VERSION,
            self.state.caller,
        )
        try:
            self.realtime = await asyncio.wait_for(
                self.connect_realtime(), self.config.connect_timeout_seconds
            )
            await self._send_realtime(session_update(self.config, self.plan))
            await self._send_realtime(greeting_request(self.config))
        except Exception as exc:  # noqa: BLE001 - any failure here means no assistant
            logger.warning("event=rider_realtime_unavailable error=%s", type(exc).__name__)
            self.state.outcome = "assistant_unavailable"
            await self._close_realtime()
            await self._stop_stream()
            return await self._finish()

        tasks = [
            asyncio.create_task(self._socket_loop()),
            asyncio.create_task(self._realtime_loop()),
            asyncio.create_task(self._deadline()),
        ]
        try:
            await self._done.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._close_realtime()
            await self._stop_stream()
        return await self._finish()

    def _stop(self, reason: str) -> None:
        if self._end_reason is None:
            self._end_reason = reason
        self._done.set()

    async def _await_start(self) -> StreamStart | None:
        deadline = self.clock() + self.config.start_timeout_seconds
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(self.socket.receive_text(), remaining)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - disconnect or timeout
                self._socket_closed = True
                return None
            try:
                event = self.adapter.parse(raw)
            except ProtocolError:
                continue
            if event.kind == "start" and event.start is not None:
                return event.start
            if event.kind == "stop":
                return None

    async def _deadline(self) -> None:
        cap = self.config.max_call_seconds
        wrap_at = max(0.0, cap - WRAP_UP_BEFORE_END_SECONDS)
        await asyncio.sleep(wrap_at)
        try:
            await self._send_realtime(
                {"type": "response.create", "event_id": "rider_wrap_up", "response": {"instructions": WRAP_UP}}
            )
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(cap - wrap_at)
        logger.info("event=rider_call_cap seconds=%s", cap)
        self._stop("time_limit")

    # ---- call → Realtime -------------------------------------------------

    async def _socket_loop(self) -> None:
        while not self._done.is_set():
            try:
                raw = await asyncio.wait_for(self.socket.receive_text(), self.config.idle_seconds)
            except asyncio.TimeoutError:
                self._stop("idle")
                return
            except Exception:  # noqa: BLE001 - the carrier closed the socket (hang-up)
                self._socket_closed = True
                self._stop("caller_left")
                return
            try:
                event = self.adapter.parse(raw)
            except ProtocolError:
                continue
            await self._on_call_event(event)

    async def _on_call_event(self, event: TelephonyEvent) -> None:
        if event.kind == "media" and event.audio:
            audio = self._to_realtime.process(event.audio) if self._to_realtime else event.audio
            if audio:
                await self._send_realtime(
                    {"type": "input_audio_buffer.append", "audio": base64.b64encode(audio).decode("ascii")}
                )
        elif event.kind == "played" and event.name == END_CALL_MARK:
            self._end_played.set()
        elif event.kind == "stop":
            self._stop("stopped")

    # ---- Realtime → call -------------------------------------------------

    async def _realtime_loop(self) -> None:
        assert self.realtime is not None
        while not self._done.is_set():
            try:
                raw = await self.realtime.recv()
            except Exception as exc:  # noqa: BLE001 - OpenAI closed the socket
                logger.warning("event=rider_realtime_closed error=%s", type(exc).__name__)
                if self.state.outcome is None and self._end_reason is None:
                    self.state.outcome = "ended_early"
                self._stop("realtime_closed")
                return
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(event, dict):
                await self._on_realtime(event)

    async def _on_realtime(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind in {"response.output_audio.delta", "response.audio.delta"}:
            await self._play(event)
        elif kind in {"response.output_audio.done", "response.audio.done"}:
            await self._flush_audio()
        elif kind == "input_audio_buffer.speech_started":
            await self._barge_in()
        elif kind == "conversation.item.input_audio_transcription.completed":
            self._remember(self.state.rider_lines, event.get("transcript"), rider=True)
        elif kind in {"response.output_audio_transcript.done", "response.audio_transcript.done"}:
            self._remember(self.state.assistant_lines, event.get("transcript"), rider=False)
        elif kind == "response.done":
            await self._flush_audio()
            await self._on_response_done(event.get("response"))
        elif kind == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            # Code and type only: messages can echo caller content.
            logger.warning(
                "event=rider_realtime_error code=%s type=%s", error.get("code"), error.get("type")
            )

    async def _play(self, event: dict[str, Any]) -> None:
        response_id = event.get("response_id")
        if isinstance(response_id, str) and response_id in self._dropped_responses:
            return
        delta = event.get("delta")
        if not isinstance(delta, str) or self._chunker is None:
            return
        try:
            audio = base64.b64decode(delta, validate=True)
        except (binascii.Error, ValueError):
            return
        item_id = event.get("item_id")
        if isinstance(item_id, str) and item_id != self._speaking_item:
            self._speaking_item = item_id
            self._item_sent_bytes = 0
            self._item_started_at = None
        if isinstance(response_id, str):
            self._speaking_response = response_id
        # Realtime output is already in the outbound format (μ-law 8 kHz or PCM 24 kHz).
        for chunk in self._chunker.push(audio):
            await self._send_audio(chunk)

    async def _flush_audio(self) -> None:
        if self._chunker is None:
            return
        for chunk in self._chunker.flush():
            await self._send_audio(chunk)

    async def _send_audio(self, chunk: bytes) -> None:
        now = self.clock()
        if self._item_started_at is None:
            self._item_started_at = now
        self._item_sent_bytes += len(chunk)
        self.sent_chunks.append(len(chunk))
        await self._send_socket(self.adapter.play(self.stream_id, self.plan, chunk))

    def _sent_ms(self) -> int:
        return self._item_sent_bytes // max(1, self.plan.outbound_bytes_per_ms)

    def _playback_ms(self) -> int:
        """Audio of the current item the caller has heard so far (estimate)."""

        if self._item_started_at is None:
            return 0
        elapsed = int((self.clock() - self._item_started_at) * 1000)
        return max(0, min(self._sent_ms(), elapsed))

    def _assistant_audible(self) -> bool:
        if self._item_started_at is None:
            return False
        return (self.clock() - self._item_started_at) * 1000 < self._sent_ms() + 250

    async def _barge_in(self) -> None:
        if self._chunker is not None:
            self._chunker.drop()
        if not self._assistant_audible():
            return
        await self._send_socket(self.adapter.clear(self.stream_id))
        if self._speaking_response:
            self._dropped_responses.add(self._speaking_response)
        if self._speaking_item:
            await self._send_realtime(
                {
                    "type": "conversation.item.truncate",
                    "item_id": self._speaking_item,
                    "content_index": 0,
                    "audio_end_ms": self._playback_ms(),
                }
            )
        self._speaking_item = None
        self._item_started_at = None
        self._item_sent_bytes = 0

    async def _on_response_done(self, response: Any) -> None:
        if not isinstance(response, dict):
            return
        calls = [
            item
            for item in response.get("output") or []
            if isinstance(item, dict) and item.get("type") == "function_call"
        ]
        if not calls:
            return
        end = False
        for item in calls:
            call_id = item.get("call_id")
            name = item.get("name") if isinstance(item.get("name"), str) else ""
            arguments = item.get("arguments") if isinstance(item.get("arguments"), str) else ""
            if not isinstance(call_id, str):
                continue
            result = await self.toolbox.execute(name, arguments)
            end = end or result.end_call
            await self._send_realtime(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result.output, ensure_ascii=False),
                    },
                }
            )
        if end:
            await self._end_after_playback()
        else:
            await self._send_realtime({"type": "response.create"})

    async def _end_after_playback(self) -> None:
        """Let the goodbye play (checkpoint → playedStream, with a timeout), then stop."""

        await self._flush_audio()
        await self._send_socket(self.adapter.checkpoint(self.stream_id, END_CALL_MARK))
        try:
            await asyncio.wait_for(self._end_played.wait(), self.config.end_playback_timeout_seconds)
        except asyncio.TimeoutError:
            pass
        self._stop("end_call")

    # ---- transport helpers ----------------------------------------------

    async def _send_realtime(self, event: dict[str, Any]) -> None:
        if self.realtime is None:
            raise RealtimeUnavailable("Realtime is not connected.")
        await self.realtime.send(json.dumps(event, ensure_ascii=False))

    async def _send_socket(self, message: str) -> None:
        if self._socket_closed:
            return
        async with self._send_lock:
            try:
                await self.socket.send_text(message)
            except Exception:  # noqa: BLE001 - caller hung up
                self._socket_closed = True
                self._stop("caller_left")

    async def _close_realtime(self) -> None:
        if self.realtime is not None:
            try:
                await self.realtime.close()
            except Exception:  # noqa: BLE001
                pass

    async def _stop_stream(self) -> None:
        """Tell the carrier the stream is over (its next XML runs), then close."""

        if not self._socket_closed and self.stream_id:
            message = self.adapter.stop(self.stream_id)
            if message:
                await self._send_socket(message)
        await self._close_socket()

    async def _close_socket(self, code: int = 1000) -> None:
        if self._socket_closed:
            return
        self._socket_closed = True
        try:
            await self.socket.close(code)
        except Exception:  # noqa: BLE001
            pass

    # ---- summary ---------------------------------------------------------

    def _remember(self, lines: list[str], text: Any, *, rider: bool) -> None:
        if not isinstance(text, str):
            return
        cleaned = " ".join(text.split())
        if not cleaned or len(lines) >= MAX_TRANSCRIPTS:
            return
        if rider:
            self.state.flags |= request_flags(cleaned)
        lines.append(redact_secrets(cleaned)[:300])

    async def _deliver_note(self, note: OwnerNote) -> bool:
        """notify_owner: the owner's app shows it on its next poll (no call, no push)."""

        try:
            summary = self.summary(outcome="in_progress")
            if self.state.call_id is None:
                saved = await self.recorder.add_call(self.toolbox.user_id, summary)
                self.state.call_id = getattr(saved, "id", None)
            else:
                await self.recorder.update_call(self.toolbox.user_id, self.state.call_id, summary)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=rider_notify_failed error=%s", type(exc).__name__)
            return False

    def _outcome(self) -> str:
        if self.state.outcome is not None:
            return self.state.outcome
        reason = self.toolbox.end_reason
        if reason == "not_delivery":
            return "not_delivery"
        if self.toolbox.used:
            return "answered"
        if self.toolbox.notifications:
            return "message_taken"
        return "answered" if reason else "ended_early"

    def summary(self, outcome: str | None = None) -> RiderCallSummary:
        outcome = outcome or self._outcome()
        notes = self.toolbox.notifications
        urgency = "normal"
        if any(note.urgency == "high" for note in notes):
            urgency = "high"
        elif notes and all(note.urgency == "low" for note in notes):
            urgency = "low"
        parts: list[str] = [note.summary for note in notes]
        if "otp" in self.state.flags:
            parts.append("Asked for an OTP or code; it was not shared.")
        if "money" in self.state.flags:
            parts.append("Asked about payment or charges; nothing was agreed.")
        if "order_change" in self.state.flags:
            parts.append("Asked to change the order; nothing was changed.")
        if outcome == "assistant_unavailable":
            parts.insert(0, "The assistant couldn't answer this call (OpenAI was unreachable). Call the rider back if you can.")
        elif outcome == "ended_early" and not parts:
            parts.append("The call ended before anything was answered.")
        if not parts:
            used = self.toolbox.used
            parts.append(
                "Answered from your notes and order."
                if used == {"notes", "order"}
                else "Answered from your notes."
                if "notes" in used
                else "Answered from your order."
                if "order" in used
                else "No question was asked."
            )
        delivery: bool | None = None
        if self.toolbox.end_reason == "not_delivery":
            delivery = False
        elif self.toolbox.used:
            delivery = True
        rider_said = next((line for line in self.state.rider_lines if len(line) >= 4), None)
        return RiderCallSummary(
            started_at=self.state.started_at,
            ended_at=None if outcome == "in_progress" else self.now(),
            caller=self.state.caller,
            delivery_call=delivery,
            outcome=outcome,  # type: ignore[arg-type]
            urgency=urgency,  # type: ignore[arg-type]
            rider_said=rider_said[:RIDER_QUOTE_CHARS] if rider_said else None,
            summary=" ".join(parts)[:600],
            answered_from=sorted(self.toolbox.used),  # type: ignore[arg-type]
        )

    async def _finish(self) -> RiderCallSummary:
        final = self.summary()
        try:
            if self.state.call_id is None:
                await self.recorder.add_call(self.toolbox.user_id, final)
            else:
                await self.recorder.update_call(self.toolbox.user_id, self.state.call_id, final)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=rider_summary_failed error=%s", type(exc).__name__)
        logger.info(
            "event=rider_call_end outcome=%s reason=%s chunks=%s",
            final.outcome,
            self._end_reason,
            len(self.sent_chunks),
        )
        return final
