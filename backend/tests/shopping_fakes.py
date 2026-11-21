from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService, item_total_paise
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.models import Basket, BasketLine, Charge, ProductRef, ProviderName, Quote


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
