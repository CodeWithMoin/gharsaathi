"""Voice and chat context read the bill shown on screen without re-quoting it."""

import json

import pytest

from app.conversations.context import basket_views, recent_context_from_state
from app.shopping.models import ApprovalStatus
from app.voice.basket_tools import BasketStore, BasketToolExecutor
from tests.shopping_fakes import HOUSEHOLD, PROVIDER, World


def voice_session(world: World) -> BasketToolExecutor:
    """A fresh voice session: it has requested no bill itself."""

    return BasketToolExecutor(
        world.baskets, world.approvals, {}, [BasketStore(PROVIDER, "Swiggy Instamart", "addr_home")], HOUSEHOLD
    )


async def view(executor: BasketToolExecutor) -> dict:
    return (await executor.execute("view_basket", json.dumps({"store": PROVIDER}))).model_output


async def on_screen_bill(world: World):
    """What the "Get final bill" button does: POST /api/basket/approval."""

    basket = await world.ready_basket()
    return await world.approvals.request(basket.id)


@pytest.mark.asyncio
async def test_view_basket_reads_a_bill_opened_on_screen_without_requoting() -> None:
    world = World()
    result = await on_screen_bill(world)
    quotes = world.provider.quotes

    output = await view(voice_session(world))

    bill = output["current_bill"]
    assert bill["payable"] == "₹184"
    assert bill["item_total"] == "₹154"
    assert bill["charges"] == [{"label": "Delivery fee", "amount": "₹30"}]
    assert bill["approval_status"] == "pending"
    assert bill["expires_at"] == result.approval.expires_at.isoformat()
    assert bill["promo_code_entry"] == "not_available"
    assert "bill_offers" in bill
    assert world.provider.quotes == quotes  # read only: no new quote, nothing superseded
    [approval] = await world.approval_repo.for_basket(result.basket.id)
    assert approval.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_view_basket_reports_approved_and_expired_bills() -> None:
    world = World()
    result = await on_screen_bill(world)
    await world.approvals.approve(result.approval.id, result.approval.fingerprint)
    assert (await view(voice_session(world)))["current_bill"]["approval_status"] == "approved"

    expired = World()
    await on_screen_bill(expired)
    expired.clock.advance(minutes=11)
    assert (await view(voice_session(expired)))["current_bill"]["approval_status"] == "expired"


@pytest.mark.asyncio
async def test_no_bill_when_the_basket_changed_after_it() -> None:
    world = World()
    await on_screen_bill(world)
    session = voice_session(world)
    await world.baskets.set_quantity(HOUSEHOLD, PROVIDER, "spin1", 2)
    assert "current_bill" not in await view(session)


@pytest.mark.asyncio
async def test_chat_context_lists_the_on_screen_bill_per_store() -> None:
    world = World()
    result = await on_screen_bill(world)
    views = await basket_views(world.basket_repo, world.approval_repo, HOUSEHOLD)
    assert [view.approval.id for view in views if view.approval] == [result.approval.id]
    context = recent_context_from_state({}, views, now=world.clock())
    assert context.on_screen == (
        "Swiggy Instamart basket: 1 x Item spin1; items ₹154; bill item total ₹154, payable ₹184, "
        "awaiting on-screen approval, expires in 10 min; no store offers",
    )
    # Another household sees nothing.
    assert await basket_views(world.basket_repo, world.approval_repo, "someone-else") == []


@pytest.mark.asyncio
async def test_database_keeps_the_quote_with_the_approval(tmp_path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database.base import Base
    from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bill.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    world = World()
    world.basket_repo = DatabaseBasketRepository(sessions)  # type: ignore[assignment]
    world.approval_repo = DatabaseApprovalRepository(sessions)  # type: ignore[assignment]
    world.baskets.baskets = world.basket_repo
    world.baskets.approvals = world.approval_repo
    world.approvals.baskets = world.basket_repo
    world.approvals.approvals = world.approval_repo
    try:
        result = await on_screen_bill(world)
        [stored] = await world.approval_repo.for_basket(result.basket.id)
        assert stored.quote == result.quote
        assert (await view(voice_session(world)))["current_bill"]["payable"] == "₹184"
    finally:
        await engine.dispose()
