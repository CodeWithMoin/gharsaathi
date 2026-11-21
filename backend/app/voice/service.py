from typing import Any

import httpx
from pydantic import BaseModel

from app.core.config import Settings
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.voice.prompts import BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS
from app.voice.sideband import SidebandHub
from app.voice.toolbox import ToolExecutor, VOICE_TOOL_DEFINITIONS, VoiceToolbox
from app.voice.tools import (
    TOOL_DEFINITIONS,
    MultiProviderShoppingToolExecutor,
    ProductSearcher,
    ProviderSearchTarget,
    ShoppingToolExecutor,
)


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


def build_session_config(
    settings: Settings, tool_definitions: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "model": settings.live_model,
        "instructions": LIVE_INSTRUCTIONS,
        "audio": {"output": {"voice": settings.live_voice}},
        "delegation": {
            "type": "responses",
            "responses": {
                "model": settings.live_backend_model,
                "instructions": BACKEND_INSTRUCTIONS,
                "tools": tool_definitions if tool_definitions is not None else TOOL_DEFINITIONS,
                "tool_choice": "auto",
                "parallel_tool_calls": True,
            },
        },
    }


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
    ) -> None:
        self.settings = settings
        self.searcher = searcher
        self.hub = hub
        self.baskets = baskets
        self.approvals = approvals
        self.zepto_searcher = zepto_searcher
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
            )
        if self.zepto_searcher is not None:
            return MultiProviderShoppingToolExecutor(
                [
                    ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", self.searcher, address_id, True),
                    ProviderSearchTarget("zepto", "Zepto", self.zepto_searcher, zepto_address_id),
                ]
            )
        return ShoppingToolExecutor(self.searcher, address_id)

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
        try:
            response = await self._post(
                f"{self.settings.openai_base_url.rstrip('/')}/live/sessions",
                json={
                    "session": build_session_config(
                        self.settings,
                        VOICE_TOOL_DEFINITIONS if self.basket_enabled else TOOL_DEFINITIONS,
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
        self.hub.start(
            session_id,
            self._tool_executor(swiggy_address_id, zepto_address_id, household_id),
        )
        return LiveSession(session_id=session_id, sdp=answer_sdp)


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        message = error.get("message") if isinstance(error, dict) else None
    except ValueError:
        message = None
    if isinstance(message, str) and message:
        return f"Voice session could not start: {message}"
    return f"Voice session could not start (HTTP {response.status_code})."
