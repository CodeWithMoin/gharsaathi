"""Wiring for rider assist: who the owner is, what may run, and the Vobiz endpoints."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import WebSocket, status

from app.commerce.provider_state import SavedAddressDirectory
from app.core.config import Settings, get_settings
from app.database.session import get_session_factory
from app.notifications import messages
from app.rider.bridge import BridgeConfig, RealtimeConnector, RiderCallBridge
from app.rider.orders import ActiveOrderReader, DatabaseActiveOrderReader
from app.rider.realtime_client import realtime_connector
from app.rider.store import RiderCallSummary, RiderSettings, RiderStore, outcome_text
from app.rider.telephony import mask_number, same_number
from app.rider.tools import RiderToolbox
from app.rider import vobiz
from app.user_profile import DatabaseProfileReader, ProfileReader

logger = logging.getLogger(__name__)

MAX_CONCURRENT_CALLS = 2
POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION
TRY_AGAIN_LATER = 1013
REGISTRY_SIZE = 200
REGISTRY_TTL_SECONDS = 15 * 60


@dataclass
class _Call:
    caller: str
    created: float
    language: str
    streamed: bool = False
    outcome: str | None = None


class CallRegistry:
    """Calls the signed answer webhook let in, by Vobiz CallUUID (memory, bounded).

    A stream is accepted once per answered call; the after-stream webhook reads
    how it went (apologise when the assistant never answered).
    """

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self._calls: OrderedDict[str, _Call] = OrderedDict()

    def _prune(self) -> None:
        cutoff = self.clock() - REGISTRY_TTL_SECONDS
        for key in [key for key, call in self._calls.items() if call.created < cutoff]:
            self._calls.pop(key, None)
        while len(self._calls) > REGISTRY_SIZE:
            self._calls.popitem(last=False)

    def answered(self, call_id: str, caller: str, language: str) -> None:
        self._prune()
        self._calls[call_id] = _Call(caller=caller, created=self.clock(), language=language)

    def claim_stream(self, call_id: str) -> _Call | None:
        self._prune()
        call = self._calls.get(call_id)
        if call is None or call.streamed:
            return None
        call.streamed = True
        return call

    def finished(self, call_id: str, outcome: str) -> None:
        call = self._calls.get(call_id)
        if call is not None:
            call.outcome = outcome

    def get(self, call_id: str) -> _Call | None:
        self._prune()
        return self._calls.get(call_id)


@dataclass(frozen=True)
class WebhookReply:
    status_code: int
    xml: str | None = None


class NotifyingRecorder:
    """Saves the call summary and sends the owner one browser notification per call.

    The in-app card comes from the saved summary (``GET /api/rider/calls``); the
    browser notification (kind ``rider_called``, user-toggleable) is sent the first
    time a call is saved, so an urgent ``notify_owner`` reaches the owner at once.
    """

    def __init__(self, store: RiderStore, notifier: Any, user_id: str) -> None:
        self.store = store
        self.notifier = notifier
        self.user_id = user_id
        self._sent = False

    async def add_call(self, user_id: str, call: RiderCallSummary):
        saved = await self.store.add_call(user_id, call)
        await self._notify(saved.id, call)
        return saved

    async def update_call(self, user_id: str, call_id: str, call: RiderCallSummary) -> bool:
        return await self.store.update_call(user_id, call_id, call)

    async def _notify(self, call_id: str, call: RiderCallSummary) -> None:
        if self._sent or self.notifier is None:
            return
        self._sent = True
        try:
            await self.notifier.send(
                self.user_id,
                messages.rider_called(
                    call.started_at, call.rider_said, outcome_text(call), urgent=call.urgency == "high", call_id=call_id
                ),
                dedupe_key=f"rider_called:{call_id}",
            )
        except Exception as exc:  # noqa: BLE001 - a notification never breaks a call
            logger.warning("event=rider_push_failed error=%s", type(exc).__name__)


class RiderAssistService:
    def __init__(
        self,
        settings: Settings,
        store: RiderStore,
        orders: ActiveOrderReader,
        profiles: ProfileReader | None,
        connector_factory=realtime_connector,
        *,
        notifier: Any = None,
        registry: CallRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.orders = orders
        self.profiles = profiles
        self.connector_factory = connector_factory
        self.notifier = notifier
        self.registry = registry or CallRegistry()
        self.adapter = vobiz.VobizAdapter()
        self._calls = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
        self._active = 0

    @property
    def owner_id(self) -> str:
        return self.settings.rider_assist_owner_user_id.strip()

    @property
    def _secret(self) -> str:
        return self.settings.rider_assist_stream_secret.get_secret_value()

    @property
    def _auth_token(self) -> str:
        return self.settings.vobiz_auth_token.get_secret_value().strip()

    def configured(self) -> bool:
        return (
            self.settings.rider_assist_provider == "vobiz"
            and bool(self.owner_id)
            and bool(self.settings.vobiz_auth_id.strip())
            and bool(self._auth_token)
            and len(self._secret) >= vobiz.MIN_SECRET_LENGTH
            and vobiz.valid_public_url(self.settings.rider_assist_public_url)
        )

    async def settings_for(self, user_id: str) -> RiderSettings:
        enabled, notes = await self.store.enabled_and_notes(user_id)
        return RiderSettings(
            feature_enabled=self.settings.rider_assist_enabled,
            owner_configured=bool(self.owner_id) and self.owner_id == user_id,
            stream_auth_configured=self.configured(),
            enabled=enabled,
            notes=notes,
        )

    # ---- Vobiz webhooks --------------------------------------------------

    def _signed(self, path: str, headers: Mapping[str, str]) -> bool:
        url = vobiz.base_url(self.settings.rider_assist_public_url, path)
        return vobiz.signature_valid(url, self._auth_token, headers)

    async def answer(self, form: Mapping[str, str], headers: Mapping[str, str]) -> WebhookReply:
        if not self.settings.rider_assist_enabled:
            return WebhookReply(404)
        if not self.configured():
            logger.warning("event=rider_answer_refused reason=not_configured")
            return WebhookReply(503)
        if not self._signed(vobiz.ANSWER_PATH, headers):
            logger.warning("event=rider_answer_refused reason=signature")
            return WebhookReply(403)
        call_id = str(form.get("CallUUID", ""))
        if not vobiz.CALL_ID.match(call_id):
            return WebhookReply(400)
        enabled, notes = await self.store.enabled_and_notes(self.owner_id)
        language = notes.preferred_language
        number = self.settings.rider_assist_number.strip()
        if number and not same_number(str(form.get("To", "")), number):
            logger.warning("event=rider_answer_refused reason=unexpected_number")
            return WebhookReply(200, vobiz.unavailable_xml(language))
        if not enabled:
            logger.info("event=rider_answer_refused reason=owner_switched_off")
            return WebhookReply(200, vobiz.unavailable_xml(language))
        if self._active >= MAX_CONCURRENT_CALLS:
            logger.warning("event=rider_answer_refused reason=busy")
            return WebhookReply(200, vobiz.unavailable_xml(language))
        self.registry.answered(call_id, mask_number(str(form.get("From", ""))), language)
        public = self.settings.rider_assist_public_url
        xml = vobiz.answer_xml(
            vobiz.stream_url(public, self._secret, call_id),
            vobiz.base_url(public, vobiz.AFTER_STREAM_PATH),
            timeout_seconds=self.settings.rider_assist_max_call_seconds + 30,
        )
        return WebhookReply(200, xml)

    async def after_stream(self, form: Mapping[str, str], headers: Mapping[str, str]) -> WebhookReply:
        if not self.settings.rider_assist_enabled:
            return WebhookReply(404)
        if not self.configured() or not self._signed(vobiz.AFTER_STREAM_PATH, headers):
            return WebhookReply(403)
        call = self.registry.get(str(form.get("CallUUID", "")))
        # Apologise when the assistant never got to answer: no stream, or OpenAI down.
        apologise = call is not None and (not call.streamed or call.outcome == "assistant_unavailable")
        language = call.language if call is not None else "hinglish"
        return WebhookReply(200, vobiz.after_stream_xml(apologise=apologise, language=language))

    # ---- stream ----------------------------------------------------------

    async def refusal(self, call_id: str, token: str) -> str | None:
        """Why this stream must be refused, or None. Checked before accepting it."""

        if not self.settings.rider_assist_enabled:
            return "feature_off"
        if not self.configured():
            return "not_configured"
        if not vobiz.stream_token_valid(self._secret, call_id, token):
            return "auth"
        enabled, _ = await self.store.enabled_and_notes(self.owner_id)
        if not enabled:
            return "owner_switched_off"
        return None

    async def handle(self, websocket: WebSocket, call_id: str, token: str) -> None:
        try:
            reason = await self.refusal(call_id, token)
        except Exception as exc:  # noqa: BLE001 - e.g. database down
            logger.warning("event=rider_stream_refused reason=error error=%s", type(exc).__name__)
            reason = "error"
        call = None
        if reason is None:
            call = self.registry.claim_stream(call_id)
            if call is None:
                reason = "not_answered"
        if reason is not None:
            # Closing before accept answers the handshake with HTTP 403.
            logger.warning("event=rider_stream_refused reason=%s", reason)
            await websocket.close(code=POLICY_VIOLATION)
            return
        assert call is not None
        if self._calls.locked():
            logger.warning("event=rider_stream_refused reason=busy")
            await websocket.close(code=TRY_AGAIN_LATER)
            return
        async with self._calls:
            self._active += 1
            try:
                await websocket.accept()
                bridge = await self.bridge_for(websocket, call_id, call.caller)
                summary = await bridge.run()
                self.registry.finished(call_id, summary.outcome if summary else "refused")
            finally:
                self._active -= 1

    def connector(self, owner: str) -> RealtimeConnector:
        return self.connector_factory(self.settings, owner)

    def _toolbox(self, owner: str) -> RiderToolbox:
        async def notes():
            return (await self.store.enabled_and_notes(owner))[1]

        return RiderToolbox(user_id=owner, orders=self.orders, notes=notes)

    async def bridge_for(self, socket: Any, call_id: str, caller: str) -> RiderCallBridge:
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
        config = BridgeConfig(
            model=self.settings.rider_assist_realtime_model,
            voice=self.settings.rider_assist_voice,
            transcription_model=self.settings.rider_assist_transcription_model,
            max_call_seconds=self.settings.rider_assist_max_call_seconds,
            owner_name=name,
            language=notes.preferred_language,
            expected_call_id=call_id,
            caller=caller,
        )
        return RiderCallBridge(
            socket,
            self.adapter,
            self.connector(owner),
            self._toolbox(owner),
            config,
            NotifyingRecorder(self.store, self.notifier, owner),
        )


@lru_cache
def get_rider_service() -> RiderAssistService:
    from app.dependencies import get_order_tracking_service, get_push_notifier

    settings = get_settings()
    sessions = get_session_factory()
    return RiderAssistService(
        settings,
        RiderStore(sessions),
        DatabaseActiveOrderReader(
            sessions, get_order_tracking_service().store, SavedAddressDirectory(sessions)
        ),
        DatabaseProfileReader(sessions),
        notifier=get_push_notifier(),
    )
