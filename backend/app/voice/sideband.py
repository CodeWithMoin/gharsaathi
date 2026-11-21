import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from app.core.config import Settings
from app.voice.tools import ShoppingToolExecutor


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
    ) -> None:
        self.connection = connection
        self.executor = executor
        self.publish = publish
        self.log_event_types = log_event_types
        self._seen_event_ids: set[str] = set()
        self._handled_call_ids: set[str] = set()
        self._pending: dict[str, list[asyncio.Task[None]]] = {}
        self._continuations: set[asyncio.Task[None]] = set()
        self._round = 0
        self._send_lock = asyncio.Lock()

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
        for task in [*self._continuations, *(t for ts in self._pending.values() for t in ts)]:
            task.cancel()

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

        if inner_type == "response.output_item.done":
            item = inner.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                self._start_call(delegation_id, item)
        elif inner_type in {"response.completed", "response.incomplete", "response.failed"}:
            tasks = self._pending.pop(delegation_id, [])
            if tasks:
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
        task = asyncio.create_task(
            self._run_call(
                call_id,
                name if isinstance(name, str) else "",
                arguments if isinstance(arguments, str) else "",
            )
        )
        self._pending.setdefault(delegation_id, []).append(task)

    async def _run_call(self, call_id: str, name: str, arguments: str) -> None:
        self.publish({"type": "tool.started", "name": name})
        try:
            outcome = await self.executor.execute(name, arguments)
            model_output = outcome.model_output
            if outcome.ui_event is not None:
                self.publish(outcome.ui_event)
        except Exception:
            logger.exception("Voice tool %s failed", name)
            model_output = {"status": "error", "message": "The tool failed unexpectedly."}
            self.publish({"type": "tool.error", "message": "Search mein kuch gadbad ho gayi. Ek baar phir boliye."})
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
        await asyncio.gather(*tasks, return_exceptions=True)
        self._round += 1
        await self.send({"type": "response.create", "event_id": f"continue_{delegation_id}_{self._round}"})


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

    async def _attach(
        self, session_id: str, executor: ShoppingToolExecutor, channel: SessionChannel
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
