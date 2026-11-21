"""BasketService + ApprovalService together, with an edit interleaved mid-transition.

ApprovalService transitions take the checkout workflow lock, but basket edits
(PATCH /api/basket/lines, voice set_basket_quantity) do not. Each repository
call is an await point, so on one event loop an edit can land between the
approval's read of the basket and its unconditional ``baskets.save``.
"""

from collections.abc import Awaitable, Callable

import pytest

from app.shopping.approval import ApprovalService
from app.shopping.memory import InMemoryApprovalRepository
from app.shopping.models import Approval, ApprovalStatus, BasketStatus
from tests.shopping_fakes import HOUSEHOLD, PROVIDER, World


class InterleavingApprovals(InMemoryApprovalRepository):
    """Runs a concurrent user action just before an approval is written."""

    def __init__(self) -> None:
        super().__init__()
        self.before_save: dict[ApprovalStatus, Callable[[], Awaitable[None]]] = {}

    async def save(self, approval: Approval) -> None:
        hook = self.before_save.pop(approval.status, None)
        if hook is not None:
            await hook()
        await super().save(approval)


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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: ApprovalService._approve saves the basket it read earlier without a "
        "revision check, so an edit committed mid-approval is silently reverted and "
        "the approval ends APPROVED for the pre-edit basket (which checkout would then order)."
    ),
)
async def test_edit_during_approve_is_not_lost() -> None:
    world, approvals = _world()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    approvals.before_save[ApprovalStatus.APPROVED] = lambda: _edit_to_quantity_two(world)

    await world.approvals.approve(requested.approval.id, requested.approval.fingerprint)

    stored = await world.basket_repo.get(basket.id)
    approval = await world.approval_repo.get(requested.approval.id)
    assert stored is not None and approval is not None
    assert stored.lines[0].quantity == 2
    assert approval.status != ApprovalStatus.APPROVED or stored.revision == approval.basket_revision


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: ApprovalService._request saves the basket it re-read before the "
        "approval insert without a revision check, so an edit committed in that "
        "window is reverted and the user's change disappears."
    ),
)
async def test_edit_during_bill_request_is_not_lost() -> None:
    world, approvals = _world()
    basket = await world.ready_basket()
    approvals.before_save[ApprovalStatus.PENDING] = lambda: _edit_to_quantity_two(world)

    await world.approvals.request(basket.id)

    stored = await world.basket_repo.get(basket.id)
    assert stored is not None
    assert stored.lines[0].quantity == 2


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
