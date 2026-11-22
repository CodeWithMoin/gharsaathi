"""Typed chat through the same backend assistant, tools and checks as voice.

One HTTP request runs one bounded turn: the voice delegation instructions
(``build_session_config``), the voice tool definitions and a ``VoiceToolbox``
built exactly as a voice session builds it. Tool results the browser needs are
returned as the same UI events the voice sideband publishes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.core.config import Settings
from app.voice.service import LiveSessionService, build_session_config
from app.voice.toolbox import ToolExecutor


logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
MAX_SEARCHES_PER_TURN = 2
MAX_HISTORY_MESSAGES = 12
# Rough token budget for prior turns (~4 characters per token).
MAX_HISTORY_CHARS = 6_000
MAX_SEARCH_CONTEXTS = 2
MAX_SESSIONS = 200
SESSION_IDLE_SECONDS = 60 * 60

SEARCH_TOOL = "search_products"
SEARCH_LIMIT_OUTPUT = {
    "status": "error",
    "message": (
        f"Search limit reached: at most {MAX_SEARCHES_PER_TURN} searches per message. "
        "Answer with the results you already have, or ask the user one short question."
    ),
}
TOOL_FAILED_EVENT = {
    "type": "tool.error",
    "message": "Search mein kuch gadbad ho gayi. Ek baar phir likhiye.",
}
EMPTY_REPLY = "Maaf kijiye, abhi jawab nahi bana paayi. Ek baar phir likhiye."
SEARCH_CONTEXT_HEADER = (
    "Search results already shown to the user earlier in this chat (tool output, "
    "newest last). Their option_id values are still valid for add_to_basket."
)


class ChatError(Exception):
    """Base error for the typed chat boundary; messages are safe to show."""


class ChatConfigurationError(ChatError):
    pass


class ChatProviderError(ChatError):
    pass


class ChatTimeout(ChatError):
    pass


class ResponsesClient(Protocol):
    """The subset of ``AsyncOpenAI().responses`` the turn loop uses."""

    async def create(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class HistoryMessage:
    role: str
    content: str


class ChatTurnResult(BaseModel):
    reply: str
    ui_events: list[dict[str, Any]]


@dataclass
class ChatSession:
    """Per-conversation state that must outlive one HTTP request.

    The toolbox keeps the opaque option ids of earlier searches, so "Dark" or
    "haan wahi" in the next message can still add the option the user saw.
    """

    toolbox: ToolExecutor
    addresses: tuple[str | None, str | None]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    recent: list[HistoryMessage] = field(default_factory=list)
    search_contexts: list[str] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)


class ChatSessionStore:
    """Bounded in-memory sessions keyed by (verified user, conversation)."""

    def __init__(self, max_sessions: int = MAX_SESSIONS, idle_seconds: float = SESSION_IDLE_SECONDS) -> None:
        self.max_sessions = max_sessions
        self.idle_seconds = idle_seconds
        self._sessions: OrderedDict[tuple[str, str], ChatSession] = OrderedDict()

    def get(self, user_id: str, conversation_id: UUID | None) -> ChatSession | None:
        self._prune()
        key = (user_id, str(conversation_id or ""))
        session = self._sessions.get(key)
        if session is None and conversation_id is not None:
            # The first message of a brand-new chat runs before the browser has
            # saved it; adopt that session once the conversation has an id.
            session = self._sessions.pop((user_id, ""), None)
            if session is not None:
                self._sessions[key] = session
        if session is not None:
            self._sessions.move_to_end(key)
            session.last_used = time.monotonic()
        return session

    def put(self, user_id: str, conversation_id: UUID | None, session: ChatSession) -> None:
        key = (user_id, str(conversation_id or ""))
        self._sessions[key] = session
        self._sessions.move_to_end(key)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.idle_seconds
        for key in [key for key, value in self._sessions.items() if value.last_used < cutoff]:
            self._sessions.pop(key, None)


def merge_history(
    saved: list[HistoryMessage], recent: list[HistoryMessage], message: str
) -> list[HistoryMessage]:
    """Saved conversation turns plus this server's recent turns, bounded.

    The browser saves the conversation shortly after each turn, so the last
    exchange may be missing from ``saved``; ``recent`` fills that gap. The
    current message is dropped from the tail if the browser already saved it.
    """

    current = HistoryMessage("user", message)
    if saved and saved[-1] == current:
        saved = saved[:-1]
    history = saved[-MAX_HISTORY_MESSAGES:]
    known = set(history)
    history += [item for item in recent if item not in known]
    history = history[-MAX_HISTORY_MESSAGES:]
    while history and sum(len(item.content) for item in history) > MAX_HISTORY_CHARS:
        history.pop(0)
    return history


class ChatTurnService:
    def __init__(
        self,
        settings: Settings,
        voice: LiveSessionService,
        responses: ResponsesClient | None = None,
        sessions: ChatSessionStore | None = None,
    ) -> None:
        self.settings = settings
        self.voice = voice
        self._responses = responses
        self.sessions = sessions or ChatSessionStore()

    def _client(self) -> ResponsesClient:
        if self._responses is None:
            api_key = self.settings.openai_api_key.get_secret_value().strip()
            if not api_key:
                raise ChatConfigurationError("Chat assistant ke liye server par OPENAI_API_KEY set nahi hai.")
            from openai import AsyncOpenAI

            self._responses = AsyncOpenAI(api_key=api_key, base_url=self.settings.openai_base_url).responses
        return self._responses

    def _session(
        self,
        user_id: str,
        conversation_id: UUID | None,
        swiggy_address_id: str | None,
        zepto_address_id: str | None,
    ) -> ChatSession:
        addresses = (swiggy_address_id, zepto_address_id)
        session = self.sessions.get(user_id, conversation_id)
        if session is not None and session.addresses == addresses:
            return session
        fresh = ChatSession(
            toolbox=self.voice.tool_executor(swiggy_address_id, zepto_address_id, user_id),
            addresses=addresses,
        )
        if session is not None:
            # A different delivery address invalidates earlier search options.
            fresh.recent = session.recent
            fresh.lock = session.lock
        self.sessions.put(user_id, conversation_id, fresh)
        return fresh

    async def run_turn(
        self,
        user_id: str,
        conversation_id: UUID | None,
        message: str,
        saved_history: list[HistoryMessage],
        swiggy_address_id: str | None = None,
        zepto_address_id: str | None = None,
    ) -> ChatTurnResult:
        if not user_id:
            raise ValueError("A verified user is required for typed chat.")
        client = self._client()
        session = self._session(user_id, conversation_id, swiggy_address_id, zepto_address_id)
        async with session.lock:
            started = time.monotonic()
            stats: dict[str, Any] = {"rounds": 0, "tools": [], "outcome": "ok"}
            try:
                async with asyncio.timeout(self.settings.chat_turn_timeout_seconds):
                    result = await self._loop(client, session, user_id, message, saved_history, stats)
            except TimeoutError as exc:
                stats["outcome"] = "timeout"
                raise ChatTimeout("Jawab mein bahut der ho gayi. Ek baar phir likhiye.") from exc
            except ChatError:
                stats["outcome"] = "provider_error"
                raise
            finally:
                # Content-free diagnostics: never message text, arguments or outputs.
                logger.info(
                    "Chat turn %s: rounds=%d tools=%s latency_ms=%d",
                    stats["outcome"],
                    stats["rounds"],
                    ",".join(stats["tools"]) or "-",
                    int((time.monotonic() - started) * 1000),
                )
            session.recent = (
                session.recent
                + [HistoryMessage("user", message), HistoryMessage("assistant", result.reply)]
            )[-MAX_HISTORY_MESSAGES:]
            return result

    async def _loop(
        self,
        client: ResponsesClient,
        session: ChatSession,
        user_id: str,
        message: str,
        saved_history: list[HistoryMessage],
        stats: dict[str, Any],
    ) -> ChatTurnResult:
        delegation = build_session_config(
            self.settings, self.voice.tool_definitions, await self.voice.profile(user_id)
        )["delegation"]["responses"]
        allowed = {tool["name"] for tool in delegation["tools"]}
        request_input: list[dict[str, Any]] = [
            {"role": item.role, "content": item.content}
            for item in merge_history(saved_history, session.recent, message)
        ]
        if session.search_contexts:
            request_input.append(
                {
                    "role": "developer",
                    "content": SEARCH_CONTEXT_HEADER + "\n" + "\n".join(session.search_contexts),
                }
            )
        request_input.append({"role": "user", "content": message})

        ui_events: list[dict[str, Any]] = []
        previous_response_id: str | None = None
        searches = 0
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            # After the last allowed tool round the model must answer in text.
            final = round_index == MAX_TOOL_ROUNDS
            response = await self._create(
                client,
                model=delegation["model"],
                instructions=delegation["instructions"],
                tools=delegation["tools"],
                tool_choice="none" if final else delegation["tool_choice"],
                parallel_tool_calls=delegation["parallel_tool_calls"],
                input=request_input,
                previous_response_id=previous_response_id,
            )
            previous_response_id = response.id
            calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not calls or final:
                reply = (getattr(response, "output_text", "") or "").strip()
                return ChatTurnResult(reply=reply or EMPTY_REPLY, ui_events=ui_events)
            stats["rounds"] += 1
            request_input = []
            for call in calls:
                name = call.name if isinstance(call.name, str) else ""
                stats["tools"].append(name if name in allowed else "unknown")
                if name not in allowed:
                    output: dict[str, Any] = {"status": "error", "message": f"Unknown tool {name[:64]}."}
                elif name == SEARCH_TOOL and searches >= MAX_SEARCHES_PER_TURN:
                    output = SEARCH_LIMIT_OUTPUT
                else:
                    if name == SEARCH_TOOL:
                        searches += 1
                    output = await self._execute(session, name, call.arguments, ui_events)
                request_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(output, ensure_ascii=False),
                    }
                )
        raise AssertionError("unreachable: the final round always returns")

    async def _create(self, client: ResponsesClient, **kwargs: Any) -> Any:
        try:
            return await client.create(**kwargs)
        except ChatError:
            raise
        except Exception as exc:  # noqa: BLE001 - any SDK/transport failure is a provider error
            logger.warning("Chat model call failed: %s", type(exc).__name__)
            raise ChatProviderError("Assistant se abhi jawab nahi mila. Thodi der mein phir likhiye.") from exc

    async def _execute(
        self, session: ChatSession, name: str, arguments: Any, ui_events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            outcome = await session.toolbox.execute(name, arguments if isinstance(arguments, str) else "")
        except Exception:
            logger.exception("Chat tool %s failed", name)
            ui_events.append(dict(TOOL_FAILED_EVENT))
            return {"status": "error", "message": "The tool failed unexpectedly."}
        if outcome.ui_event is not None:
            ui_events.append(outcome.ui_event)
        if name == SEARCH_TOOL and outcome.model_output.get("status") == "ok":
            session.search_contexts = (
                session.search_contexts + [json.dumps(outcome.model_output, ensure_ascii=False)]
            )[-MAX_SEARCH_CONTEXTS:]
        return outcome.model_output
