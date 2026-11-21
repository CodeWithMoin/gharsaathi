from app.shopping.models import Approval, Basket, ProviderName


class InMemoryBasketRepository:
    """Local-development repository; replace with PostgreSQL before multi-user use."""

    def __init__(self) -> None:
        self._items: dict[str, Basket] = {}

    async def get(self, basket_id: str) -> Basket | None:
        basket = self._items.get(basket_id)
        return basket.model_copy(deep=True) if basket else None

    async def save(self, basket: Basket) -> None:
        self._items[basket.id] = basket.model_copy(deep=True)

    async def active_for(self, household_id: str, provider: ProviderName) -> Basket | None:
        candidates = [
            basket
            for basket in self._items.values()
            if basket.household_id == household_id
            and basket.provider == provider
            and basket.status in {"draft", "awaiting_approval", "approved"}
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
