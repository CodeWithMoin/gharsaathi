"""Provider call budget for dev tools: never get the owner's store accounts rate-limited.

Every Swiggy/Zepto call a dev tool makes goes through ``ProviderCallBudget``:
at most ``max_calls`` per command (default 5) and at least ``min_spacing``
seconds between two calls (default 1 s). A recorded store cooldown refuses the
command before any call. Only read-only tools are ever wrapped; dev tools have
no cart, address, payment or order path at all.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

DEFAULT_MAX_CALLS = 5
DEFAULT_MIN_SPACING_SECONDS = 1.0


class ProviderBudgetExceeded(Exception):
    """Raised instead of making a call; the message is safe to print."""


class ProviderCooldown(ProviderBudgetExceeded):
    pass


class ProviderCallBudget:
    def __init__(
        self,
        max_calls: int = DEFAULT_MAX_CALLS,
        min_spacing: float = DEFAULT_MIN_SPACING_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.max_calls = max_calls
        self.min_spacing = min_spacing
        self.clock = clock
        self.sleep = sleep
        self.calls: list[tuple[str, str]] = []
        self._last: float | None = None
        self._lock = asyncio.Lock()

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - len(self.calls))

    async def spend(self, provider: str, tool: str) -> None:
        """Reserve one call, waiting out the spacing; raises when the budget is used up."""

        async with self._lock:
            if len(self.calls) >= self.max_calls:
                raise ProviderBudgetExceeded(
                    f"Stopped: dev-tool budget of {self.max_calls} {provider} calls per command is used up "
                    f"(no further call was made)."
                )
            if self._last is not None:
                wait = self.min_spacing - (self.clock() - self._last)
                if wait > 0:
                    await self.sleep(wait)
            self._last = self.clock()
            self.calls.append((provider, tool))


def refuse_if_cooling_down(provider: str, rate_limited_until: datetime | None, now: datetime | None = None) -> None:
    """A store cooldown recorded in our own connection file means: no call at all."""

    if rate_limited_until is None:
        return
    if rate_limited_until.tzinfo is None:
        rate_limited_until = rate_limited_until.replace(tzinfo=UTC)
    left = (rate_limited_until - (now or datetime.now(UTC))).total_seconds()
    if left > 0:
        raise ProviderCooldown(
            f"{provider} has a recorded rate-limit cooldown ({int(left)} s left); no call was made. Try again later."
        )


class BudgetedOrderHistory:
    """The two read-only Zepto history tools the reconciler uses, behind a budget."""

    PROVIDER = "zepto"

    def __init__(self, inner: Any, budget: ProviderCallBudget) -> None:
        self._inner = inner
        self.budget = budget

    async def order_history(self, *, limit: int = 10, page_number: int = 1) -> Any:
        await self.budget.spend(self.PROVIDER, "list_order_history")
        return await self._inner.order_history(limit=limit, page_number=page_number)

    async def order_detail(self, order_id: str) -> Any:
        await self.budget.spend(self.PROVIDER, "get_order_detail")
        return await self._inner.order_detail(order_id)
