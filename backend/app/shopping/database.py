from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import ShoppingApprovalRecord, ShoppingBasketRecord
from app.shopping.errors import InvalidBasketChange
from app.shopping.models import Approval, Basket, BasketStatus, ProviderName


OPEN_BASKET_STATUSES = ("draft", "awaiting_approval", "approved", "checkout_pending")


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
            values = basket.model_dump(mode="python")
            values["lines"] = [line.model_dump(mode="json") for line in basket.lines]
            if record is None:
                session.add(ShoppingBasketRecord(**values))
                return
            for key, value in values.items():
                setattr(record, key, value)

    async def save_if_editable(self, basket: Basket, *, expected_revision: int) -> None:
        """Optimistically save a user edit without thawing a checkout claim."""

        values = basket.model_dump(mode="python")
        values["lines"] = [line.model_dump(mode="json") for line in basket.lines]
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(ShoppingBasketRecord)
                .where(
                    ShoppingBasketRecord.id == basket.id,
                    ShoppingBasketRecord.revision == expected_revision,
                    ShoppingBasketRecord.status.notin_(
                        (BasketStatus.CHECKOUT_PENDING.value, BasketStatus.ORDERED.value)
                    ),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise InvalidBasketChange(
                    "This basket changed or checkout already started. Refresh before editing."
                )

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
            values = approval.model_dump(mode="python")
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
        }
    )
