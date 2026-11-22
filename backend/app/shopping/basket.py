import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.shopping.errors import BasketConflict, InvalidBasketChange, NotFound
from app.shopping.models import (
    OPEN_APPROVAL_STATUSES,
    Basket,
    BasketLine,
    BasketStatus,
    ProductRef,
    ProviderName,
    Quote,
)
from app.shopping.ports import ApprovalRepository, BasketRepository


MIN_QUANTITY = 1
MAX_QUANTITY = 50
EDIT_ATTEMPTS = 5
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


def quote_item_total_matches(quote: Quote, basket: Basket) -> bool:
    """Whether the store priced every item as the basket did.

    A provider reports its item total either at selling price, or at MRP with
    the MRP-to-selling gap as a separate item discount. Both must land on the
    basket's selling-price total; at MRP the MRP total must agree too. A discount
    that also folds in a coupon cannot be told apart from a price drop, so it is
    reported as a mismatch.
    """

    selling = item_total_paise(basket)
    if quote.item_total_paise == selling:
        return True
    if quote.item_discount_paise == 0:
        return False
    return (
        quote.item_total_paise == selling + savings_vs_mrp_paise(basket)
        and quote.item_total_paise - quote.item_discount_paise == selling
    )


def same_variant(a: ProductRef, b: ProductRef) -> bool:
    return (a.provider, a.product_id, a.variant_id) == (b.provider, b.product_id, b.variant_id)


def _validate_quantity(quantity: int) -> None:
    if not MIN_QUANTITY <= quantity <= MAX_QUANTITY:
        raise InvalidBasketChange(
            f"Quantity must be between {MIN_QUANTITY} and {MAX_QUANTITY}."
        )


class BasketService:
    """User edits to the open basket.

    Every edit is a compare-and-set on ``Basket.revision``; that single atomic
    write is what serialises edits with quote, approval and checkout in every
    process. An edit that loses the race re-reads the basket and re-applies the
    user's change, so concurrent edits are neither lost nor reported as failed.
    """

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
        return await self.baskets.create_open(
            Basket(
                id=uuid.uuid4().hex,
                household_id=household_id,
                provider=provider,
                created_at=now,
                updated_at=now,
            )
        )

    async def add_line(
        self, household_id: str, provider: ProviderName, line: BasketLine
    ) -> Basket:
        _validate_quantity(line.quantity)

        def change(basket: Basket) -> bool:
            if line.ref.provider != basket.provider:
                raise InvalidBasketChange("This item belongs to a different store.")
            existing = next(
                (item for item in basket.lines if same_variant(item.ref, line.ref)), None
            )
            if existing is None:
                basket.lines.append(line.model_copy(deep=True))
                return True
            quantity = existing.quantity + line.quantity
            _validate_quantity(quantity)
            basket.lines[basket.lines.index(existing)] = line.model_copy(
                update={"quantity": quantity}, deep=True
            )
            return True

        return await self._edit(household_id, provider, change)

    async def add_lines(
        self, household_id: str, provider: ProviderName, lines: list[BasketLine]
    ) -> Basket:
        """Add several lines in one revision-checked edit (saved lists, reorder).

        A line whose variant is already in the basket adds to its quantity, as
        ``add_line`` does. Either every line is added or none is.
        """

        for line in lines:
            _validate_quantity(line.quantity)

        def change(basket: Basket) -> bool:
            if not lines:
                return False
            for line in lines:
                if line.ref.provider != basket.provider:
                    raise InvalidBasketChange("This item belongs to a different store.")
                existing = next(
                    (item for item in basket.lines if same_variant(item.ref, line.ref)), None
                )
                if existing is None:
                    basket.lines.append(line.model_copy(deep=True))
                    continue
                quantity = existing.quantity + line.quantity
                _validate_quantity(quantity)
                basket.lines[basket.lines.index(existing)] = line.model_copy(
                    update={"quantity": quantity}, deep=True
                )
            return True

        return await self._edit(household_id, provider, change)

    async def set_quantity(
        self, household_id: str, provider: ProviderName, variant_id: str, quantity: int
    ) -> Basket:
        if quantity != 0:
            _validate_quantity(quantity)

        def change(basket: Basket) -> bool:
            index = self._index(basket, variant_id)
            if quantity == 0:
                del basket.lines[index]
                return True
            if basket.lines[index].quantity == quantity:
                return False
            basket.lines[index].quantity = quantity
            return True

        return await self._edit(household_id, provider, change)

    async def remove_line(
        self, household_id: str, provider: ProviderName, variant_id: str
    ) -> Basket:
        def change(basket: Basket) -> bool:
            del basket.lines[self._index(basket, variant_id)]
            return True

        return await self._edit(household_id, provider, change)

    async def clear(
        self,
        household_id: str,
        provider: ProviderName,
        *,
        expected_revision: int | None = None,
    ) -> Basket:
        """Empty the open basket in one revision-checked edit (supersedes its bill).

        Refused like every edit while checkout is pending or its outcome is unknown.
        With ``expected_revision`` (the on-screen "Clear basket") only the basket
        the user saw is cleared: if anyone changed it since, ``BasketConflict`` is
        raised and nothing is removed.
        """

        def change(basket: Basket) -> bool:
            if expected_revision is not None and basket.revision != expected_revision:
                raise BasketConflict("This basket changed. Review it and try again.")
            if not basket.lines:
                return False
            basket.lines.clear()
            return True

        return await self._edit(household_id, provider, change)

    async def set_address(
        self, household_id: str, provider: ProviderName, address_id: str
    ) -> Basket:
        def change(basket: Basket) -> bool:
            if basket.address_id == address_id:
                return False
            basket.address_id = address_id
            return True

        return await self._edit(household_id, provider, change)

    async def clear_removed_address(
        self, household_id: str, provider: ProviderName, address_id: str
    ) -> bool:
        """The saved address ``address_id`` no longer exists at the store.

        Lines are kept; the basket's address is cleared, which supersedes any
        open bill and therefore any order card bound to it. Returns whether
        the basket changed. A basket already in checkout is left untouched.
        """

        basket = await self.baskets.active_for(household_id, provider)
        if basket is None or basket.address_id != address_id:
            return False
        if basket.status in {BasketStatus.CHECKOUT_PENDING, BasketStatus.ORDERED}:
            return False

        def change(current: Basket) -> bool:
            if current.address_id != address_id:
                return False
            current.address_id = None
            return True

        await self._edit(household_id, provider, change)
        return True

    @staticmethod
    def _index(basket: Basket, variant_id: str) -> int:
        for index, line in enumerate(basket.lines):
            if line.ref.variant_id == variant_id:
                return index
        raise NotFound("That item is not in the basket.")

    async def _edit(
        self,
        household_id: str,
        provider: ProviderName,
        change: Callable[[Basket], bool],
    ) -> Basket:
        for attempt in range(EDIT_ATTEMPTS):
            basket = await self.active(household_id, provider)
            self._require_editable(basket)
            if not change(basket):
                return basket
            try:
                return await self._commit(basket)
            except BasketConflict:
                if attempt == EDIT_ATTEMPTS - 1:
                    raise
        raise AssertionError("unreachable")

    async def _commit(self, basket: Basket) -> Basket:
        now = self.clock()
        expected_revision = basket.revision
        basket.revision += 1
        basket.updated_at = now
        basket.status = BasketStatus.DRAFT
        await self.baskets.save_if_editable(
            basket, expected_revision=expected_revision
        )
        # The database repository already superseded these in the edit's own
        # transaction; this keeps other repositories correct too.
        await self.approvals.supersede_open(basket.id, now)
        return basket

    @staticmethod
    def _require_editable(basket: Basket) -> None:
        if basket.status in {BasketStatus.CHECKOUT_PENDING, BasketStatus.ORDERED}:
            raise InvalidBasketChange(
                "Checkout already started for this basket. Do not create or submit it again."
            )
