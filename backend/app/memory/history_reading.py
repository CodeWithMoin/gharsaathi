"""Shared contract for conservative, read-only order-history readers.

A reader makes a small, spaced, never-retried number of provider calls and
returns what it got plus content-free counters. It stops on the first rate
limit or session problem and hands the error back instead of raising, so the
orders it already read are kept.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Literal, Protocol

from app.commerce.swiggy.models import OrderSummary


Coverage = Literal["last_15_days", "full_history"]


@dataclass(frozen=True)
class HistoryReadPlan:
    known_ids: frozenset[str]
    cutoff: datetime
    cutoff_reason: Literal["lookback", "retention"]
    start_page: int = 1
    # Stop at the first already-imported order (newest-first lists only).
    incremental: bool = False


@dataclass
class HistoryRead:
    coverage: Coverage
    orders: list[OrderSummary] = field(default_factory=list)
    list_calls: int = 0
    detail_calls: int = 0
    skipped_known: int = 0
    detail_failures: int = 0
    stopped_reason: str = "complete"
    # Where the next sync resumes; ``None`` once the window was walked fully.
    next_page: int | None = None
    error: Exception | None = None


class HistoryReader(Protocol):
    async def read_history(self, plan: HistoryReadPlan) -> HistoryRead: ...


class CallPacer:
    """Keeps at least ``min_interval`` seconds between consecutive provider calls."""

    def __init__(
        self,
        min_interval: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._last: float | None = None

    async def wait(self) -> None:
        if self._last is not None:
            remaining = self._last + self.min_interval - self._monotonic()
            if remaining > 0:
                await self._sleep(remaining)
        self._last = self._monotonic()


def stop_reason_for(exc: Exception) -> str:
    """A content-free category for why a read stopped early."""

    name = type(exc).__name__
    if "RateLimited" in name:
        return "rate_limited"
    if "Session" in name or "Authentication" in name:
        return "session_error"
    return "provider_error"
