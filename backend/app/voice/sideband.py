import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from app.core.config import Settings
from app.devtools.trace import TurnTrace, start_trace
from app.voice.address_prefill import RecentUserWords
from app.voice.timing import collect, log_timing, ms_since
from app.voice.tools import ShoppingToolExecutor
from app.voice.ui_ack import UiAckRegistry, apply_ack


logger = logging.getLogger(__name__)

MAX_TRACKED_SESSIONS = 20
# How long the events token can open (or EventSource can re-open) the stream.
EVENTS_TOKEN_TTL_SECONDS = 60 * 60
FINISHED = {"type": "session.finished"}
_EVENT_ID_FIELDS = (
    "event_id",
    "client_event_id",
    "item_id",
    "response_id",
    "delegation_id",
    "call_id",
)
_EVENT_TIMING_FIELDS = ("start_ms", "end_ms", "offset_ms")
# Per delegation: tool-continuation rounds and product searches the backend
# model may use before it must answer with what it already has.
MAX_CONTINUATION_ROUNDS = 6
MAX_SEARCHES_PER_DELEGATION = 4
SEARCH_TOOL = "search_products"
SEARCH_LIMIT_OUTPUT = {
    "status": "limit_reached",
    "message": (
        "Search limit for this request reached; no new search was run. Answer the user now "
        "with the results you already have, or ask one short question."
    ),
}
# Top-level GPT-Live events carrying the user's own speech as text.
_USER_TRANSCRIPT_DELTAS = {"session.input_transcript.delta", "conversation.item.input_audio_transcription.delta"}
_USER_TRANSCRIPT_DONE = {"session.input_transcript.done", "conversation.item.input_audio_transcription.completed"}
ROUND_LIMIT_OUTPUT = {
    "status": "limit_reached",
    "message": (
        "Tool limit for this request reached; this tool was not run. Answer the user now "
        "with what you already have."
    ),
}


def describe_event(event: dict[str, Any]) -> dict[str, Any]:
    """A content-free summary of a Live event: type, id field names, timing.

    Never includes transcript text, tool arguments, outputs or id values.
    """
    summary: dict[str, Any] = {
        "type": str(event.get("type", "(no type)"))[:120],
        "ids": [field for field in _EVENT_ID_FIELDS if event.get(field) is not None],
    }
    timing = {
        field: event[field]
        for field in _EVENT_TIMING_FIELDS
        if isinstance(event.get(field), int) and not isinstance(event.get(field), bool)
    }
    if timing:
        summary["timing"] = timing
    inner = event.get("event")
    if isinstance(inner, dict) and isinstance(inner.get("type"), str):
        summary["inner"] = inner["type"][:120]
    return summary


class Connection(Protocol):
    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


@dataclass
class SessionChannel:
    """UI events for one voice session, replayed to late subscribers."""

    history: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    finished: bool = False
    task: asyncio.Task[None] | None = None
    owner_id: str = ""
    events_token_digest: bytes = b""
    events_token_expires_at: float = 0.0
    acks: UiAckRegistry = field(default_factory=UiAckRegistry)

    def publish(self, event: dict[str, Any]) -> None:
        self.history.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)

    def finish(self) -> None:
        if not self.finished:
            self.finished = True
            self.publish(FINISHED)


class SidebandRunner:
    """Handles one GPT-Live session's events from the server side.

    Executes Responses-delegated function calls with the backend's own tool
    executor, returns results to GPT-Live, then continues the backend response.
    """

    def __init__(
        self,
        connection: Connection,
        executor: ShoppingToolExecutor,
        publish: Callable[[dict[str, Any]], None],
        *,
        log_event_types: bool = False,
        acks: UiAckRegistry | None = None,
        user_words: RecentUserWords | None = None,
        trace_factory: Callable[[], TurnTrace | None] | None = None,
    ) -> None:
        self.connection = connection
        self.executor = executor
        self.publish = publish
        self.log_event_types = log_event_types
        self.acks = acks
        self.user_words = user_words
        self._seen_event_ids: set[str] = set()
        self._handled_call_ids: set[str] = set()
        self._pending: dict[str, list[asyncio.Task[None]]] = {}
        self._continuations: set[asyncio.Task[None]] = set()
        self._round = 0
        self._delegation_rounds: dict[str, int] = {}
        self._delegation_searches: dict[str, int] = {}
        self._send_lock = asyncio.Lock()
        # Content-free per-delegation stage marks (perf_counter), VOICE_EVENT_LOG only.
        self._timeline: dict[str, dict[str, Any]] = {}
        # Opt-in DEV_TRACE records per delegation: the trace, its current round and
        # when that round's model response started (perf_counter).
        self.trace_factory = trace_factory
        self._traces: dict[str, tuple[TurnTrace, list[Any]]] = {}
        # Order results from the user's tap on the order card are spoken by
        # GPT-Live through session.commentary.append (application-authored text).
        attach = getattr(getattr(executor, "order", None), "attach_sender", None)
        self._detach_order = attach if callable(attach) else None
        if self._detach_order is not None:
            self._detach_order(self.send_order_result)

    async def send_order_result(self, note: str) -> None:
        await self.send(
            {
                "type": "session.commentary.append",
                "event_id": f"order_result_{uuid.uuid4().hex[:16]}",
                "delegation_id": None,
                "content": note,
            }
        )

    async def send(self, event: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.connection.send(json.dumps(event))

    async def run(self) -> None:
        async for raw in self.connection:
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            if await self.handle(event):
                break
        if self._detach_order is not None:
            self._detach_order(None)
        for task in [*self._continuations, *(t for ts in self._pending.values() for t in ts)]:
            task.cancel()
        for delegation_id in list(self._traces):
            self._finish_trace(delegation_id, "session_closed")

    async def handle(self, event: dict[str, Any]) -> bool:
        """Process one event. Returns True when the session has closed."""
        if self.log_event_types:
            # WARNING so the opt-in diagnostic shows without app logging configuration.
            logger.warning("Voice sideband event %s", json.dumps(describe_event(event)))
        event_id = event.get("event_id")
        if isinstance(event_id, str):
            if event_id in self._seen_event_ids:
                return False
            self._seen_event_ids.add(event_id)

        event_type = event.get("type")
        if event_type == "session.closed":
            return True
        if self.user_words is not None and event_type in _USER_TRANSCRIPT_DELTAS:
            if isinstance(event.get("delta"), str):
                self.user_words.add_partial(event["delta"])
            return False
        if self.user_words is not None and event_type in _USER_TRANSCRIPT_DONE:
            if isinstance(event.get("transcript"), str):
                self.user_words.add(event["transcript"])
            return False
        if event_type == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            logger.warning("GPT-Live error: %s %s", error.get("code"), error.get("message"))
            return False
        if event_type != "response.event":
            return False

        delegation_id = event.get("delegation_id")
        inner = event.get("event")
        if not isinstance(delegation_id, str) or not isinstance(inner, dict):
            return False
        inner_type = inner.get("type")
        if self.log_event_types:
            self._mark(delegation_id, inner_type, inner)
        traced = self._trace_for(delegation_id)

        if inner_type == "response.output_item.done":
            item = inner.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                self._start_call(delegation_id, item)
        elif inner_type in {"response.completed", "response.incomplete", "response.failed"}:
            tasks = self._pending.pop(delegation_id, [])
            if traced is not None:
                trace, state = traced
                response = inner.get("response") if isinstance(inner.get("response"), dict) else {}
                trace.model_round(ms_since(state[1]), response.get("usage"), round_number=state[0])
            if not tasks and self.log_event_types:
                self._log_turn(delegation_id, str(inner_type))
            if not tasks:
                self._finish_trace(delegation_id, str(inner_type).removeprefix("response."))
            if tasks and self._delegation_rounds.get(delegation_id, 0) >= MAX_CONTINUATION_ROUNDS:
                logger.warning("event=voice_delegation_round_cap rounds=%s", MAX_CONTINUATION_ROUNDS)
                self._finish_trace(delegation_id, "round_cap")
            elif tasks:
                self._delegation_rounds[delegation_id] = (
                    self._delegation_rounds.get(delegation_id, 0) + 1
                )
                continuation = asyncio.create_task(self._continue_after(delegation_id, tasks))
                self._continuations.add(continuation)
                continuation.add_done_callback(self._continuations.discard)
        return False

    def _start_call(self, delegation_id: str, item: dict[str, Any]) -> None:
        call_id = item.get("call_id")
        name = item.get("name")
        arguments = item.get("arguments")
        if not isinstance(call_id, str) or call_id in self._handled_call_ids:
            return
        self._handled_call_ids.add(call_id)
        name = name if isinstance(name, str) else ""
        limited: dict[str, Any] | None = None
        if self._delegation_rounds.get(delegation_id, 0) >= MAX_CONTINUATION_ROUNDS:
            limited = ROUND_LIMIT_OUTPUT
        elif name == SEARCH_TOOL:
            searches = self._delegation_searches.get(delegation_id, 0)
            if searches >= MAX_SEARCHES_PER_DELEGATION:
                limited = SEARCH_LIMIT_OUTPUT
            else:
                self._delegation_searches[delegation_id] = searches + 1
        traced = self._traces.get(delegation_id)
        if limited is not None:
            if traced is not None:
                traced[0].tool(
                    name, arguments if isinstance(arguments, str) else None, None, 0.0,
                    round_number=traced[1][0], status="refused_" + str(limited.get("status")),
                )
            task = asyncio.create_task(self._send_output(call_id, limited))
        else:
            task = asyncio.create_task(
                self._run_call(
                    call_id,
                    name,
                    arguments if isinstance(arguments, str) else "",
                    traced=(traced[0], traced[1][0]) if traced is not None else None,
                )
            )
        self._pending.setdefault(delegation_id, []).append(task)

    async def _run_call(
        self, call_id: str, name: str, arguments: str, *, traced: tuple[TurnTrace, int] | None = None
    ) -> None:
        self.publish({"type": "tool.started", "name": name})
        started = time.perf_counter()
        try:
            with collect(self.log_event_types or traced is not None) as stages:
                outcome = await self.executor.execute(name, arguments)
            if self.log_event_types:
                log_timing("voice_tool_timing", {"name": name[:64], "total_ms": ms_since(started), **stages})
            if traced is not None:
                traced[0].tool(
                    name, arguments, outcome.model_output, ms_since(started),
                    round_number=traced[1], stages=stages,
                )
            model_output = outcome.model_output
            if outcome.ui_event is not None:
                self.publish(outcome.ui_event)
                ack_id = outcome.ui_event.get("ack_id")
                if isinstance(ack_id, str) and self.acks is not None:
                    # Only claim what the screen confirmed it shows.
                    model_output = apply_ack(model_output, await self.acks.wait(ack_id))
        except Exception:
            logger.exception("Voice tool %s failed", name)
            if traced is not None:
                traced[0].tool(name, arguments, None, ms_since(started), round_number=traced[1], status="exception")
            model_output = {"status": "error", "message": "The tool failed unexpectedly."}
            self.publish({"type": "tool.error", "message": "Search mein kuch gadbad ho gayi. Ek baar phir boliye."})
        await self._send_output(call_id, model_output)

    async def _send_output(self, call_id: str, model_output: dict[str, Any]) -> None:
        await self.send(
            {
                "type": "response.item.create",
                "event_id": f"result_{call_id}",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(model_output, ensure_ascii=False),
                },
            }
        )

    async def _continue_after(
        self, delegation_id: str, tasks: list[asyncio.Task[None]]
    ) -> None:
        waited = time.perf_counter()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._round += 1
        traced = self._traces.get(delegation_id)
        if traced is not None:
            traced[1][0] += 1
            traced[1][1] = time.perf_counter()
        await self.send({"type": "response.create", "event_id": f"continue_{delegation_id}_{self._round}"})
        timeline = self._timeline.get(delegation_id)
        if timeline is not None:
            # Tool time the model actually waited for after its response completed.
            timeline["tool_wait_ms"] = timeline.get("tool_wait_ms", 0.0) + ms_since(waited)
            timeline["continued_at"] = time.perf_counter()

    def _trace_for(self, delegation_id: str) -> tuple[TurnTrace, list[Any]] | None:
        traced = self._traces.get(delegation_id)
        if traced is None and self.trace_factory is not None:
            trace = self.trace_factory()
            if trace is not None:
                # [current round number, that round's start]
                traced = self._traces[delegation_id] = (trace, [1, time.perf_counter()])
        return traced

    def _finish_trace(self, delegation_id: str, outcome: str) -> None:
        traced = self._traces.pop(delegation_id, None)
        if traced is not None and not traced[0].finished:
            traced[0].finish(outcome)

    def _mark(self, delegation_id: str, inner_type: Any, inner: dict[str, Any]) -> None:
        now = time.perf_counter()
        timeline = self._timeline.setdefault(
            delegation_id, {"start": now, "rounds": 0, "continuation_ms": []}
        )
        if "continued_at" in timeline:
            timeline["continuation_ms"].append(round((now - timeline.pop("continued_at")) * 1000, 1))
            timeline["rounds"] += 1
        item = inner.get("item")
        if (
            inner_type == "response.output_item.done"
            and isinstance(item, dict)
            and item.get("type") == "function_call"
            and "first_call_ms" not in timeline
        ):
            timeline["first_call_ms"] = round((now - timeline["start"]) * 1000, 1)
        if (
            isinstance(inner_type, str)
            and inner_type.startswith("response.output_text")
            and "first_text_ms" not in timeline
        ):
            timeline["first_text_ms"] = round((now - timeline["start"]) * 1000, 1)

    def _log_turn(self, delegation_id: str, outcome: str) -> None:
        timeline = self._timeline.pop(delegation_id, None)
        if timeline is None:
            return
        fields: dict[str, Any] = {"outcome": outcome.removeprefix("response."), "rounds": timeline["rounds"] + 1}
        for key in ("first_call_ms", "tool_wait_ms", "first_text_ms"):
            if key in timeline:
                fields[key] = float(timeline[key])
        if timeline["continuation_ms"]:
            # Continuation response.create -> first backend event of the next round.
            fields["continuation_first_event_ms"] = float(sum(timeline["continuation_ms"]))
        fields["total_ms"] = ms_since(timeline["start"])
        log_timing("voice_delegation_timing", fields)


class SidebandHub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._channels: dict[str, SessionChannel] = {}

    def channel(self, session_id: str) -> SessionChannel | None:
        return self._channels.get(session_id)

    def authorized_channel(self, session_id: str, events_token: str | None) -> SessionChannel | None:
        """Return the channel only for the token issued to its creating user."""

        channel = self._channels.get(session_id)
        if channel is None or not events_token or not channel.owner_id:
            return None
        if time.monotonic() >= channel.events_token_expires_at:
            return None
        if not hmac.compare_digest(channel.events_token_digest, _token_digest(events_token)):
            return None
        return channel

    def start(
        self,
        session_id: str,
        executor: ShoppingToolExecutor,
        *,
        owner_id: str,
        events_token: str,
    ) -> SessionChannel:
        if not owner_id or not events_token:
            raise ValueError("A voice session needs a verified owner and an events token.")
        self._prune()
        channel = SessionChannel(
            owner_id=owner_id,
            events_token_digest=_token_digest(events_token),
            events_token_expires_at=time.monotonic() + EVENTS_TOKEN_TTL_SECONDS,
        )
        self._channels[session_id] = channel
        channel.task = asyncio.create_task(self._attach(session_id, executor, channel))
        return channel

    def _prune(self) -> None:
        finished = [key for key, value in self._channels.items() if value.finished]
        while len(self._channels) >= MAX_TRACKED_SESSIONS and finished:
            self._channels.pop(finished.pop(0), None)

    def owned_channel(self, session_id: str, owner_id: str) -> SessionChannel | None:
        """The channel only for the verified user who created it."""

        channel = self._channels.get(session_id)
        if channel is None or not owner_id or not channel.owner_id:
            return None
        if not hmac.compare_digest(channel.owner_id.encode(), owner_id.encode()):
            return None
        return channel

    async def _attach(
        self,
        session_id: str,
        executor: ShoppingToolExecutor,
        channel: SessionChannel,
    ) -> None:
        base = self.settings.openai_base_url.rstrip("/").replace("https://", "wss://", 1)
        url = f"{base}/live/sessions/{session_id}/attach"
        api_key = self.settings.openai_api_key.get_secret_value().strip()
        try:
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {api_key}"},
                max_size=16 * 1024 * 1024,
                open_timeout=15,
            ) as connection:
                await SidebandRunner(
                    connection,
                    executor,
                    channel.publish,
                    log_event_types=self.settings.voice_event_log,
                    acks=channel.acks,
                    # The toolbox's own buffer, so the address form checks prefill
                    # against what this session's user actually said.
                    user_words=getattr(executor, "user_words", None),
                    trace_factory=lambda: start_trace(self.settings, "voice", session_id=session_id[-12:]),
                ).run()
        except (OSError, WebSocketException, asyncio.TimeoutError) as exc:
            logger.warning("Voice sideband for %s ended: %s", session_id, type(exc).__name__)
            channel.publish(
                {
                    "type": "tool.error",
                    "message": "Voice ka server connection toot gaya. Shopping tools ruk gaye hain.",
                }
            )
        finally:
            channel.finish()


def _token_digest(token: str) -> bytes:
    # The raw token is never stored server-side.
    return hashlib.sha256(token.encode("utf-8")).digest()
