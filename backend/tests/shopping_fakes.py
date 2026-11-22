from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService, item_total_paise
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.errors import ApprovalRefused
from app.shopping.models import (
    ApprovalStatus,
    Basket,
    BasketLine,
    BasketStatus,
    Charge,
    CheckoutAttempt,
    CheckoutStatus,
    ProductRef,
    ProviderName,
    Quote,
)


HOUSEHOLD = "h1"
PROVIDER: ProviderName = "swiggy_instamart"
DELIVERY_FEE = 3000


class FakeProvider:
    name: ProviderName = PROVIDER

    def __init__(self) -> None:
        self.unavailable: list[ProductRef] = []
        self.extra_paise = 0
        self.quotes = 0

    async def quote(self, basket: Basket) -> Quote:
        self.quotes += 1
        items = item_total_paise(basket)
        return Quote(
            provider=self.name,
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=items,
            charges=[Charge(label="Delivery fee", amount_paise=DELIVERY_FEE)],
            payable_paise=items + DELIVERY_FEE + self.extra_paise,
            unavailable_refs=self.unavailable,
            eta_text="12 mins",
            quoted_at=datetime.now(UTC),
        )


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.provider = FakeProvider()
        self.basket_repo = InMemoryBasketRepository()
        self.approval_repo = InMemoryApprovalRepository()
        self.baskets = BasketService(self.basket_repo, self.approval_repo, self.clock)
        self.approvals = ApprovalService(
            self.basket_repo,
            self.approval_repo,
            lambda _: self.provider,
            self.clock,
        )

    async def ready_basket(self) -> Basket:
        await self.baskets.add_line(HOUSEHOLD, PROVIDER, line())
        return await self.baskets.set_address(HOUSEHOLD, PROVIDER, "addr_home")


def line(
    variant: str = "spin1", price: int = 15400, quantity: int = 1, **overrides: object
) -> BasketLine:
    values: dict[str, object] = {
        "ref": ProductRef(
            provider=PROVIDER,
            product_id=f"p_{variant}",
            variant_id=variant,
            sku_id=f"sku_{variant}",
        ),
        "name": f"Item {variant}",
        "unit_price_paise": price,
        "mrp_paise": 18000,
        "quantity": quantity,
    }
    values.update(overrides)
    return BasketLine.model_validate(values)


class InMemoryCheckoutAttempts:
    """CheckoutAttemptRepository over the in-memory baskets/approvals: one claim per approval."""

    def __init__(self, baskets: InMemoryBasketRepository, approvals: InMemoryApprovalRepository) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.by_approval: dict[str, CheckoutAttempt] = {}

    async def for_approval(self, approval_id: str) -> CheckoutAttempt | None:
        value = self.by_approval.get(approval_id)
        return value.model_copy(deep=True) if value else None

    async def claim(self, attempt: CheckoutAttempt, *, basket_revision: int) -> tuple[CheckoutAttempt, bool]:
        existing = self.by_approval.get(attempt.approval_id)
        if existing is not None:
            return existing.model_copy(deep=True), False
        approval = await self.approvals.get(attempt.approval_id)
        basket = await self.baskets.get(attempt.basket_id)
        if (
            approval is None
            or basket is None
            or approval.status != ApprovalStatus.APPROVED
            or basket.status != BasketStatus.APPROVED
            or basket.revision != basket_revision
        ):
            raise ApprovalRefused("The approved basket changed before checkout.")
        approval.status = ApprovalStatus.CONSUMING
        basket.status = BasketStatus.CHECKOUT_PENDING
        await self.approvals.save(approval)
        await self.baskets.save(basket)
        self.by_approval[attempt.approval_id] = attempt.model_copy(deep=True)
        return attempt.model_copy(deep=True), True

    async def mark_unknown(self, attempt_id: str, completed_at: datetime) -> CheckoutAttempt:
        attempt = next(item for item in self.by_approval.values() if item.id == attempt_id)
        attempt.status = CheckoutStatus.OUTCOME_UNKNOWN
        attempt.completed_at = completed_at
        return attempt.model_copy(deep=True)

    async def confirm(
        self, attempt_id: str, *, provider_order_id: str, provider_status: str, completed_at: datetime
    ) -> CheckoutAttempt:
        attempt = next(item for item in self.by_approval.values() if item.id == attempt_id)
        attempt.status = CheckoutStatus.CONFIRMED
        attempt.provider_order_id = provider_order_id
        attempt.provider_status = provider_status
        attempt.completed_at = completed_at
        approval = await self.approvals.get(attempt.approval_id)
        basket = await self.baskets.get(attempt.basket_id)
        assert approval is not None and basket is not None
        approval.status = ApprovalStatus.CONSUMED
        basket.status = BasketStatus.ORDERED
        await self.approvals.save(approval)
        await self.baskets.save(basket)
        return attempt.model_copy(deep=True)
