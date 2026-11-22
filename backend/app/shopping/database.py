from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import ShoppingApprovalRecord, ShoppingBasketRecord
from app.shopping.errors import BasketConflict, InvalidBasketChange
from app.shopping.models import (
    OPEN_APPROVAL_STATUSES as _OPEN_APPROVALS,
    OPEN_BASKET_STATUSES as _OPEN_BASKETS,
    Approval,
    ApprovalStatus,
    Basket,
    BasketStatus,
    ProviderName,
)


OPEN_BASKET_STATUSES = tuple(sorted(status.value for status in _OPEN_BASKETS))
OPEN_APPROVAL_STATUSES = tuple(sorted(status.value for status in _OPEN_APPROVALS))
FROZEN_BASKET_STATUSES = (BasketStatus.CHECKOUT_PENDING.value, BasketStatus.ORDERED.value)
CREATE_OPEN_ATTEMPTS = 3


def _basket_values(basket: Basket) -> dict:
    values = basket.model_dump(mode="python")
    values["lines"] = [line.model_dump(mode="json") for line in basket.lines]
    return values


class DatabaseBasketRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, basket_id: str) -> Basket | None:
        async with self.sessions() as session:
            record = await session.get(ShoppingBasketRecord, basket_id)
            return _basket(record) if record else None

    async def save(self, basket: Basket) -> None:
        async with self.sessions() as session, session.begin():
            record = await session.get(ShoppingBasketRecord, basket.id)
            values = _basket_values(basket)
            if record is None:
                session.add(ShoppingBasketRecord(**values))
                return
            for key, value in values.items():
                setattr(record, key, value)

    async def save_if_editable(self, basket: Basket, *, expected_revision: int) -> None:
        """Compare-and-set a user edit and supersede open approvals in one transaction.

        The ``revision`` predicate makes the UPDATE the serialisation point for
        every writer, in any process. Superseding in the same transaction means
        no committed edit can coexist with an open approval for its basket.
        """

        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(ShoppingBasketRecord)
                .where(
                    ShoppingBasketRecord.id == basket.id,
                    ShoppingBasketRecord.revision == expected_revision,
                    ShoppingBasketRecord.status.notin_(FROZEN_BASKET_STATUSES),
                )
                .values(**_basket_values(basket))
            )
            if result.rowcount != 1:
                status = await session.scalar(
                    select(ShoppingBasketRecord.status).where(
                        ShoppingBasketRecord.id == basket.id
                    )
                )
                if status in FROZEN_BASKET_STATUSES:
                    raise InvalidBasketChange(
                        "This basket changed or checkout already started. Refresh before editing."
                    )
                raise BasketConflict("This basket changed. Refresh before editing.")
            await session.execute(
                update(ShoppingApprovalRecord)
                .where(
                    ShoppingApprovalRecord.basket_id == basket.id,
                    ShoppingApprovalRecord.status.in_(OPEN_APPROVAL_STATUSES),
                )
                .values(status=ApprovalStatus.SUPERSEDED.value, decided_at=basket.updated_at)
            )

    async def transition(
        self,
        basket_id: str,
        *,
        expected_revision: int,
        from_statuses: Collection[BasketStatus],
        to_status: BasketStatus,
    ) -> bool:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(ShoppingBasketRecord)
                .where(
                    ShoppingBasketRecord.id == basket_id,
                    ShoppingBasketRecord.revision == expected_revision,
                    ShoppingBasketRecord.status.in_([status.value for status in from_statuses]),
                )
                .values(status=to_status.value)
            )
            return result.rowcount == 1

    async def create_open(self, basket: Basket) -> Basket:
        """Get-or-create guarded by ``uq_shopping_baskets_one_open``."""

        for _ in range(CREATE_OPEN_ATTEMPTS):
            existing = await self.active_for(basket.household_id, basket.provider)
            if existing is not None:
                return existing
            try:
                async with self.sessions() as session, session.begin():
                    session.add(ShoppingBasketRecord(**_basket_values(basket)))
                return basket
            except IntegrityError:
                # A concurrent request created the open basket first.
                continue
        raise BasketConflict("This basket changed. Refresh before editing.")

    async def active_for(self, household_id: str, provider: ProviderName) -> Basket | None:
        async with self.sessions() as session:
            record = await session.scalar(
                select(ShoppingBasketRecord)
                .where(
                    ShoppingBasketRecord.household_id == household_id,
                    ShoppingBasketRecord.provider == provider,
                    ShoppingBasketRecord.status.in_(OPEN_BASKET_STATUSES),
                )
                .order_by(ShoppingBasketRecord.updated_at.desc())
                .limit(1)
            )
            return _basket(record) if record else None


class DatabaseApprovalRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, approval_id: str) -> Approval | None:
        async with self.sessions() as session:
            record = await session.get(ShoppingApprovalRecord, approval_id)
            return _approval(record) if record else None

    async def save(self, approval: Approval) -> None:
        async with self.sessions() as session, session.begin():
            record = await session.get(ShoppingApprovalRecord, approval.id)
            values = approval.model_dump(mode="python", exclude={"quote"})
            values["quote"] = approval.quote.model_dump(mode="json") if approval.quote else None
            if record is None:
                session.add(ShoppingApprovalRecord(**values))
                return
            for key, value in values.items():
                setattr(record, key, value)

    async def for_basket(self, basket_id: str) -> list[Approval]:
        async with self.sessions() as session:
            records = (
                await session.scalars(
                    select(ShoppingApprovalRecord).where(
                        ShoppingApprovalRecord.basket_id == basket_id
                    )
                )
            ).all()
            return [_approval(record) for record in records]

    async def transition(
        self,
        approval_id: str,
        *,
        from_statuses: Collection[ApprovalStatus],
        to_status: ApprovalStatus,
        decided_at: datetime,
    ) -> bool:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(ShoppingApprovalRecord)
                .where(
                    ShoppingApprovalRecord.id == approval_id,
                    ShoppingApprovalRecord.status.in_([status.value for status in from_statuses]),
                )
                .values(status=to_status.value, decided_at=decided_at)
            )
            return result.rowcount == 1

    async def supersede_open(self, basket_id: str, decided_at: datetime) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(ShoppingApprovalRecord)
                .where(
                    ShoppingApprovalRecord.basket_id == basket_id,
                    ShoppingApprovalRecord.status.in_(OPEN_APPROVAL_STATUSES),
                )
                .values(status=ApprovalStatus.SUPERSEDED.value, decided_at=decided_at)
            )


def _basket(record: ShoppingBasketRecord) -> Basket:
    return Basket.model_validate(
        {
            "id": record.id,
            "household_id": record.household_id,
            "provider": record.provider,
            "address_id": record.address_id,
            "status": record.status,
            "revision": record.revision,
            "lines": record.lines,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


basket_from_record = _basket


def _approval(record: ShoppingApprovalRecord) -> Approval:
    return Approval.model_validate(
        {
            "id": record.id,
            "basket_id": record.basket_id,
            "basket_revision": record.basket_revision,
            "fingerprint": record.fingerprint,
            "payable_paise": record.payable_paise,
            "status": record.status,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "decided_at": record.decided_at,
            "quote": record.quote,
        }
    )
