"""Wiring for rider assist: who the owner is, what may run, and the stream handler."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from fastapi import WebSocket, status

from app.commerce.provider_state import SavedAddressDirectory
from app.core.config import Settings, get_settings
from app.database.session import get_session_factory
from app.rider.auth import stream_auth_configured, stream_request_authorised
from app.rider.bridge import BridgeConfig, RealtimeConnector, RiderCallBridge
from app.rider.orders import ActiveOrderReader, DatabaseActiveOrderReader
from app.rider.protocol import sample_rate_from_query
from app.rider.realtime_client import realtime_connector
from app.rider.store import RiderSettings, RiderStore
from app.rider.tools import RiderToolbox
from app.user_profile import DatabaseProfileReader, ProfileReader

logger = logging.getLogger(__name__)

MAX_CONCURRENT_CALLS = 2
POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION
TRY_AGAIN_LATER = 1013


class RiderAssistService:
    def __init__(
        self,
        settings: Settings,
        store: RiderStore,
        orders: ActiveOrderReader,
        profiles: ProfileReader | None,
        connector_factory=realtime_connector,
    ) -> None:
        self.settings = settings
        self.store = store
        self.orders = orders
        self.profiles = profiles
        self.connector_factory = connector_factory
        self._calls = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

    @property
    def owner_id(self) -> str:
        return self.settings.rider_assist_owner_user_id.strip()

    async def settings_for(self, user_id: str) -> RiderSettings:
        enabled, notes = await self.store.enabled_and_notes(user_id)
        return RiderSettings(
            feature_enabled=self.settings.rider_assist_enabled,
            owner_configured=bool(self.owner_id) and self.owner_id == user_id,
            stream_auth_configured=stream_auth_configured(self.settings),
            enabled=enabled,
            notes=notes,
        )

    async def refusal(self, websocket: WebSocket) -> str | None:
        """Why this stream must be refused, or None. Checked before accepting it."""

        if not self.settings.rider_assist_enabled:
            return "feature_off"
        if not stream_request_authorised(self.settings, websocket.headers.get("authorization")):
            return "auth"
        if not self.owner_id:
            return "no_owner"
        enabled, _ = await self.store.enabled_and_notes(self.owner_id)
        if not enabled:
            return "owner_switched_off"
        return None

    async def handle(self, websocket: WebSocket) -> None:
        try:
            reason = await self.refusal(websocket)
        except Exception as exc:  # noqa: BLE001 - e.g. database down
            logger.warning("event=rider_stream_refused reason=error error=%s", type(exc).__name__)
            reason = "error"
        if reason is not None:
            # Closing before accept answers the handshake with HTTP 403; Exotel moves
            # on to the flow's next applet (the fallback greeting / Connect).
            logger.warning("event=rider_stream_refused reason=%s", reason)
            await websocket.close(code=POLICY_VIOLATION)
            return
        if self._calls.locked():
            logger.warning("event=rider_stream_refused reason=busy")
            await websocket.close(code=TRY_AGAIN_LATER)
            return
        async with self._calls:
            await websocket.accept()
            bridge = await self.bridge_for(websocket)
            await bridge.run()

    def connector(self, owner: str) -> RealtimeConnector:
        return self.connector_factory(self.settings, owner)

    def _toolbox(self, owner: str) -> RiderToolbox:
        async def notes():
            return (await self.store.enabled_and_notes(owner))[1]

        return RiderToolbox(user_id=owner, orders=self.orders, notes=notes)

    def _config(self, websocket: WebSocket, *, owner_name: str | None, language: str) -> BridgeConfig:
        return BridgeConfig(
            model=self.settings.rider_assist_realtime_model,
            voice=self.settings.rider_assist_voice,
            transcription_model=self.settings.rider_assist_transcription_model,
            max_call_seconds=self.settings.rider_assist_max_call_seconds,
            owner_name=owner_name,
            language=language,
            query_sample_rate=sample_rate_from_query(websocket.url.query),
            expected_to=self.settings.rider_assist_exophone.strip(),
        )

    async def bridge_for(self, websocket: WebSocket) -> RiderCallBridge:
        owner = self.owner_id
        name: str | None = None
        _, notes = await self.store.enabled_and_notes(owner)
        if self.profiles is not None:
            try:
                profile = await self.profiles.read(owner)
            except Exception:  # noqa: BLE001
                profile = None
            if profile is not None:
                name = profile.first_name
        return RiderCallBridge(
            websocket,
            self.connector(owner),
            self._toolbox(owner),
            self._config(websocket, owner_name=name, language=notes.preferred_language),
            self.store,
        )


@lru_cache
def get_rider_service() -> RiderAssistService:
    from app.dependencies import get_order_tracking_service

    settings = get_settings()
    sessions = get_session_factory()
    return RiderAssistService(
        settings,
        RiderStore(sessions),
        DatabaseActiveOrderReader(
            sessions, get_order_tracking_service().store, SavedAddressDirectory(sessions)
        ),
        DatabaseProfileReader(sessions),
    )
