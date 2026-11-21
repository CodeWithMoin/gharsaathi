import asyncio

import pytest

from app.shopping.approval import ApprovalService
from app.shopping.errors import ApprovalRefused, ProviderUnavailable
from app.shopping.models import ApprovalStatus, BasketStatus, basket_fingerprint
from tests.shopping_fakes import DELIVERY_FEE, HOUSEHOLD, PROVIDER, World, line


async def test_request_quotes_and_binds_fingerprint_to_payable() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    approval = requested.approval
    assert approval.status == ApprovalStatus.PENDING
    assert approval.payable_paise == 15400 + DELIVERY_FEE
    assert approval.fingerprint == basket_fingerprint(requested.basket, approval.payable_paise)
    assert (approval.expires_at - approval.created_at).total_seconds() == 600


async def test_request_refused_without_address_or_lines() -> None:
    world = World()
    basket = await world.baskets.add_line(HOUSEHOLD, PROVIDER, line())
    with pytest.raises(ApprovalRefused, match="address"):
        await world.approvals.request(basket.id)


async def test_approve_requires_matching_fingerprint_and_current_basket() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    with pytest.raises(ApprovalRefused, match="does not match"):
        await world.approvals.approve(requested.approval.id, "0" * 64)
    approved = await world.approvals.approve(
        requested.approval.id, requested.approval.fingerprint
    )
    assert approved.status == ApprovalStatus.APPROVED
    stored = await world.basket_repo.get(basket.id)
    assert stored is not None and stored.status == BasketStatus.APPROVED


async def test_approve_refused_after_expiry() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    world.clock.advance(minutes=10)
    with pytest.raises(ApprovalRefused, match="expired"):
        await world.approvals.approve(requested.approval.id, requested.approval.fingerprint)


async def test_reject_returns_basket_to_draft() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    rejected = await world.approvals.reject(requested.approval.id)
    assert rejected.status == ApprovalStatus.REJECTED
    stored = await world.basket_repo.get(basket.id)
    assert stored is not None and stored.status == BasketStatus.DRAFT


async def test_provider_errors_are_normalized() -> None:
    world = World()
    basket = await world.ready_basket()

    async def broken(_):
        raise RuntimeError("upstream down")

    world.provider.quote = broken
    with pytest.raises(ProviderUnavailable):
        await world.approvals.request(basket.id)


async def test_approval_transition_waits_for_shared_checkout_coordinator() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    coordinator = asyncio.Lock()
    coordinated = ApprovalService(
        world.basket_repo,
        world.approval_repo,
        lambda _: world.provider,
        world.clock,
        workflow_lock=coordinator,
    )

    await coordinator.acquire()
    task = asyncio.create_task(
        coordinated.approve(requested.approval.id, requested.approval.fingerprint)
    )
    await asyncio.sleep(0)
    assert not task.done()

    coordinator.release()
    approved = await task
    assert approved.status == ApprovalStatus.APPROVED
