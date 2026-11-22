import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, Field

from app.conversations.context import (
    ConversationContextReader,
    RecentConversationContext,
    context_instructions_block,
    live_input_items,
)
from app.core.config import Settings
from app.memory.recall import HouseholdMemory, PreferenceRecaller
from app.memory.remembered import memory_summary_block
from app.user_profile import ProfileReader, UserProfile
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.shopping.order_confirmation import OrderConfirmations
from app.voice.address_book_tools import AddressBook
from app.voice.address_prefill import RecentUserWords
from app.voice.address_tools import ZeptoConnectionChecker
from app.voice.profile_context import with_profile
from app.voice.basket_tools import AddressDescriber
from app.commerce.zepto.payments import prepaid_payments_offered
from app.voice.photo_tools import PhotoReading
from app.voice.prompts import BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS, payment_instructions
from app.voice.sideband import SidebandHub
from app.voice.toolbox import ToolExecutor, VOICE_TOOL_DEFINITIONS, VoiceToolbox
from app.voice.order_status_tools import OrderHistoryReader
from app.tracking.service import OrderTrackingService
from app.voice.turn_taking import client_turn_taking, live_instructions
from app.voice.tools import (
    TOOL_DEFINITIONS,
    MultiProviderShoppingToolExecutor,
    ProductSearcher,
    ProviderSearchTarget,
    ShoppingToolExecutor,
)


logger = logging.getLogger(__name__)
T = TypeVar("T")

# Typed chat waits up to 2 s for chat context; voice start never waits long,
# because without context the session simply starts blank.
CONTEXT_TIMEOUT_SECONDS = 2.0
VOICE_START_CONTEXT_TIMEOUT_SECONDS = 0.3
# Profile and memory are small reads; the voice cap only matters when the database stalls.
VOICE_START_READ_TIMEOUT_SECONDS = 1.0
# The shared OpenAI connection is kept open between session starts and prewarms.
KEEPALIVE_SECONDS = 120.0
PREWARM_INTERVAL_SECONDS = 30.0


class VoiceError(Exception):
    """Base error for the voice session boundary."""


class VoiceConfigurationError(VoiceError):
    pass


class VoiceProviderError(VoiceError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class LiveSession(BaseModel):
    session_id: str
    sdp: str
    turn_taking: dict[str, str] = Field(default_factory=lambda: {"mode": "native"})
    # Capability for GET /api/voice/sessions/{id}/events, bound to the creating
    # user. EventSource cannot send Authorization headers, so it is passed as
    # the ``token`` query parameter (redacted from access logs).
    events_token: str
    # GPT-Live output transcripts transcribe the spoken audio, so Hindi words can
    # arrive in Devanagari. "roman" lets the client show them in Roman Hinglish;
    # "native" (Hindi chosen in the profile) leaves captions untouched.
    assistant_transcript_script: Literal["roman", "native"] = "roman"
    # Content-free stage timings (ms), returned only when VOICE_EVENT_LOG is on.
    start_timing_ms: dict[str, float] | None = None


def build_session_config(
    settings: Settings,
    tool_definitions: list[dict[str, Any]] | None = None,
    profile: UserProfile | None = None,
    household_memory: HouseholdMemory | None = None,
    context: RecentConversationContext | None = None,
) -> dict[str, Any]:
    """GPT-Live session config.

    Earlier turns of this chat seed GPT-Live through ``input`` (startup history,
    https://developers.openai.com/api/docs/guides/live-conversations#seed-a-session-with-prior-conversation);
    the summary, on-screen state and previous chats go in its instructions as
    quoted reference data. The backend model gets the whole context block.
    """

    live_prompt, backend_prompt = payment_instructions(
        LIVE_INSTRUCTIONS, BACKEND_INSTRUCTIONS, prepaid=prepaid_payments_offered(settings)
    )
    session: dict[str, Any] = {
        "model": settings.live_model,
        "instructions": with_profile(
            live_instructions(live_prompt, settings.voice_turn_taking), profile
        )
        + context_instructions_block(context, include_turns=False),
        "audio": {"output": {"voice": settings.live_voice}},
        "delegation": {
            "type": "responses",
            "responses": {
                "model": settings.live_backend_model,
                "instructions": with_profile(backend_prompt, profile)
                + memory_summary_block(household_memory)
                + context_instructions_block(context),
                "tools": tool_definitions if tool_definitions is not None else TOOL_DEFINITIONS,
                "tool_choice": "auto",
                "parallel_tool_calls": True,
            },
        },
    }
    history = live_input_items(context)
    if history:
        session["input"] = history
    return session


class LiveSessionService:
    def __init__(
        self,
        settings: Settings,
        searcher: ProductSearcher,
        hub: SidebandHub,
        http_client: httpx.AsyncClient | None = None,
        *,
        baskets: BasketService | None = None,
        approvals: ApprovalService | None = None,
        zepto_searcher: ProductSearcher | None = None,
        zepto_connection: ZeptoConnectionChecker | None = None,
        preference_recall: PreferenceRecaller | None = None,
        profile_reader: ProfileReader | None = None,
        addresses: AddressDescriber | None = None,
        context_reader: ConversationContextReader | None = None,
        order_confirmations: OrderConfirmations | None = None,
        address_book: AddressBook | None = None,
        photo_reader: PhotoReading | None = None,
        order_tracking: OrderTrackingService | None = None,
        order_history: OrderHistoryReader | None = None,
    ) -> None:
        self.settings = settings
        self.searcher = searcher
        self.hub = hub
        self.baskets = baskets
        self.approvals = approvals
        self.zepto_searcher = zepto_searcher
        self.zepto_connection = zepto_connection
        self.preference_recall = preference_recall
        self.profile_reader = profile_reader
        self.addresses = addresses
        self.context_reader = context_reader
        self.order_confirmations = order_confirmations
        self.address_book = address_book
        self.photo_reader = photo_reader
        self.order_tracking = order_tracking
        self.order_history = order_history
        self._http_client = http_client
        # One kept-alive connection to OpenAI per event loop, so a session start
        # does not pay for a fresh TLS handshake (and client setup) every time.
        self._shared_client: httpx.AsyncClient | None = None
        self._shared_loop: asyncio.AbstractEventLoop | None = None
        self._last_openai_use = float("-inf")
        self._warmups: set[asyncio.Task[bool]] = set()

    def _warmup_finished(self, task: "asyncio.Task[bool]") -> None:
        self._warmups.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Voice connection warm-up failed: %s", type(task.exception()).__name__)

    @property
    def basket_enabled(self) -> bool:
        return self.baskets is not None and self.approvals is not None

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        return VOICE_TOOL_DEFINITIONS if self.basket_enabled else TOOL_DEFINITIONS

    def tool_executor(
        self, address_id: str | None, zepto_address_id: str | None, household_id: str
    ) -> ToolExecutor:
        if self.basket_enabled:
            # narrowed by basket_enabled
            assert self.baskets is not None
            assert self.approvals is not None
            return VoiceToolbox(
                self.searcher,
                self.baskets,
                self.approvals,
                address_id,
                self.zepto_searcher,
                zepto_address_id,
                household_id,
                zepto_connection=self.zepto_connection,
                preference_recall=self.preference_recall,
                addresses=self.addresses,
                order_confirmations=self.order_confirmations,
                address_book=self.address_book,
                user_words=RecentUserWords(lambda: self.saved_user_words(household_id)),
                photo_reader=self.photo_reader,
                order_tracking=self.order_tracking,
                order_history=self.order_history,
            )
        if self.zepto_searcher is not None:
            return MultiProviderShoppingToolExecutor(
                [
                    ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", self.searcher, address_id, True),
                    ProviderSearchTarget("zepto", "Zepto", self.zepto_searcher, zepto_address_id),
                ]
            )
        return ShoppingToolExecutor(self.searcher, address_id)

    async def saved_user_words(self, household_id: str) -> str:
        """The user's own turns in the saved current chat (server-side, never the model's)."""

        context = await self.conversation_context(household_id)
        if context is None:
            return ""
        return " ".join(turn.text for turn in context.turns if turn.role == "user")

    async def profile(
        self, household_id: str, timeout: float | None = None
    ) -> UserProfile | None:
        if self.profile_reader is None or not household_id:
            return None
        try:
            return await asyncio.wait_for(self.profile_reader.read(household_id), timeout)
        except Exception as exc:  # noqa: BLE001 - a missing profile must not block voice
            logger.warning("Voice profile read failed: %s", type(exc).__name__)
            return None

    async def household_memory(
        self, household_id: str, timeout: float | None = None
    ) -> HouseholdMemory | None:
        reader = getattr(self.preference_recall, "household_memory", None)
        if not callable(reader) or not household_id:
            return None
        try:
            return await asyncio.wait_for(reader(household_id), timeout)
        except Exception as exc:  # noqa: BLE001 - memory must not block voice
            logger.warning("Voice memory summary failed: %s", type(exc).__name__)
            return None

    async def conversation_context(
        self, household_id: str, timeout: float = CONTEXT_TIMEOUT_SECONDS
    ) -> RecentConversationContext | None:
        if self.context_reader is None or not household_id:
            return None
        try:
            return await asyncio.wait_for(self.context_reader.load(household_id), timeout)
        except Exception as exc:  # noqa: BLE001 - missing context must not block voice
            logger.warning("Voice chat context read failed: %s", type(exc).__name__)
            return None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        loop = asyncio.get_running_loop()
        if self._shared_client is None or self._shared_client.is_closed or self._shared_loop is not loop:
            self._shared_client = httpx.AsyncClient(
                timeout=30.0, limits=httpx.Limits(keepalive_expiry=KEEPALIVE_SECONDS)
            )
            self._shared_loop = loop
            self._last_openai_use = float("-inf")
        return self._shared_client

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        response = await self._client().post(url, **kwargs)
        self._last_openai_use = time.monotonic()
        return response

    def _connection_is_warm(self) -> bool:
        return time.monotonic() - self._last_openai_use < PREWARM_INTERVAL_SECONDS

    async def prewarm(self) -> bool:
        """Open the OpenAI connection ahead of a session start.

        The request carries no API key and no user data; any response (an
        unauthenticated 401) leaves a kept-alive TLS connection in the pool.
        """

        if self._http_client is not None or self._connection_is_warm():
            return False
        if not self.settings.openai_api_key.get_secret_value().strip():
            return False
        client = self._client()
        self._last_openai_use = time.monotonic()
        try:
            await client.get(f"{self.settings.openai_base_url.rstrip('/')}/models", timeout=5.0)
        except httpx.HTTPError:
            self._last_openai_use = float("-inf")
            return False
        return True

    async def aclose(self) -> None:
        if self._shared_client is not None:
            await self._shared_client.aclose()
            self._shared_client = None

    async def create(
        self,
        offer_sdp: str,
        swiggy_address_id: str | None = None,
        zepto_address_id: str | None = None,
        household_id: str = "",
        data_channel_id: int | None = None,
    ) -> LiveSession:
        api_key = self.settings.openai_api_key.get_secret_value().strip()
        if not api_key:
            raise VoiceConfigurationError("Set OPENAI_API_KEY in .env to enable voice.")
        timings: dict[str, float] = {}
        started = time.perf_counter()
        # The warm-up overlaps the reads. If it has finished, the POST reuses its
        # connection; if not, the POST opens its own instead of waiting for it.
        warm = asyncio.create_task(_timed(timings, "connect", self.prewarm()))
        self._warmups.add(warm)
        warm.add_done_callback(self._warmup_finished)
        profile, household_memory, context = await asyncio.gather(
            _timed(timings, "profile", self.profile(household_id, VOICE_START_READ_TIMEOUT_SECONDS)),
            _timed(
                timings,
                "memory",
                self.household_memory(household_id, VOICE_START_READ_TIMEOUT_SECONDS),
            ),
            _timed(
                timings,
                "context",
                self.conversation_context(household_id, VOICE_START_CONTEXT_TIMEOUT_SECONDS),
            ),
        )
        timings["reads"] = _ms_since(started)
        mark = time.perf_counter()
        session_config = build_session_config(
            self.settings,
            self.tool_definitions,
            profile,
            household_memory if self.basket_enabled else None,
            context=context,
        )
        transport: dict[str, Any] = {"type": "webrtc", "sdp": offer_sdp}
        if data_channel_id is not None:
            # Pre-negotiated data channel (WARP), see
            # https://developers.openai.com/api/docs/guides/realtime-webrtc-warp#connect-to-gpt-live
            transport["dcid"] = data_channel_id
        timings["config"] = _ms_since(mark)
        mark = time.perf_counter()
        try:
            response = await self._post(
                f"{self.settings.openai_base_url.rstrip('/')}/live/sessions",
                json={"session": session_config, "transport": transport},
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.HTTPError as exc:
            raise VoiceProviderError("Could not reach OpenAI to start voice.") from exc
        timings["openai"] = _ms_since(mark)

        if response.status_code >= 400:
            raise VoiceProviderError(
                _error_message(response), 502 if response.status_code >= 500 else 400
            )
        try:
            payload = response.json()
            session_id = payload["session"]["id"]
            answer_sdp = payload["transport"]["sdp"]
        except (ValueError, KeyError, TypeError) as exc:
            raise VoiceProviderError("OpenAI returned an unexpected session response.") from exc
        if not isinstance(session_id, str) or not isinstance(answer_sdp, str):
            raise VoiceProviderError("OpenAI returned an unexpected session response.")
        events_token = secrets.token_urlsafe(32)
        self.hub.start(
            session_id,
            self.tool_executor(swiggy_address_id, zepto_address_id, household_id),
            owner_id=household_id,
            events_token=events_token,
        )
        timings["total"] = _ms_since(started)
        if self.settings.voice_event_log:
            logger.warning(
                "event=voice_session_start_timing %s",
                " ".join(f"{name}_ms={value:.0f}" for name, value in timings.items()),
            )
        return LiveSession(
            session_id=session_id,
            sdp=answer_sdp,
            turn_taking=client_turn_taking(self.settings.voice_turn_taking),
            events_token=events_token,
            assistant_transcript_script=(
                "native" if profile is not None and profile.language == "hindi" else "roman"
            ),
            start_timing_ms=dict(timings) if self.settings.voice_event_log else None,
        )


async def _timed(timings: dict[str, float], name: str, work: Awaitable[T]) -> T:
    started = time.perf_counter()
    try:
        return await work
    finally:
        timings[name] = _ms_since(started)


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        message = error.get("message") if isinstance(error, dict) else None
    except ValueError:
        message = None
    if isinstance(message, str) and message:
        return f"Voice session could not start: {message}"
    return f"Voice session could not start (HTTP {response.status_code})."
