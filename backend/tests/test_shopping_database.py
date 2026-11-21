from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.models import Approval, Basket, BasketStatus


@pytest.mark.asyncio
async def test_baskets_and_approvals_survive_repository_restart_and_are_user_scoped(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shopping.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    basket = Basket(
        id="basket-1",
        household_id="user-a",
        provider="swiggy_instamart",
        status=BasketStatus.AWAITING_APPROVAL,
        revision=2,
        created_at=now,
        updated_at=now,
    )
    await DatabaseBasketRepository(sessions).save(basket)
    approval = Approval(
        id="approval-1",
        basket_id=basket.id,
        basket_revision=basket.revision,
        fingerprint="a" * 64,
        payable_paise=12345,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    await DatabaseApprovalRepository(sessions).save(approval)

    restarted_baskets = DatabaseBasketRepository(sessions)
    restarted_approvals = DatabaseApprovalRepository(sessions)
    assert (await restarted_baskets.active_for("user-a", "swiggy_instamart")).id == basket.id
    assert await restarted_baskets.active_for("user-b", "swiggy_instamart") is None
    assert (await restarted_approvals.get(approval.id)).payable_paise == 12345
    await engine.dispose()
