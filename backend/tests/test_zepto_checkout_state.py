import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.dependencies import get_zepto_checkout_service
from app.main import app
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService, item_total_paise
from app.shopping.checkout import (
    CheckoutService,
    DatabaseCheckoutAttemptRepository,
    idempotency_key_hash,
)
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.errors import ApprovalRefused, NotFound
from app.shopping.memory import InMemoryApprovalRepository, InMemoryBasketRepository
from app.shopping.models import (
    Approval,
    ApprovalStatus,
    Basket,
    BasketLine,
    BasketStatus,
    Charge,
    CheckoutAttempt,
    CheckoutStatus,
    ProductRef,
    Quote,
    basket_fingerprint,
)
from tests.shopping_fakes import Clock


USER = "zepto-user"
OTHER_USER = "other-user"


class ZeptoQuoteProvider:
    name = "zepto"

    def __init__(self) -> None:
        self.extra_paise = 2500
        self.calls = 0

    async def quote(self, basket: Basket) -> Quote:
        self.calls += 1
        item_total = item_total_paise(basket)
        return Quote(
            provider="zepto",
            basket_id=basket.id,
            basket_revision=basket.revision,
            item_total_paise=item_total,
            charges=[Charge(label="Delivery fee", amount_paise=self.extra_paise)],
            payable_paise=item_total + self.extra_paise,
            quoted_at=datetime.now(UTC),
        )


@dataclass
class Confirmed:
    order_id: str
    payable_paise: int


class Gateway:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None
        self.malformed = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def confirm_cod(self, basket: Basket) -> object:
        self.calls += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.error:
            raise self.error
        if self.malformed:
            return object()
        return Confirmed(order_id="zepto-order-1", payable_paise=5800)


class MemoryAttemptRepository:
    def __init__(
        self,
        baskets: InMemoryBasketRepository,
        approvals: InMemoryApprovalRepository,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.by_approval: dict[str, CheckoutAttempt] = {}
        self.by_key: dict[tuple[str, str], CheckoutAttempt] = {}
        self.lock = asyncio.Lock()

    async def for_approval(self, approval_id: str) -> CheckoutAttempt | None:
        value = self.by_approval.get(approval_id)
        return value.model_copy(deep=True) if value else None

    async def claim(
        self, attempt: CheckoutAttempt, *, basket_revision: int
    ) -> tuple[CheckoutAttempt, bool]:
        async with self.lock:
            existing = self.by_approval.get(attempt.approval_id) or self.by_key.get(
                (attempt.household_id, attempt.idempotency_key_hash)
            )
            if existing:
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
            self.by_key[(attempt.household_id, attempt.idempotency_key_hash)] = (
                attempt.model_copy(deep=True)
            )
            return attempt.model_copy(deep=True), True

    async def mark_unknown(
        self, attempt_id: str, completed_at: datetime
    ) -> CheckoutAttempt:
        async with self.lock:
            attempt = next(
                item for item in self.by_approval.values() if item.id == attempt_id
            )
            attempt.status = CheckoutStatus.OUTCOME_UNKNOWN
            attempt.completed_at = completed_at
            return attempt.model_copy(deep=True)

    async def confirm(
        self,
        attempt_id: str,
        *,
        provider_order_id: str,
        provider_status: str,
        completed_at: datetime,
    ) -> CheckoutAttempt:
        async with self.lock:
            attempt = next(
                item for item in self.by_approval.values() if item.id == attempt_id
            )
            attempt.status = CheckoutStatus.CONFIRMED
            attempt.provider_order_id = provider_order_id
            attempt.provider_status = provider_status
            attempt.completed_at = completed_at
            approval = await self.approvals.get(attempt.approval_id)
            basket = await self.baskets.get(attempt.basket_id)
            assert approval and basket
            approval.status = ApprovalStatus.CONSUMED
            approval.decided_at = completed_at
            basket.status = BasketStatus.ORDERED
            basket.updated_at = completed_at
            await self.approvals.save(approval)
            await self.baskets.save(basket)
            return attempt.model_copy(deep=True)


class World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.basket_repo = InMemoryBasketRepository()
        self.approval_repo = InMemoryApprovalRepository()
        self.quotes = ZeptoQuoteProvider()
        self.gateway = Gateway()
        self.baskets = BasketService(
            self.basket_repo, self.approval_repo, self.clock
        )
        self.approvals = ApprovalService(
            self.basket_repo,
            self.approval_repo,
            lambda _: self.quotes,
            self.clock,
        )
        self.attempts = MemoryAttemptRepository(
            self.basket_repo, self.approval_repo
        )
        self.checkout = CheckoutService(
            self.basket_repo,
            self.approval_repo,
            self.attempts,
            self.quotes,
            self.gateway,
            self.clock,
        )

    async def approved(self):
        line = BasketLine(
            ref=ProductRef(
                provider="zepto",
                product_id="milk",
                variant_id="milk-500",
                sku_id="milk-sku",
            ),
            name="Milk 500 ml",
            unit_price_paise=3300,
            quantity=1,
        )
        await self.baskets.add_line(USER, "zepto", line)
        basket = await self.baskets.set_address(USER, "zepto", "addr-home")
        requested = await self.approvals.request(basket.id)
        approval = await self.approvals.approve(
            requested.approval.id, requested.approval.fingerprint, USER
        )
        return approval, basket


async def test_confirmed_checkout_consumes_approval_and_orders_basket() -> None:
    world = World()
    approval, basket = await world.approved()

    attempt = await world.checkout.checkout_cod(approval.id, USER, "request-1")

    assert attempt.status == CheckoutStatus.CONFIRMED
    assert attempt.provider_order_id == "zepto-order-1"
    assert world.gateway.calls == 1
    stored_approval = await world.approval_repo.get(approval.id)
    stored_basket = await world.basket_repo.get(basket.id)
    assert stored_approval and stored_approval.status == ApprovalStatus.CONSUMED
    assert stored_basket and stored_basket.status == BasketStatus.ORDERED


async def test_concurrent_and_repeated_requests_dispatch_only_once() -> None:
    world = World()
    approval, _ = await world.approved()
    world.gateway.block = True

    first_task = asyncio.create_task(
        world.checkout.checkout_cod(approval.id, USER, "same-request")
    )
    await world.gateway.entered.wait()
    concurrent = await world.checkout.checkout_cod(
        approval.id, USER, "same-request"
    )
    assert concurrent.status == CheckoutStatus.DISPATCHING
    assert world.gateway.calls == 1

    world.gateway.release.set()
    first = await first_task
    repeated = await world.checkout.checkout_cod(
        approval.id, USER, "same-request"
    )
    assert first.status == repeated.status == CheckoutStatus.CONFIRMED
    assert repeated.id == first.id
    assert world.gateway.calls == 1


async def test_exception_after_dispatch_is_unknown_and_never_retried() -> None:
    world = World()
    approval, basket = await world.approved()
    world.gateway.error = TimeoutError("provider timed out")

    first = await world.checkout.checkout_cod(approval.id, USER, "request-timeout")
    again = await world.checkout.checkout_cod(approval.id, USER, "request-timeout")

    assert first.status == again.status == CheckoutStatus.OUTCOME_UNKNOWN
    assert world.gateway.calls == 1
    stored_approval = await world.approval_repo.get(approval.id)
    stored_basket = await world.basket_repo.get(basket.id)
    assert stored_approval and stored_approval.status == ApprovalStatus.CONSUMING
    assert stored_basket and stored_basket.status == BasketStatus.CHECKOUT_PENDING


async def test_malformed_confirmation_is_unknown() -> None:
    world = World()
    approval, _ = await world.approved()
    world.gateway.malformed = True
    result = await world.checkout.checkout_cod(approval.id, USER, "malformed")
    assert result.status == CheckoutStatus.OUTCOME_UNKNOWN
    assert world.gateway.calls == 1


async def test_coordinated_checkout_holds_one_lock_across_preview_and_confirm() -> None:
    world = World()
    approval, _ = await world.approved()
    coordinator = asyncio.Lock()
    trace: list[str] = []

    class CoordinatedQuote(ZeptoQuoteProvider):
        async def quote_unlocked(self, basket: Basket) -> Quote:
            assert coordinator.locked()
            trace.append("preview")
            return await super().quote(basket)

    class CoordinatedGateway(Gateway):
        async def confirm_cod_unlocked(self, basket: Basket) -> object:
            assert coordinator.locked()
            trace.append("confirm")
            return await super().confirm_cod(basket)

    quotes = CoordinatedQuote()
    gateway = CoordinatedGateway()
    checkout = CheckoutService(
        world.basket_repo,
        world.approval_repo,
        world.attempts,
        quotes,
        gateway,
        world.clock,
        checkout_lock=coordinator,
    )

    result = await checkout.checkout_cod(approval.id, USER, "coordinated")

    assert result.status == CheckoutStatus.CONFIRMED
    assert trace == ["preview", "confirm"]


async def test_owner_expiry_staleness_and_fresh_price_are_checked_before_dispatch() -> None:
    world = World()
    approval, _ = await world.approved()
    with pytest.raises(NotFound):
        await world.checkout.checkout_cod(approval.id, OTHER_USER, "wrong-owner")
    assert world.gateway.calls == 0

    world.clock.advance(minutes=11)
    with pytest.raises(ApprovalRefused, match="expired"):
        await world.checkout.checkout_cod(approval.id, USER, "expired")
    assert world.gateway.calls == 0

    stale = World()
    stale_approval, _ = await stale.approved()
    await stale.baskets.set_quantity(USER, "zepto", "milk-500", 2)
    with pytest.raises(ApprovalRefused, match="superseded"):
        await stale.checkout.checkout_cod(stale_approval.id, USER, "stale")
    assert stale.gateway.calls == 0

    repriced = World()
    repriced_approval, _ = await repriced.approved()
    repriced.quotes.extra_paise += 100
    with pytest.raises(ApprovalRefused, match="payable amount changed"):
        await repriced.checkout.checkout_cod(
            repriced_approval.id, USER, "repriced"
        )
    assert repriced.gateway.calls == 0


async def test_different_idempotency_key_cannot_reopen_an_attempt() -> None:
    world = World()
    approval, _ = await world.approved()
    await world.checkout.checkout_cod(approval.id, USER, "first-key")
    with pytest.raises(ApprovalRefused, match="another idempotency key"):
        await world.checkout.checkout_cod(approval.id, USER, "second-key")
    assert world.gateway.calls == 1
    assert idempotency_key_hash("first-key") != idempotency_key_hash("second-key")


async def test_displayed_fingerprint_must_match_before_checkout() -> None:
    world = World()
    approval, _ = await world.approved()

    with pytest.raises(ApprovalRefused, match="displayed bill does not match"):
        await world.checkout.checkout_cod(
            approval.id,
            USER,
            "wrong-fingerprint",
            expected_fingerprint="0" * 64,
        )

    assert world.gateway.calls == 0


async def test_checkout_api_requires_idempotency_header() -> None:
    class Stub:
        async def checkout_cod(self, approval_id, household_id, key, **kwargs):
            raise AssertionError("must not run without header")

    app.dependency_overrides[get_zepto_checkout_service] = Stub
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/zepto/checkout/cod",
                json={"approval_id": "approval-1", "fingerprint": "a" * 64},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "Idempotency-Key header is required."
    finally:
        app.dependency_overrides.pop(get_zepto_checkout_service, None)


async def test_checkout_api_passes_authenticated_owner_and_returns_confirmation() -> None:
    class Stub:
        seen: tuple[str, str, str, str] | None = None

        async def checkout_cod(
            self, approval_id, household_id, key, *, expected_fingerprint=None
        ):
            self.seen = (approval_id, household_id, key, expected_fingerprint)
            return CheckoutAttempt(
                id="api-attempt",
                approval_id=approval_id,
                basket_id="api-basket",
                household_id=household_id,
                idempotency_key_hash=idempotency_key_hash(key),
                payable_paise=5800,
                status=CheckoutStatus.CONFIRMED,
                provider_order_id="api-order",
                provider_status="confirmed",
                created_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )

    stub = Stub()
    app.dependency_overrides[get_zepto_checkout_service] = lambda: stub
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/zepto/checkout/cod",
                headers={
                    "X-Test-User-Id": USER,
                    "Idempotency-Key": "api-key",
                },
                json={"approval_id": "api-approval", "fingerprint": "b" * 64},
            )
        assert response.status_code == 200
        assert response.json()["provider_order_id"] == "api-order"
        assert response.json()["payable"] == {"paise": 5800, "display": "₹58"}
        assert stub.seen == ("api-approval", USER, "api-key", "b" * 64)
    finally:
        app.dependency_overrides.pop(get_zepto_checkout_service, None)


async def test_database_claim_is_unique_and_terminal_success_is_atomic(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'checkout.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    baskets = DatabaseBasketRepository(sessions)
    approvals = DatabaseApprovalRepository(sessions)
    attempts = DatabaseCheckoutAttemptRepository(sessions)
    now = datetime.now(UTC)
    basket = Basket(
        id="db-basket",
        household_id=USER,
        provider="zepto",
        address_id="addr-home",
        lines=[
            BasketLine(
                ref=ProductRef(
                    provider="zepto",
                    product_id="milk",
                    variant_id="milk-500",
                    sku_id="store-milk",
                ),
                name="Milk",
                unit_price_paise=3300,
                quantity=1,
            )
        ],
        status=BasketStatus.APPROVED,
        revision=2,
        created_at=now,
        updated_at=now,
    )
    await baskets.save(basket)
    approval = Approval(
        id="db-approval",
        basket_id=basket.id,
        basket_revision=basket.revision,
        fingerprint=basket_fingerprint(basket, 5800),
        payable_paise=5800,
        status=ApprovalStatus.APPROVED,
        created_at=now,
        expires_at=now.replace(year=now.year + 1),
        decided_at=now,
    )
    await approvals.save(approval)
    claim = CheckoutAttempt(
        id="db-attempt",
        approval_id=approval.id,
        basket_id=basket.id,
        household_id=USER,
        idempotency_key_hash=idempotency_key_hash("db-key"),
        payable_paise=5800,
        status=CheckoutStatus.DISPATCHING,
        created_at=now,
    )

    first, created = await attempts.claim(claim, basket_revision=basket.revision)
    duplicate, duplicate_created = await attempts.claim(
        claim.model_copy(update={"id": "other-id"}),
        basket_revision=basket.revision,
    )
    assert created is True and duplicate_created is False
    assert duplicate.id == first.id
    assert (await approvals.get(approval.id)).status == ApprovalStatus.CONSUMING
    assert (await baskets.get(basket.id)).status == BasketStatus.CHECKOUT_PENDING

    confirmed = await attempts.confirm(
        claim.id,
        provider_order_id="provider-order",
        provider_status="confirmed",
        completed_at=now,
    )
    assert confirmed.status == CheckoutStatus.CONFIRMED
    assert (await approvals.get(approval.id)).status == ApprovalStatus.CONSUMED
    assert (await baskets.get(basket.id)).status == BasketStatus.ORDERED
    await engine.dispose()
