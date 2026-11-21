from typing import Protocol

from app.shopping.models import Approval, Basket, ProviderName, Quote


class BasketRepository(Protocol):
    async def get(self, basket_id: str) -> Basket | None: ...
    async def save(self, basket: Basket) -> None: ...
    async def active_for(self, household_id: str, provider: ProviderName) -> Basket | None: ...


class ApprovalRepository(Protocol):
    async def get(self, approval_id: str) -> Approval | None: ...
    async def save(self, approval: Approval) -> None: ...
    async def for_basket(self, basket_id: str) -> list[Approval]: ...


class QuoteProvider(Protocol):
    """A verified provider capability for pricing a local basket.

    The capability intentionally excludes cart mutation and checkout.
    """

    name: ProviderName

    async def quote(self, basket: Basket) -> Quote: ...
