import asyncio
import logging
import secrets
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.conversations.context import (
    ConversationContextReader,
    RecentConversationContext,
    context_instructions_block,
    live_input_items,
)
from app.core.config import Settings
from app.memory.recall import PreferenceRecaller
from app.user_profile import ProfileReader, UserProfile
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.voice.address_tools import ZeptoConnectionChecker
from app.voice.profile_context import with_profile
from app.voice.basket_tools import AddressDescriber
from app.voice.prompts import BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS
from app.voice.sideband import SidebandHub
from app.voice.toolbox import ToolExecutor, VOICE_TOOL_DEFINITIONS, VoiceToolbox
from app.voice.turn_taking import client_turn_taking, live_instructions
from app.voice.tools import (
    TOOL_DEFINITIONS,
    MultiProviderShoppingToolExecutor,
    ProductSearcher,
    ProviderSearchTarget,
    ShoppingToolExecutor,
)


logger = logging.getLogger(__name__)

# Voice start never waits long for chat context; without it the session starts blank.
CONTEXT_TIMEOUT_SECONDS = 2.0


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


def build_session_config(
    settings: Settings,
    tool_definitions: list[dict[str, Any]] | None = None,
    profile: UserProfile | None = None,
    context: RecentConversationContext | None = None,
) -> dict[str, Any]:
    """GPT-Live session config.

    Earlier turns of this chat seed GPT-Live through ``input`` (startup history,
    https://developers.openai.com/api/docs/guides/live-conversations#seed-a-session-with-prior-conversation);
    the summary, on-screen state and previous chats go in its instructions as
    quoted reference data. The backend model gets the whole context block.
    """

    session: dict[str, Any] = {
        "model": settings.live_model,
        "instructions": with_profile(
            live_instructions(LIVE_INSTRUCTIONS, settings.voice_turn_taking), profile
        )
        + context_instructions_block(context, include_turns=False),
        "audio": {"output": {"voice": settings.live_voice}},
        "delegation": {
            "type": "responses",
            "responses": {
                "model": settings.live_backend_model,
                "instructions": with_profile(BACKEND_INSTRUCTIONS, profile)
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
        self._http_client = http_client

    @property
    def basket_enabled(self) -> bool:
        return self.baskets is not None and self.approvals is not None

    def _tool_executor(
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
            )
        if self.zepto_searcher is not None:
            return MultiProviderShoppingToolExecutor(
                [
                    ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", self.searcher, address_id, True),
                    ProviderSearchTarget("zepto", "Zepto", self.zepto_searcher, zepto_address_id),
                ]
            )
        return ShoppingToolExecutor(self.searcher, address_id)

    async def _profile(self, household_id: str) -> UserProfile | None:
        if self.profile_reader is None or not household_id:
            return None
        try:
            return await self.profile_reader.read(household_id)
        except Exception as exc:  # noqa: BLE001 - a missing profile must not block voice
            logger.warning("Voice profile read failed: %s", type(exc).__name__)
            return None

    async def _context(self, household_id: str) -> RecentConversationContext | None:
        if self.context_reader is None or not household_id:
            return None
        try:
            return await asyncio.wait_for(
                self.context_reader.load(household_id), CONTEXT_TIMEOUT_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - missing context must not block voice
            logger.warning("Voice chat context read failed: %s", type(exc).__name__)
            return None

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.post(url, **kwargs)
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, **kwargs)

    async def create(
        self,
        offer_sdp: str,
        swiggy_address_id: str | None = None,
        zepto_address_id: str | None = None,
        household_id: str = "",
    ) -> LiveSession:
        api_key = self.settings.openai_api_key.get_secret_value().strip()
        if not api_key:
            raise VoiceConfigurationError("Set OPENAI_API_KEY in .env to enable voice.")
        profile, context = await asyncio.gather(
            self._profile(household_id), self._context(household_id)
        )
        try:
            response = await self._post(
                f"{self.settings.openai_base_url.rstrip('/')}/live/sessions",
                json={
                    "session": build_session_config(
                        self.settings,
                        VOICE_TOOL_DEFINITIONS if self.basket_enabled else TOOL_DEFINITIONS,
                        profile,
                        context,
                    ),
                    "transport": {"type": "webrtc", "sdp": offer_sdp},
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.HTTPError as exc:
            raise VoiceProviderError("Could not reach OpenAI to start voice.") from exc

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
            self._tool_executor(swiggy_address_id, zepto_address_id, household_id),
            owner_id=household_id,
            events_token=events_token,
        )
        return LiveSession(
            session_id=session_id,
            sdp=answer_sdp,
            turn_taking=client_turn_taking(self.settings.voice_turn_taking),
            events_token=events_token,
        )


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        message = error.get("message") if isinstance(error, dict) else None
    except ValueError:
        message = None
    if isinstance(message, str) and message:
        return f"Voice session could not start: {message}"
    return f"Voice session could not start (HTTP {response.status_code})."
