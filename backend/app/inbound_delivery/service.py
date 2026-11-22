"""The inbound delivery line: share the selected address with one approved caller, and clear it.

Everything the line says comes from server-side records: the store address the
user selected (the saved provider snapshot), the name in their profile, and the
directions they saved for that address. The browser sends only ids and an
explicit approval. Sharing and clearing each stream one cohort record to Sarvam
(``sarvam.py``); nothing here places a call or touches an order.

Clearing happens automatically when the selected address changes or the tracked
order is delivered or cancelled, and on the user's request.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.commerce.swiggy.models import AddressList
from app.core.config import Settings
from app.inbound_delivery.models import (
    ADDRESS_BOOK_KEYS,
    DeliveryLine,
    DirectionsUpdate,
    InboundDeliveryConfig,
    LineSettingsUpdate,
    PrepareRequest,
    RevokeReason,
    SavedDirections,
)
from app.inbound_delivery.sarvam import (
    E164,
    CohortResult,
    SarvamCohorts,
    SarvamError,
    clear_variables,
    mask_number,
    share_variables,
)
from app.inbound_delivery.store import DeliveryLineStore

logger = logging.getLogger(__name__)

# Cohort ids this process streamed, for status checks; oldest dropped first.
MAX_TRACKED_COHORTS = 128
SETTLE_ATTEMPTS = 15
SETTLE_SECONDS = 2.0
ACTIVE_PHASES = frozenset({"processing", "ready", "unknown", "rejected", "failed", "clear_failed"})
SHARE_PHASES = frozenset({"processing", "ready", "unknown"})
TERMINAL_REASONS: dict[str, RevokeReason] = {"delivered": "delivered", "cancelled": "cancelled"}


class InboundDeliveryError(Exception):
    """Shown to the user as ``detail`` with a machine-readable ``code``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class AddressBook(Protocol):
    async def saved(self, household_id: str, provider: str) -> AddressList | None: ...


class NameReader(Protocol):
    async def read(self, user_id: str) -> Any: ...


class ContextLog(Protocol):
    async def record_context(self, user_id: str, cohort_id: str, prepared_at: datetime) -> None: ...

    async def clear_contexts(self, user_id: str) -> int: ...


class Notifier(Protocol):
    async def send(self, user_id: str, payload: Any, *, dedupe_key: str | None = None) -> Any: ...


class InboundDeliveryService:
    def __init__(
        self,
        settings: Settings,
        store: DeliveryLineStore,
        address_book: AddressBook,
        profiles: NameReader,
        *,
        client: Any = None,
        notifier: Notifier | None = None,
        contexts: ContextLog | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.store = store
        self.address_book = address_book
        self.profiles = profiles
        self.sarvam = SarvamCohorts(settings, client)
        self.notifier = notifier
        # When each share was streamed (`inbound_delivery_contexts`): the call
        # monitor polls Sarvam for calls for 2 h after it (app/inbound_calls.py).
        self.contexts = contexts
        self.sleep = sleep
        self._cohort_owners: OrderedDict[str, str] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # Setup and guard ---------------------------------------------------------

    @staticmethod
    def _user_key(user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()[:24]

    def _configured(self) -> bool:
        settings = self.settings
        return (
            bool(settings.sarvam_voice_agent_api_key.get_secret_value().strip())
            and bool(settings.sarvam_voice_agent_org_id)
            and bool(settings.sarvam_voice_agent_workspace_id)
            and bool(settings.sarvam_inbound_deployment_id)
            and bool(E164.fullmatch(settings.sarvam_demo_recipient_number.strip()))
            and bool(E164.fullmatch(settings.sarvam_inbound_phone_number.strip()))
        )

    def _owner_state(self, user_id: str) -> str | None:
        """None when ``user_id`` may use the line, else why not."""

        owner = self.settings.sarvam_inbound_owner_user_id.strip()
        if owner:
            return None if user_id == owner else "not_owner"
        allowed = self.settings.allowed_user_id_set
        if allowed:
            return None if user_id in allowed else "not_owner"
        return "owner_not_configured"

    def config(self, user_id: str) -> InboundDeliveryConfig:
        reason = "not_configured" if not self._configured() else self._owner_state(user_id)
        enabled = reason is None
        return InboundDeliveryConfig(
            enabled=enabled,
            reason=reason,  # type: ignore[arg-type]
            inbound_phone_number=self.settings.sarvam_inbound_phone_number.strip() if enabled else None,
            approved_caller_hint=mask_number(self.settings.sarvam_demo_recipient_number) if enabled else None,
            route_directions_enabled=self.settings.sarvam_route_directions_enabled,
        )

    def _require_enabled(self, user_id: str) -> None:
        config = self.config(user_id)
        if config.reason == "not_configured":
            raise InboundDeliveryError(503, "not_configured", "The delivery line is not set up on this server.")
        if config.reason == "owner_not_configured":
            raise InboundDeliveryError(
                503, "owner_not_configured", "The delivery line has no owner set on this server."
            )
        if config.reason == "not_owner":
            raise InboundDeliveryError(403, "not_owner", "Only the owner can use the delivery line.")

    def _lock(self, user_id: str) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    def _remember_cohort(self, cohort_id: str, user_id: str) -> None:
        self._cohort_owners[cohort_id] = self._user_key(user_id)
        self._cohort_owners.move_to_end(cohort_id)
        while len(self._cohort_owners) > MAX_TRACKED_COHORTS:
            self._cohort_owners.popitem(last=False)

    def _view(self, line: DeliveryLine) -> DeliveryLine:
        line.inbound_phone_number = self.settings.sarvam_inbound_phone_number.strip() or None
        return line

    # Directions --------------------------------------------------------------

    async def _selected_address(self, user_id: str, provider: str) -> tuple[str, str] | None:
        """(id, full address text) of the store address the user selected."""

        addresses = await self.address_book.saved(user_id, ADDRESS_BOOK_KEYS[provider])
        if addresses is None:
            return None
        selected = addresses.resolution.default_address_id
        match = next((item for item in addresses.addresses if item.id == selected), None)
        if match is None:
            return None
        return match.id, " ".join(match.address_line.split())

    async def _owns_address(self, user_id: str, provider: str, address_id: str) -> bool:
        addresses = await self.address_book.saved(user_id, ADDRESS_BOOK_KEYS[provider])
        return addresses is not None and any(item.id == address_id for item in addresses.addresses)

    async def directions(self, user_id: str, provider: str, address_id: str) -> SavedDirections | None:
        return await self.store.directions(user_id, provider, address_id)

    async def save_directions(self, user_id: str, change: DirectionsUpdate) -> SavedDirections:
        if not await self._owns_address(user_id, change.provider, change.address_id):
            raise InboundDeliveryError(
                409, "address_not_saved", "That address isn't in your saved addresses. Refresh and try again."
            )
        return await self.store.save_directions(user_id, change)

    async def delete_directions(self, user_id: str, provider: str, address_id: str) -> bool:
        return await self.store.delete_directions(user_id, provider, address_id)

    # Line --------------------------------------------------------------------

    async def line(self, user_id: str) -> DeliveryLine:
        return self._view(await self.store.line(user_id))

    async def update_settings(self, user_id: str, change: LineSettingsUpdate) -> DeliveryLine:
        if change.auto_share:
            self._require_enabled(user_id)
        return self._view(await self.store.update_line(user_id, auto_share=change.auto_share))

    async def prepare(
        self, user_id: str, request: PrepareRequest, *, monitor_id: str | None = None
    ) -> DeliveryLine:
        self._require_enabled(user_id)
        async with self._lock(user_id):
            return await self._share(user_id, request.provider, request.address_id, monitor_id)

    async def _share(
        self, user_id: str, provider: str, address_id: str, monitor_id: str | None
    ) -> DeliveryLine:
        selected = await self._selected_address(user_id, provider)
        if selected is None:
            raise InboundDeliveryError(409, "no_address", "Choose a delivery address first.")
        if selected[0] != address_id:
            raise InboundDeliveryError(
                409, "address_changed", "Your selected address changed. Check it and share again."
            )
        profile = await self.profiles.read(user_id)
        name = (getattr(profile, "first_name", None) or "").strip()
        if len(name) < 2:
            raise InboundDeliveryError(409, "no_name", "Add the recipient's name first.")
        directions = await self.store.directions(user_id, provider, address_id)
        if directions is None or not directions.landmark:
            raise InboundDeliveryError(
                409, "no_directions", "Add a landmark for this address before sharing it."
            )
        variables = share_variables(
            recipient_name=name,
            delivery_address=selected[1],
            landmark=directions.landmark,
            route_directions=directions.route_directions,
            gate_note=directions.gate_note,
            handover_instructions=directions.handover_instructions,
            include_route=self.settings.sarvam_route_directions_enabled,
        )
        try:
            result = await self.sarvam.stream(
                user_identifier=self._user_key(user_id), variables=variables, kind="share"
            )
        except SarvamError as exc:
            if exc.code == "outcome_unknown":
                # The details may now be live: remember them so they are cleared later.
                await self.store.update_line(
                    user_id, phase="unknown", provider=provider, address_id=address_id,
                    cohort_id=None, monitor_id=monitor_id, reason=None, error=exc.message,
                )
            raise InboundDeliveryError(exc.status_code, exc.code, exc.message) from exc
        self._remember_cohort(result.cohort_id, user_id)
        await self._log_context(user_id, result.cohort_id)
        return self._view(await self.store.update_line(
            user_id,
            phase=_share_phase(result),
            provider=provider,
            address_id=address_id,
            cohort_id=result.cohort_id,
            monitor_id=monitor_id,
            reason=None,
            error=None,
        ))

    async def revoke(self, user_id: str, reason: RevokeReason) -> DeliveryLine:
        """Stream a replacement record that withdraws the caller's authorisation.

        Allowed even when the line is otherwise unavailable to this user, as long
        as the server can reach Sarvam: clearing only ever removes details.
        """

        if self._owner_state(user_id) is not None:
            raise InboundDeliveryError(403, "not_owner", "Only the owner can use the delivery line.")
        async with self._lock(user_id):
            current = await self.store.line(user_id)
            if current.phase not in ACTIVE_PHASES and reason != "user_cleared":
                return self._view(current)
            if not self._configured():
                raise InboundDeliveryError(503, "not_configured", "The delivery line is not set up on this server.")
            try:
                result = await self.sarvam.stream(
                    user_identifier=self._user_key(user_id),
                    variables=clear_variables(include_route=self.settings.sarvam_route_directions_enabled),
                    kind="clear",
                )
            except SarvamError as exc:
                return self._view(await self.store.update_line(
                    user_id, phase="clear_failed", reason=reason, error=exc.message, cohort_id=None
                ))
            self._remember_cohort(result.cohort_id, user_id)
            await self._forget_contexts(user_id)
            return self._view(await self.store.update_line(
                user_id,
                phase=_clear_phase(result),
                cohort_id=result.cohort_id,
                reason=reason,
                error=None,
                monitor_id=None,
            ))

    async def _log_context(self, user_id: str, cohort_id: str) -> None:
        if self.contexts is None:
            return
        try:
            await self.contexts.record_context(user_id, cohort_id, datetime.now(UTC))
        except Exception as exc:  # noqa: BLE001 - the share is live at Sarvam either way
            logger.warning("event=inbound_context_record_failed error=%s", type(exc).__name__)

    async def _forget_contexts(self, user_id: str) -> None:
        if self.contexts is None:
            return
        try:
            await self.contexts.clear_contexts(user_id)
        except Exception as exc:  # noqa: BLE001 - the clear is sent either way
            logger.warning("event=inbound_context_clear_failed error=%s", type(exc).__name__)

    async def status(self, user_id: str, cohort_id: str) -> DeliveryLine:
        current = await self.store.line(user_id)
        owned = self._cohort_owners.get(cohort_id) == self._user_key(user_id)
        if not owned and current.cohort_id != cohort_id:
            raise InboundDeliveryError(404, "not_found", "Delivery line update not found.")
        try:
            result = await self.sarvam.get(cohort_id)
        except SarvamError as exc:
            raise InboundDeliveryError(exc.status_code, exc.code, exc.message) from exc
        if current.cohort_id != cohort_id:
            return self._view(current)
        clearing = current.phase in {"clearing", "cleared", "clear_failed"}
        phase = _clear_phase(result) if clearing else _share_phase(result)
        return self._view(await self.store.update_line(user_id, phase=phase))

    async def _settle(self, user_id: str, line: DeliveryLine) -> DeliveryLine:
        """Background work only: wait for Sarvam to finish processing one cohort."""

        for _ in range(SETTLE_ATTEMPTS):
            if line.phase not in {"processing", "clearing"} or not line.cohort_id:
                return line
            await self.sleep(SETTLE_SECONDS)
            try:
                line = await self.status(user_id, line.cohort_id)
            except InboundDeliveryError:
                return line
        return line

    # Automatic sharing and clearing --------------------------------------------

    def _spawn(self, work: Callable[[], Awaitable[Any]]) -> None:
        try:
            task = asyncio.get_running_loop().create_task(work())
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def address_selected(self, user_id: str, provider: str, address_id: str) -> None:
        """The user picked another store address: clear details shared for the old one."""

        current = await self.store.line(user_id)
        if current.phase not in ACTIVE_PHASES or current.provider != provider:
            return
        if current.address_id == address_id:
            return
        line = await self.revoke(user_id, "address_changed")
        await self._notify_cleared(user_id, await self._settle(user_id, line))

    async def address_selection_changed(self, user_id: str, provider: str, address_id: str) -> None:
        """Hook for every address selection. Returns fast; clearing runs in the background."""

        if not self._configured():
            return
        try:
            current = await self.store.line(user_id)
        except Exception as exc:  # noqa: BLE001 - selecting an address must still work
            logger.warning("event=inbound_delivery_line_read_failed error=%s", type(exc).__name__)
            return
        if current.phase in ACTIVE_PHASES and current.provider == provider and current.address_id != address_id:
            self._spawn(lambda: self._safely(self.address_selected(user_id, provider, address_id)))

    def tracking_started(self, watch: Any) -> None:
        """A watch began for an order on its way: share automatically if the user turned it on."""

        if not self._configured() or getattr(watch, "status", None) in {"delivered", "cancelled"}:
            return
        self._spawn(lambda: self._safely(self._auto_share(watch.user_id, str(watch.provider), watch.id)))

    def tracking_updated(self, watch: Any) -> None:
        reason = TERMINAL_REASONS.get(str(getattr(watch, "status", "")))
        if reason is None or not self._configured():
            return
        self._spawn(lambda: self._safely(self._order_finished(watch.user_id, str(watch.provider), watch.id, reason)))

    async def _safely(self, work: Awaitable[Any]) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background work never breaks tracking
            logger.warning("event=inbound_delivery_background_failed error=%s", type(exc).__name__)

    async def _auto_share(self, user_id: str, provider: str, monitor_id: str) -> None:
        current = await self.store.line(user_id)
        if not current.auto_share or self.config(user_id).reason is not None:
            return
        selected = await self._selected_address(user_id, provider)
        if selected is None:
            await self._notify(user_id, "Delivery line not shared", "Choose a delivery address in GharSaathi, then share it.", f"share:{monitor_id}")
            return
        if current.phase in {"ready", "processing"} and current.provider == provider and current.address_id == selected[0]:
            return
        try:
            async with self._lock(user_id):
                line = await self._share(user_id, provider, selected[0], monitor_id)
        except InboundDeliveryError as exc:
            await self._notify(user_id, "Delivery line not shared", _short(exc.message), f"share:{monitor_id}")
            return
        line = await self._settle(user_id, line)
        if line.phase == "ready":
            await self._notify(user_id, "Delivery line ready", "The delivery partner can now call the delivery line for your address.", f"share:{monitor_id}")
        else:
            await self._notify(user_id, "Delivery line not confirmed", "Open GharSaathi to check the delivery line before relying on it.", f"share:{monitor_id}")

    async def _order_finished(self, user_id: str, provider: str, monitor_id: str, reason: RevokeReason) -> None:
        current = await self.store.line(user_id)
        if current.phase not in ACTIVE_PHASES or current.provider != provider:
            return
        if current.monitor_id is not None and current.monitor_id != monitor_id:
            return
        line = await self.revoke(user_id, reason)
        await self._notify_cleared(user_id, await self._settle(user_id, line))

    async def _notify_cleared(self, user_id: str, line: DeliveryLine) -> None:
        if line.phase == "cleared":
            await self._notify(user_id, "Delivery line cleared", "Your address is no longer shared on the delivery line.", None)
        elif line.phase == "clear_failed":
            await self._notify(user_id, "Delivery line not cleared", "Open GharSaathi and clear the delivery line again.", None)

    async def _notify(self, user_id: str, title: str, body: str, dedupe_key: str | None) -> None:
        if self.notifier is None:
            return
        from app.notifications.messages import delivery_line

        await self.notifier.send(user_id, delivery_line(title, body), dedupe_key=dedupe_key)


def _short(text: str) -> str:
    return text if len(text) <= 150 else text[:149].rstrip() + "…"


def _share_phase(result: CohortResult) -> str:
    if result.status == "processing":
        return "processing"
    if result.status == "failed":
        return "failed"
    return "ready" if result.accepted_one else "rejected"


def _clear_phase(result: CohortResult) -> str:
    if result.status == "processing":
        return "clearing"
    return "cleared" if result.accepted_one else "clear_failed"


class LineClearingAddressBook:
    """An address book whose selections (e.g. by voice) also clear a stale delivery line."""

    def __init__(self, book: Any, line: InboundDeliveryService) -> None:
        self.book = book
        self.line = line

    async def saved(self, household_id: str, provider: str) -> AddressList | None:
        return await self.book.saved(household_id, provider)

    async def select(self, household_id: str, provider: str, address_id: str) -> AddressList | None:
        addresses = await self.book.select(household_id, provider, address_id)
        store = next((name for name, key in ADDRESS_BOOK_KEYS.items() if key == provider), None)
        if addresses is not None and store is not None:
            await self.line.address_selection_changed(household_id, store, address_id)
        return addresses
