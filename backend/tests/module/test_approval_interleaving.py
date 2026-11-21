"""BasketService + ApprovalService together, with an edit interleaved mid-transition.

Basket edits (PATCH /api/basket/lines, voice set_basket_quantity) do not take
the checkout workflow lock. Each repository call is an await point, so on one
event loop an edit can land between the approval's reads and its writes. The
writes are compare-and-set on the basket revision and approval status, so the
edit must survive and the approval must not end APPROVED for the old basket.
"""

from collections.abc import Awaitable, Callable, Collection
from datetime import datetime

import pytest

from app.shopping.approval import ApprovalService
from app.shopping.errors import ApprovalRefused
from app.shopping.memory import InMemoryApprovalRepository
from app.shopping.models import Approval, ApprovalStatus, BasketStatus
from tests.shopping_fakes import HOUSEHOLD, PROVIDER, World


class InterleavingApprovals(InMemoryApprovalRepository):
    """Runs a concurrent user action just before an approval is written."""

    def __init__(self) -> None:
        super().__init__()
        self.before_save: dict[ApprovalStatus, Callable[[], Awaitable[None]]] = {}

    async def _before(self, status: ApprovalStatus) -> None:
        hook = self.before_save.pop(status, None)
        if hook is not None:
            await hook()

    async def save(self, approval: Approval) -> None:
        await self._before(approval.status)
        await super().save(approval)

    async def transition(
        self,
        approval_id: str,
        *,
        from_statuses: Collection[ApprovalStatus],
        to_status: ApprovalStatus,
        decided_at: datetime,
    ) -> bool:
        await self._before(to_status)
        return await super().transition(
            approval_id, from_statuses=from_statuses, to_status=to_status, decided_at=decided_at
        )


def _world() -> tuple[World, InterleavingApprovals]:
    world = World()
    approvals = InterleavingApprovals()
    world.approval_repo = approvals
    world.baskets.approvals = approvals
    world.approvals = ApprovalService(world.basket_repo, approvals, lambda _: world.provider, world.clock)
    return world, approvals


async def _edit_to_quantity_two(world: World) -> None:
    edited = await world.baskets.set_quantity(HOUSEHOLD, PROVIDER, "spin1", 2)
    assert edited.lines[0].quantity == 2  # the user is told the edit succeeded


async def test_edit_during_approve_is_not_lost() -> None:
    world, approvals = _world()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    approvals.before_save[ApprovalStatus.APPROVED] = lambda: _edit_to_quantity_two(world)

    # The user approved a bill for a basket that changed underneath them: refuse.
    with pytest.raises(ApprovalRefused, match="basket changed"):
        await world.approvals.approve(requested.approval.id, requested.approval.fingerprint)

    stored = await world.basket_repo.get(basket.id)
    approval = await world.approval_repo.get(requested.approval.id)
    assert stored is not None and approval is not None
    assert stored.lines[0].quantity == 2
    assert approval.status != ApprovalStatus.APPROVED or stored.revision == approval.basket_revision


async def test_edit_during_bill_request_is_not_lost() -> None:
    world, approvals = _world()
    basket = await world.ready_basket()
    approvals.before_save[ApprovalStatus.PENDING] = lambda: _edit_to_quantity_two(world)

    requested = await world.approvals.request(basket.id)

    stored = await world.basket_repo.get(basket.id)
    assert stored is not None
    assert stored.lines[0].quantity == 2
    # The edit forced a re-quote; the bill shown is for the edited basket.
    assert world.provider.quotes == 2
    assert requested.approval.basket_revision == stored.revision
    assert requested.basket.lines[0].quantity == 2
    stale = [
        item
        for item in await world.approval_repo.for_basket(basket.id)
        if item.id != requested.approval.id
    ]
    assert [item.status for item in stale] == [ApprovalStatus.SUPERSEDED]


async def test_edit_between_requests_supersedes_without_interleaving() -> None:
    """Control case: the same edit, not interleaved, is handled correctly."""

    world, _ = _world()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    await _edit_to_quantity_two(world)

    stored = await world.basket_repo.get(basket.id)
    approval = await world.approval_repo.get(requested.approval.id)
    assert stored is not None and stored.status == BasketStatus.DRAFT
    assert approval is not None and approval.status == ApprovalStatus.SUPERSEDED
