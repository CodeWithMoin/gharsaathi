"""The app's order paths call the browser-notification hooks once, at the right time.

Reuses the in-memory worlds of the order-confirmation and reconciliation tests
with a recorder in place of ``PushEvents``. No provider call is possible.
"""

from __future__ import annotations

from typing import Any

from app.shopping.models import CheckoutStatus
from tests.module import test_checkout_reconciliation as reconciliation
from tests.module import test_order_confirmation as confirmation


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def checkout_outcome(self, attempt: Any, **kwargs: Any) -> None:
        self.calls.append(("checkout_outcome", attempt, kwargs))

    def order_still_unknown(self, attempt: Any, **kwargs: Any) -> None:
        self.calls.append(("order_still_unknown", attempt, kwargs))

    def card_prepared(self, card: Any) -> None:
        self.calls.append(("card_prepared", card, {}))

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]


async def test_tap_prepares_one_reminder_and_announces_the_placed_order() -> None:
    world = confirmation.World()
    recorder = Recorder()
    world.confirmations.push = recorder
    world.checkout.push = recorder
    await world.fill()
    toolbox = world.toolbox()
    _, event = await confirmation.prepare(world, toolbox)
    assert recorder.names() == ["card_prepared"]
    assert recorder.calls[0][1].state == "open"

    result = await world.confirmations.place(confirmation.USER, event["confirmation"]["id"], world.checkout)

    assert result.status == "confirmed"
    assert recorder.names() == ["card_prepared", "checkout_outcome"]
    attempt = recorder.calls[1][1]
    assert attempt.status == CheckoutStatus.CONFIRMED and attempt.provider_order_id == "ZEP-1"
    # The used card no longer reminds (PushEvents checks state at send time).
    assert recorder.calls[0][1].state == "used"


async def test_reconciled_order_is_announced_as_an_earlier_order() -> None:
    world = reconciliation.World()
    recorder = Recorder()
    world.reconciler.push = recorder
    world.client.add("ord-live", reconciliation.CODE, world.placed_iso())
    result, _ = await world.unknown_order(world.toolbox())
    await world.settle()

    outcomes = [(name, attempt.status, kwargs) for name, attempt, kwargs in recorder.calls]
    assert outcomes == [("checkout_outcome", CheckoutStatus.CONFIRMED, {"earlier": True})]
    assert recorder.calls[0][1].id == result.checkout_id


async def test_still_unknown_after_the_check_window_is_announced_once() -> None:
    world = reconciliation.World()
    recorder = Recorder()
    world.reconciler.push = recorder
    world.client.add("ord-other", "OTHER00001", world.placed_iso(), total=9900)
    await world.unknown_order(world.toolbox())
    await world.settle()

    assert recorder.names() == ["order_still_unknown"]
    assert recorder.calls[0][1].status == CheckoutStatus.OUTCOME_UNKNOWN


async def test_unknown_outcome_at_the_tap_is_not_announced_as_placed() -> None:
    world = confirmation.World()
    recorder = Recorder()
    world.checkout.push = recorder
    world.gateway.fail = True
    await world.fill()
    _, event = await confirmation.prepare(world, world.toolbox())
    result = await world.confirmations.place(confirmation.USER, event["confirmation"]["id"], world.checkout)
    assert result.status == "outcome_unknown"
    # The hook sees the unknown attempt; PushEvents sends nothing for it.
    assert [attempt.status for _, attempt, _ in recorder.calls] == [CheckoutStatus.OUTCOME_UNKNOWN]
