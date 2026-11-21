from collections.abc import Collection
from datetime import datetime

from app.shopping.errors import BasketConflict, InvalidBasketChange
from app.shopping.models import (
    OPEN_APPROVAL_STATUSES,
    OPEN_BASKET_STATUSES,
    Approval,
    ApprovalStatus,
    Basket,
    BasketStatus,
    ProviderName,
)


class InMemoryBasketRepository:
    """Local-development repository; replace with PostgreSQL before multi-user use.

    Each method is free of await points, so on one event loop it is atomic.
    """

    def __init__(self) -> None:
        self._items: dict[str, Basket] = {}

    async def get(self, basket_id: str) -> Basket | None:
        basket = self._items.get(basket_id)
        return basket.model_copy(deep=True) if basket else None

    async def save(self, basket: Basket) -> None:
        self._items[basket.id] = basket.model_copy(deep=True)

    async def save_if_editable(self, basket: Basket, *, expected_revision: int) -> None:
        current = self._items.get(basket.id)
        if current is not None and current.status in {
            BasketStatus.CHECKOUT_PENDING,
            BasketStatus.ORDERED,
        }:
            raise InvalidBasketChange(
                "This basket changed or checkout already started. Refresh before editing."
            )
        if current is None or current.revision != expected_revision:
            raise BasketConflict("This basket changed. Refresh before editing.")
        self._items[basket.id] = basket.model_copy(deep=True)

    async def transition(
        self,
        basket_id: str,
        *,
        expected_revision: int,
        from_statuses: Collection[BasketStatus],
        to_status: BasketStatus,
    ) -> bool:
        current = self._items.get(basket_id)
        if (
            current is None
            or current.revision != expected_revision
            or current.status not in from_statuses
        ):
            return False
        current.status = to_status
        return True

    async def create_open(self, basket: Basket) -> Basket:
        existing = await self.active_for(basket.household_id, basket.provider)
        if existing is not None:
            return existing
        self._items[basket.id] = basket.model_copy(deep=True)
        return basket.model_copy(deep=True)

    async def active_for(self, household_id: str, provider: ProviderName) -> Basket | None:
        candidates = [
            basket
            for basket in self._items.values()
            if basket.household_id == household_id
            and basket.provider == provider
            and basket.status in OPEN_BASKET_STATUSES
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda basket: basket.updated_at).model_copy(deep=True)


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self._items: dict[str, Approval] = {}

    async def get(self, approval_id: str) -> Approval | None:
        approval = self._items.get(approval_id)
        return approval.model_copy(deep=True) if approval else None

    async def save(self, approval: Approval) -> None:
        self._items[approval.id] = approval.model_copy(deep=True)

    async def for_basket(self, basket_id: str) -> list[Approval]:
        return [
            approval.model_copy(deep=True)
            for approval in self._items.values()
            if approval.basket_id == basket_id
        ]

    async def transition(
        self,
        approval_id: str,
        *,
        from_statuses: Collection[ApprovalStatus],
        to_status: ApprovalStatus,
        decided_at: datetime,
    ) -> bool:
        current = self._items.get(approval_id)
        if current is None or current.status not in from_statuses:
            return False
        current.status = to_status
        current.decided_at = decided_at
        return True

    async def supersede_open(self, basket_id: str, decided_at: datetime) -> None:
        for approval in self._items.values():
            if approval.basket_id == basket_id and approval.status in OPEN_APPROVAL_STATUSES:
                approval.status = ApprovalStatus.SUPERSEDED
                approval.decided_at = decided_at
