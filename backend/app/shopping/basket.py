import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.shopping.errors import InvalidBasketChange, NotFound
from app.shopping.models import (
    ApprovalStatus,
    Basket,
    BasketLine,
    BasketStatus,
    ProductRef,
    ProviderName,
)
from app.shopping.ports import ApprovalRepository, BasketRepository


MIN_QUANTITY = 1
MAX_QUANTITY = 50
OPEN_APPROVAL_STATUSES = {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def line_total_paise(line: BasketLine) -> int:
    return line.unit_price_paise * line.quantity


def item_total_paise(basket: Basket) -> int:
    return sum(line_total_paise(line) for line in basket.lines)


def line_savings_paise(line: BasketLine) -> int:
    if line.mrp_paise is None or line.mrp_paise <= line.unit_price_paise:
        return 0
    return (line.mrp_paise - line.unit_price_paise) * line.quantity


def savings_vs_mrp_paise(basket: Basket) -> int:
    return sum(line_savings_paise(line) for line in basket.lines)


def format_rupees(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    fraction = f".{remainder:02d}" if remainder else ""
    return f"{sign}₹{digits}{fraction}"


def same_variant(a: ProductRef, b: ProductRef) -> bool:
    return (a.provider, a.product_id, a.variant_id) == (b.provider, b.product_id, b.variant_id)


def _validate_quantity(quantity: int) -> None:
    if not MIN_QUANTITY <= quantity <= MAX_QUANTITY:
        raise InvalidBasketChange(
            f"Quantity must be between {MIN_QUANTITY} and {MAX_QUANTITY}."
        )


class BasketService:
    def __init__(
        self,
        baskets: BasketRepository,
        approvals: ApprovalRepository,
        clock: Clock = utc_now,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.clock = clock

    async def active(self, household_id: str, provider: ProviderName) -> Basket:
        basket = await self.baskets.active_for(household_id, provider)
        if basket is not None:
            return basket
        now = self.clock()
        basket = Basket(
            id=uuid.uuid4().hex,
            household_id=household_id,
            provider=provider,
            created_at=now,
            updated_at=now,
        )
        await self.baskets.save(basket)
        return basket

    async def add_line(
        self, household_id: str, provider: ProviderName, line: BasketLine
    ) -> Basket:
        basket = await self.active(household_id, provider)
        self._require_editable(basket)
        if line.ref.provider != basket.provider:
            raise InvalidBasketChange("This item belongs to a different store.")
        _validate_quantity(line.quantity)
        existing = next((item for item in basket.lines if same_variant(item.ref, line.ref)), None)
        if existing is None:
            basket.lines.append(line.model_copy(deep=True))
        else:
            quantity = existing.quantity + line.quantity
            _validate_quantity(quantity)
            basket.lines[basket.lines.index(existing)] = line.model_copy(
                update={"quantity": quantity}, deep=True
            )
        return await self._commit(basket)

    async def set_quantity(
        self, household_id: str, provider: ProviderName, variant_id: str, quantity: int
    ) -> Basket:
        basket = await self.active(household_id, provider)
        self._require_editable(basket)
        index = self._index(basket, variant_id)
        if quantity == 0:
            del basket.lines[index]
            return await self._commit(basket)
        _validate_quantity(quantity)
        if basket.lines[index].quantity == quantity:
            return basket
        basket.lines[index].quantity = quantity
        return await self._commit(basket)

    async def remove_line(
        self, household_id: str, provider: ProviderName, variant_id: str
    ) -> Basket:
        basket = await self.active(household_id, provider)
        self._require_editable(basket)
        del basket.lines[self._index(basket, variant_id)]
        return await self._commit(basket)

    async def set_address(
        self, household_id: str, provider: ProviderName, address_id: str
    ) -> Basket:
        basket = await self.active(household_id, provider)
        self._require_editable(basket)
        if basket.address_id == address_id:
            return basket
        basket.address_id = address_id
        return await self._commit(basket)

    @staticmethod
    def _index(basket: Basket, variant_id: str) -> int:
        for index, line in enumerate(basket.lines):
            if line.ref.variant_id == variant_id:
                return index
        raise NotFound("That item is not in the basket.")

    async def _commit(self, basket: Basket) -> Basket:
        now = self.clock()
        expected_revision = basket.revision
        basket.revision += 1
        basket.updated_at = now
        basket.status = BasketStatus.DRAFT
        await self.baskets.save_if_editable(
            basket, expected_revision=expected_revision
        )
        for approval in await self.approvals.for_basket(basket.id):
            if approval.status in OPEN_APPROVAL_STATUSES:
                approval.status = ApprovalStatus.SUPERSEDED
                approval.decided_at = now
                await self.approvals.save(approval)
        return basket

    @staticmethod
    def _require_editable(basket: Basket) -> None:
        if basket.status in {BasketStatus.CHECKOUT_PENDING, BasketStatus.ORDERED}:
            raise InvalidBasketChange(
                "Checkout already started for this basket. Do not create or submit it again."
            )
