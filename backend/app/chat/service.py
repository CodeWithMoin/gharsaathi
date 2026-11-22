"""Typed chat through the same backend assistant, tools and checks as voice.

One HTTP request runs one bounded turn: the voice delegation instructions
(``build_session_config``), the voice tool definitions and a ``VoiceToolbox``
built exactly as a voice session builds it. Earlier turns, the chat summary,
on-screen state and previous chats come from the shared conversation context
loader, read server-side exactly as a voice session start reads them. Tool
results the browser needs are returned as the same UI events the voice
sideband publishes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.conversations.context import RecentConversationContext
from app.core.config import Settings
from app.voice.service import LiveSessionService, build_session_config
from app.voice.timing import collect, log_timing, ms_since
from app.voice.toolbox import ToolExecutor
from app.voice.ui_ack import UiAck, screen_report_block


logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
MAX_SEARCHES_PER_TURN = 2
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
# Appended to the voice backend instructions for this channel only. Voice gets
# nothing, so live sessions keep the speech formatting rules unchanged.
CHANNEL_CONTEXT = {
    "voice": "",
    "typed": (
        "\n## Channel: typed\n"
        "This turn was typed and your reply is shown as text, not spoken. Only the "
        "speech-formatting rules change: when you list several items or "
        "options you may use short bullet lines such as \"- AXE Dark Temptation 150 ml, "
        "₹172, Zepto\". Every other rule stays the same, including the reply language, "
        "address form, tool use and keeping replies short.\n"
    ),
}
# Typed replies are rendered together with their ui_events, so the form cannot
# be confirmed within the same turn; the browser reports it with the next turn.
TYPED_FORM_REQUESTED = (
    "The Zepto address form is being shown with this reply. Nothing was saved. Say it is "
    "opening on screen (not that it is already open), and ask the user to check the details, "
    "set the map location, type the contact number, and tap Save. Never say the address was added."
)
SEARCH_CONTEXT_HEADER = (
    "Search results already shown to the user earlier in this chat (tool output, "
    "newest last). Their option_id values are still valid for add_to_basket."
)
ORDER_UPDATES_HEADER = (
    "Order card results since the last message (from the server, after the user's tap "
    "or Cancel on the order card). Only these confirm whether an order was placed."
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


def turn_context(
    context: RecentConversationContext | None, message: str, *, current_chat: bool = True
) -> RecentConversationContext | None:
    """The shared context for this typed turn.

    The browser may already have saved the message being answered; it is the
    turn's input, so a trailing identical user turn is dropped. When the turn
    belongs to a chat other than the user's current one, only previous chats
    and baskets are kept, because the current-chat part describes another chat.
    """

    if context is None:
        return None
    turns = context.turns
    if turns and turns[-1].role == "user" and turns[-1].text.strip() == message.strip():
        turns = turns[:-1]
    if not current_chat:
        return replace(context, summary="", turns=(), on_screen=tuple(
            line for line in context.on_screen if not line.startswith("Results ")
        ))
    return replace(context, turns=turns)


async def _none() -> None:
    return None


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
            fresh.lock = session.lock
        self.sessions.put(user_id, conversation_id, fresh)
        return fresh

    async def run_turn(
        self,
        user_id: str,
        conversation_id: UUID | None,
        message: str,
        swiggy_address_id: str | None = None,
        zepto_address_id: str | None = None,
        *,
        current_chat: bool = True,
        ui_acks: list[UiAck] | None = None,
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
                    result = await self._loop(
                        client, session, user_id, message, current_chat, stats, ui_acks or []
                    )
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
            return result

    async def _loop(
        self,
        client: ResponsesClient,
        session: ChatSession,
        user_id: str,
        message: str,
        current_chat: bool,
        stats: dict[str, Any],
        ui_acks: list[UiAck] | None = None,
    ) -> ChatTurnResult:
        words = getattr(session.toolbox, "user_words", None)
        if words is not None:
            words.add(message)
        delegation = await self._delegation(user_id, "typed", message, current_chat)
        allowed = {tool["name"] for tool in delegation["tools"]}
        request_input: list[dict[str, Any]] = []
        if session.search_contexts:
            request_input.append(
                {
                    "role": "developer",
                    "content": SEARCH_CONTEXT_HEADER + "\n" + "\n".join(session.search_contexts),
                }
            )
        take_updates = getattr(getattr(session.toolbox, "order", None), "take_updates", None)
        order_updates = take_updates() if callable(take_updates) else []
        if order_updates:
            request_input.append(
                {"role": "developer", "content": ORDER_UPDATES_HEADER + "\n" + "\n".join(order_updates)}
            )
        if ui_acks:
            request_input.append({"role": "developer", "content": screen_report_block(ui_acks)})
        request_input.append({"role": "user", "content": message})

        ui_events: list[dict[str, Any]] = []
        previous_response_id: str | None = None
        searches = 0
        timing = self.settings.voice_event_log
        turn_started = time.perf_counter()
        model_ms: list[float] = []
        tool_ms: list[float] = []
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            # After the last allowed tool round the model must answer in text.
            final = round_index == MAX_TOOL_ROUNDS
            mark = time.perf_counter()
            response = await self._create(
                client,
                model=delegation["model"],
                instructions=delegation["instructions"],
                tools=delegation["tools"],
                tool_choice="none" if final else delegation["tool_choice"],
                parallel_tool_calls=delegation["parallel_tool_calls"],
                input=request_input,
                previous_response_id=previous_response_id,
                **({"reasoning": delegation["reasoning"]} if "reasoning" in delegation else {}),
            )
            model_ms.append(ms_since(mark))
            previous_response_id = response.id
            calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not calls or final:
                reply = (getattr(response, "output_text", "") or "").strip()
                if timing:
                    log_timing(
                        "chat_turn_timing",
                        {
                            "rounds": len(model_ms),
                            "model_ms": round(sum(model_ms), 1),
                            "first_model_ms": model_ms[0],
                            "final_model_ms": model_ms[-1],
                            "tools_ms": round(sum(tool_ms), 1),
                            "total_ms": ms_since(turn_started),
                        },
                    )
                return ChatTurnResult(reply=reply or EMPTY_REPLY, ui_events=ui_events)
            stats["rounds"] += 1
            jobs: list[Any] = []
            for call in calls:
                name = call.name if isinstance(call.name, str) else ""
                stats["tools"].append(name if name in allowed else "unknown")
                if name not in allowed:
                    jobs.append({"status": "error", "message": f"Unknown tool {name[:64]}."})
                elif name == SEARCH_TOOL and searches >= MAX_SEARCHES_PER_TURN:
                    jobs.append(SEARCH_LIMIT_OUTPUT)
                else:
                    if name == SEARCH_TOOL:
                        searches += 1
                    jobs.append((name, call.arguments))
            mark = time.perf_counter()
            outputs = await self._execute_round(session, jobs, ui_events)
            tool_ms.append(ms_since(mark))
            request_input = [
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(output, ensure_ascii=False),
                }
                for call, output in zip(calls, outputs)
            ]
        raise AssertionError("unreachable: the final round always returns")

    async def _execute_round(
        self, session: ChatSession, jobs: list[Any], ui_events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Run one round's tool calls: searches together, everything else in call order.

        Searches are read-only and independent, so they overlap (voice runs every
        call of a round concurrently too). Basket, memory and order tools keep
        their order. UI events keep call order either way.
        """

        outputs: list[dict[str, Any] | None] = [job if isinstance(job, dict) else None for job in jobs]
        events: list[list[dict[str, Any]]] = [[] for _ in jobs]
        searches = [index for index, job in enumerate(jobs) if isinstance(job, tuple) and job[0] == SEARCH_TOOL]

        async def run(index: int) -> None:
            name, arguments = jobs[index]
            outputs[index] = await self._execute(session, name, arguments, events[index])

        if len(searches) > 1:
            await asyncio.gather(*(run(index) for index in searches))
        for index, job in enumerate(jobs):
            if isinstance(job, tuple) and outputs[index] is None:
                await run(index)
        for batch in events:
            ui_events.extend(batch)
        return [output if output is not None else {"status": "error"} for output in outputs]

    async def _delegation(
        self,
        user_id: str,
        channel: Literal["typed", "voice"],
        message: str = "",
        current_chat: bool = True,
    ) -> dict[str, Any]:
        """The voice backend config with the same profile, memory and chat context blocks."""

        profile, household_memory, context = await asyncio.gather(
            self.voice.profile(user_id),
            self.voice.household_memory(user_id) if self.voice.basket_enabled else _none(),
            self.voice.conversation_context(user_id),
        )
        delegation = dict(
            build_session_config(
                self.settings,
                self.voice.tool_definitions,
                profile,
                household_memory,
                context=turn_context(context, message, current_chat=current_chat),
            )["delegation"]["responses"]
        )
        delegation["instructions"] += CHANNEL_CONTEXT[channel]
        return delegation

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
            with collect(self.settings.voice_event_log) as stages:
                started = time.perf_counter()
                outcome = await session.toolbox.execute(name, arguments if isinstance(arguments, str) else "")
            if self.settings.voice_event_log:
                log_timing("chat_tool_timing", {"name": name[:64], "total_ms": ms_since(started), **stages})
        except Exception:
            logger.exception("Chat tool %s failed", name)
            ui_events.append(dict(TOOL_FAILED_EVENT))
            return {"status": "error", "message": "The tool failed unexpectedly."}
        if outcome.ui_event is not None:
            ui_events.append(outcome.ui_event)
        if outcome.model_output.get("status") == "form_requested":
            outcome.model_output["message"] = TYPED_FORM_REQUESTED
        current = getattr(session.toolbox, "current_addresses", None)
        if callable(current):
            # A voice-style address switch keeps this session (and its options).
            session.addresses = current()
        searched = outcome.model_output if name == SEARCH_TOOL else outcome.model_output.get("search")
        if isinstance(searched, dict) and searched.get("status") == "ok":
            # A no_memory recall carries its own search; its options stay addable too.
            session.search_contexts = (
                session.search_contexts + [json.dumps(searched, ensure_ascii=False)]
            )[-MAX_SEARCH_CONTEXTS:]
        return outcome.model_output
