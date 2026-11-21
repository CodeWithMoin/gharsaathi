from collections.abc import Collection
from datetime import datetime
from typing import Protocol

from app.shopping.models import (
    Approval,
    ApprovalStatus,
    Basket,
    BasketStatus,
    ProviderName,
    Quote,
)


class BasketRepository(Protocol):
    async def get(self, basket_id: str) -> Basket | None: ...
    async def save(self, basket: Basket) -> None: ...

    async def save_if_editable(self, basket: Basket, *, expected_revision: int) -> None:
        """Compare-and-set a user edit.

        Raises ``BasketConflict`` if the stored revision is not
        ``expected_revision`` and ``InvalidBasketChange`` if checkout froze it.
        """

    async def transition(
        self,
        basket_id: str,
        *,
        expected_revision: int,
        from_statuses: Collection[BasketStatus],
        to_status: BasketStatus,
    ) -> bool:
        """Atomically change status only if revision and status still match."""

    async def create_open(self, basket: Basket) -> Basket:
        """Insert ``basket`` as the open basket, or return the one that already won."""

    async def active_for(self, household_id: str, provider: ProviderName) -> Basket | None: ...


class ApprovalRepository(Protocol):
    async def get(self, approval_id: str) -> Approval | None: ...
    async def save(self, approval: Approval) -> None: ...
    async def for_basket(self, basket_id: str) -> list[Approval]: ...

    async def transition(
        self,
        approval_id: str,
        *,
        from_statuses: Collection[ApprovalStatus],
        to_status: ApprovalStatus,
        decided_at: datetime,
    ) -> bool:
        """Atomically change status only if the stored status is in ``from_statuses``."""

    async def supersede_open(self, basket_id: str, decided_at: datetime) -> None:
        """Atomically supersede every pending or approved approval of a basket."""


class QuoteProvider(Protocol):
    """A verified provider capability for pricing a local basket.

    The capability intentionally excludes cart mutation and checkout.
    """

    name: ProviderName

    async def quote(self, basket: Basket) -> Quote: ...
