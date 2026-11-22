"""Bounded, automatic refresh of the Zepto saved-address snapshot.

Swiggy addresses refresh only on the user's explicit tap (its rate limits are
strict). Zepto's ``list_saved_addresses`` is a cheap read, so its snapshot is
also refreshed when it is stale at app load / voice or chat start, when the
address picker opens, and when a selected address turns out to be missing.

Bounds: at most one refresh in flight per user and at most one start per
``min_interval_seconds``; a caller waits at most ``wait_seconds`` and otherwise
continues with the cached snapshot while the refresh lands in the background.

A selection that disappeared is cleared, never replaced: the user must pick an
address again (even when only one is left). The Zepto basket keeps its lines,
but its address is cleared, which supersedes any open bill and order card.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.commerce.provider_state import (
    ProviderAddressRepository,
    cached_address_list,
    is_demo_address_state,
)
from app.commerce.swiggy.models import AddressList
from app.database.models import MemorySubject, ProviderAddressState, SubjectType
from app.memory.service import MemoryService
from app.shopping.basket import BasketService
from app.shopping.errors import ShoppingError


logger = logging.getLogger(__name__)

ZEPTO_PROVIDER = "zepto"
PICKER_MAX_AGE_SECONDS = 5 * 60
MIN_INTERVAL_SECONDS = 2 * 60
WAIT_SECONDS = 1.5


class ZeptoAddressSource(Protocol):
    async def addresses(self) -> AddressList: ...

    def mark_address_removed(self, address_id: str) -> None: ...


@dataclass(frozen=True)
class AddressRefreshOutcome:
    addresses: AddressList
    # True only when this refresh removed the user's selected address.
    selection_removed: bool = False


class ZeptoAddressRefresher:
    def __init__(
        self,
        source: ZeptoAddressSource,
        sessions: async_sessionmaker[AsyncSession],
        baskets: BasketService | None,
        *,
        max_age_seconds: float,
        min_interval_seconds: float = MIN_INTERVAL_SECONDS,
        wait_seconds: float = WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.source = source
        self.sessions = sessions
        self.baskets = baskets
        self.max_age_seconds = max_age_seconds
        self.min_interval_seconds = min_interval_seconds
        self.wait_seconds = wait_seconds
        self.clock = clock
        self.now = now
        self._in_flight: dict[str, asyncio.Task[AddressRefreshOutcome | None]] = {}
        self._last_started: dict[str, float] = {}

    async def refresh_if_stale(
        self, user_id: str, *, max_age_seconds: float | None = None, reason: str
    ) -> AddressRefreshOutcome | None:
        """Refresh a stale snapshot, waiting at most ``wait_seconds`` for it.

        Returns the outcome when the refresh finished in time, else None (the
        caller uses the cached snapshot). Never raises.
        """

        if not user_id:
            return None
        task = self._in_flight.get(user_id)
        if task is None:
            if self.clock() - self._last_started.get(user_id, -math.inf) < self.min_interval_seconds:
                return None
            try:
                age = await self._snapshot_age(user_id)
            except Exception as exc:  # noqa: BLE001 - never block the user's request
                logger.warning("event=zepto_address_refresh_failed reason=%s", type(exc).__name__)
                return None
            limit = self.max_age_seconds if max_age_seconds is None else max_age_seconds
            if age is None or age < limit:
                return None
            # Re-check after the read: another caller may have started meanwhile.
            task = self._in_flight.get(user_id) or self._start(user_id, reason)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.wait_seconds)
        except TimeoutError:
            return None
        except Exception:  # noqa: BLE001 - logged by the task
            return None

    def _start(self, user_id: str, reason: str) -> asyncio.Task[AddressRefreshOutcome | None]:
        self._last_started[user_id] = self.clock()
        task = asyncio.create_task(self._run(user_id, reason))
        self._in_flight[user_id] = task

        def finished(done: asyncio.Task[AddressRefreshOutcome | None]) -> None:
            if self._in_flight.get(user_id) is done:
                del self._in_flight[user_id]

        task.add_done_callback(finished)
        return task

    async def _run(self, user_id: str, reason: str) -> AddressRefreshOutcome | None:
        try:
            live = await self.source.addresses()
            outcome = await self.apply(user_id, live)
        except Exception as exc:  # noqa: BLE001 - background refresh; cached snapshot stays
            logger.warning(
                "event=zepto_address_refresh_failed reason=%s trigger=%s", type(exc).__name__, reason
            )
            return None
        logger.info(
            "event=zepto_address_refresh trigger=%s count=%d selection_removed=%s",
            reason,
            len(outcome.addresses.addresses),
            outcome.selection_removed,
        )
        return outcome

    async def apply(self, user_id: str, live: AddressList) -> AddressRefreshOutcome:
        """Store a live address list as the user's snapshot (explicit or automatic refresh)."""

        async with self.sessions() as session:
            subject_id = await _subject_id(session, user_id, create=True)
            assert subject_id is not None
            repository = ProviderAddressRepository(session)
            previous = await repository.get(subject_id, ZEPTO_PROVIDER)
            previous_selection = previous.selected_address_id if previous is not None else None
            state = await repository.save(subject_id, live, ZEPTO_PROVIDER)
            removed = previous_selection is not None and state.selected_address_id is None
            addresses = cached_address_list(state)
        if removed:
            assert previous_selection is not None
            await self._selection_removed(user_id, previous_selection)
        return AddressRefreshOutcome(addresses, removed)

    async def address_missing(self, live: AddressList, address_id: str) -> None:
        """A search found ``address_id`` missing: update every snapshot that selected it."""

        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(MemorySubject.external_id)
                    .join(ProviderAddressState, ProviderAddressState.subject_id == MemorySubject.id)
                    .where(
                        ProviderAddressState.provider == ZEPTO_PROVIDER,
                        ProviderAddressState.selected_address_id == address_id,
                    )
                )
            ).scalars().all()
        for user_id in rows:
            self._last_started[user_id] = self.clock()
            await self.apply(user_id, live)

    async def _selection_removed(self, user_id: str, address_id: str) -> None:
        logger.warning("event=zepto_address_selection_removed")
        self.source.mark_address_removed(address_id)
        if self.baskets is None:
            return
        try:
            await self.baskets.clear_removed_address(user_id, ZEPTO_PROVIDER, address_id)
        except ShoppingError as exc:
            logger.warning("event=zepto_address_bill_invalidate_failed error=%s", type(exc).__name__)

    async def _snapshot_age(self, user_id: str) -> float | None:
        async with self.sessions() as session:
            subject_id = await _subject_id(session, user_id, create=False)
            if subject_id is None:
                return None
            state = await ProviderAddressRepository(session).get(subject_id, ZEPTO_PROVIDER)
            if state is None or is_demo_address_state(state):
                # No snapshot yet: the first load stays an explicit user action.
                return None
            refreshed = state.refreshed_at
        if refreshed.tzinfo is None:
            refreshed = refreshed.replace(tzinfo=UTC)
        return (self.now() - refreshed).total_seconds()


async def _subject_id(session: AsyncSession, user_id: str, *, create: bool) -> UUID | None:
    subject_id = (
        await session.execute(
            select(MemorySubject.id).where(
                MemorySubject.subject_type == SubjectType.PERSON,
                MemorySubject.external_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if subject_id is not None or not create:
        return subject_id
    return (await MemoryService(session).actor_for_external_id(user_id)).id

