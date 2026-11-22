"""Provider call budget used by every dev tool that can reach Swiggy/Zepto."""

from datetime import UTC, datetime, timedelta

import pytest

from app.devtools.budget import (
    BudgetedOrderHistory,
    ProviderBudgetExceeded,
    ProviderCallBudget,
    ProviderCooldown,
    refuse_if_cooling_down,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


async def test_budget_caps_calls_and_spaces_them() -> None:
    clock = FakeClock()
    budget = ProviderCallBudget(max_calls=3, min_spacing=1.0, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        await budget.spend("zepto", "list_order_history")
    assert clock.slept == [1.0, 1.0]
    assert budget.remaining == 0
    with pytest.raises(ProviderBudgetExceeded, match="no further call"):
        await budget.spend("zepto", "get_order_detail")
    assert len(budget.calls) == 3


async def test_default_budget_is_five_calls_a_second_apart() -> None:
    budget = ProviderCallBudget()
    assert (budget.max_calls, budget.min_spacing) == (5, 1.0)


def test_a_recorded_cooldown_refuses_before_any_call() -> None:
    now = datetime(2026, 11, 22, tzinfo=UTC)
    with pytest.raises(ProviderCooldown, match="no call was made"):
        refuse_if_cooling_down("Zepto", now + timedelta(seconds=40), now)
    refuse_if_cooling_down("Zepto", now - timedelta(seconds=1), now)
    refuse_if_cooling_down("Zepto", None, now)


class History:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def order_history(self, *, limit: int = 10, page_number: int = 1):
        self.calls.append("history")
        return []

    async def order_detail(self, order_id: str):
        self.calls.append("detail")
        return {}


async def test_budgeted_history_stops_before_the_provider() -> None:
    clock = FakeClock()
    inner = History()
    history = BudgetedOrderHistory(inner, ProviderCallBudget(max_calls=2, clock=clock, sleep=clock.sleep))
    await history.order_history()
    await history.order_detail("o1")
    with pytest.raises(ProviderBudgetExceeded):
        await history.order_detail("o2")
    assert inner.calls == ["history", "detail"]
