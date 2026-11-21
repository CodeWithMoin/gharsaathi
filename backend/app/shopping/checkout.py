"""One-shot, approval-bound Zepto COD checkout orchestration.

The provider order call is deliberately surrounded by a durable claim.  Once a
claim exists, no code path in this service can call the provider a second time,
including after a timeout or malformed response.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    ShoppingApprovalRecord,
    ShoppingBasketRecord,
    ZeptoCheckoutAttemptRecord,
)
from app.shopping.approval import check_quote
from app.shopping.basket import Clock, utc_now
from app.shopping.errors import ApprovalRefused, NotFound, ProviderUnavailable, ShoppingError
from app.shopping.models import (
    Approval,
    ApprovalStatus,
    Basket,
    BasketStatus,
    CheckoutAttempt,
    CheckoutStatus,
    Quote,
    basket_fingerprint,
)
from app.shopping.ports import ApprovalRepository, BasketRepository, QuoteProvider


@runtime_checkable
class ZeptoCodGateway(Protocol):
    """The narrow consequential capability supplied by the Zepto adapter."""

    async def confirm_cod(self, basket: Basket) -> object: ...


class CheckoutAttemptRepository(Protocol):
    async def for_approval(self, approval_id: str) -> CheckoutAttempt | None: ...

    async def claim(
        self, attempt: CheckoutAttempt, *, basket_revision: int
    ) -> tuple[CheckoutAttempt, bool]: ...

    async def mark_unknown(
        self, attempt_id: str, completed_at: datetime
    ) -> CheckoutAttempt: ...

    async def confirm(
        self,
        attempt_id: str,
        *,
        provider_order_id: str,
        provider_status: str,
        completed_at: datetime,
    ) -> CheckoutAttempt: ...


def idempotency_key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DatabaseCheckoutAttemptRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def for_approval(self, approval_id: str) -> CheckoutAttempt | None:
        async with self.sessions() as session:
            record = await session.scalar(
                select(ZeptoCheckoutAttemptRecord).where(
                    ZeptoCheckoutAttemptRecord.approval_id == approval_id
                )
            )
            return _attempt(record) if record else None

    async def claim(
        self, attempt: CheckoutAttempt, *, basket_revision: int
    ) -> tuple[CheckoutAttempt, bool]:
        """Atomically reserve the approval and freeze its exact basket.

        The unique constraints make both the approval and idempotency key
        one-shot across processes.  The status changes keep normal basket APIs
        from mutating the basket while the provider result is in flight.
        """

        try:
            async with self.sessions() as session, session.begin():
                record = ZeptoCheckoutAttemptRecord(**attempt.model_dump(mode="python"))
                session.add(record)
                await session.flush()

                approval = await session.scalar(
                    select(ShoppingApprovalRecord)
                    .where(ShoppingApprovalRecord.id == attempt.approval_id)
                    .with_for_update()
                )
                basket = await session.scalar(
                    select(ShoppingBasketRecord)
                    .where(ShoppingBasketRecord.id == attempt.basket_id)
                    .with_for_update()
                )
                if approval is None or basket is None:
                    raise NotFound("Approval not found.")
                if (
                    approval.status != ApprovalStatus.APPROVED.value
                    or basket.status != BasketStatus.APPROVED.value
                    or basket.revision != basket_revision
                    or basket.household_id != attempt.household_id
                    or basket.provider != "zepto"
                ):
                    raise ApprovalRefused(
                        "The approved basket changed before checkout. Please review it again."
                    )
                approval.status = ApprovalStatus.CONSUMING.value
                basket.status = BasketStatus.CHECKOUT_PENDING.value
            return attempt, True
        except IntegrityError:
            # A concurrent request won. Read the durable winner in a clean
            # transaction; never dispatch from the losing request.
            async with self.sessions() as session:
                record = await session.scalar(
                    select(ZeptoCheckoutAttemptRecord).where(
                        (ZeptoCheckoutAttemptRecord.approval_id == attempt.approval_id)
                        | (
                            (ZeptoCheckoutAttemptRecord.household_id == attempt.household_id)
                            & (
                                ZeptoCheckoutAttemptRecord.idempotency_key_hash
                                == attempt.idempotency_key_hash
                            )
                        )
                    )
                )
                if record is None:
                    raise
                return _attempt(record), False

    async def mark_unknown(
        self, attempt_id: str, completed_at: datetime
    ) -> CheckoutAttempt:
        async with self.sessions() as session, session.begin():
            record = await session.get(
                ZeptoCheckoutAttemptRecord, attempt_id, with_for_update=True
            )
            if record is None:
                raise NotFound("Checkout attempt not found.")
            if record.status == CheckoutStatus.DISPATCHING.value:
                record.status = CheckoutStatus.OUTCOME_UNKNOWN.value
                record.completed_at = completed_at
            await session.flush()
            return _attempt(record)

    async def confirm(
        self,
        attempt_id: str,
        *,
        provider_order_id: str,
        provider_status: str,
        completed_at: datetime,
    ) -> CheckoutAttempt:
        """Persist provider success and consume local approval atomically."""

        async with self.sessions() as session, session.begin():
            record = await session.get(
                ZeptoCheckoutAttemptRecord, attempt_id, with_for_update=True
            )
            if record is None:
                raise NotFound("Checkout attempt not found.")
            if record.status != CheckoutStatus.DISPATCHING.value:
                return _attempt(record)
            approval = await session.get(
                ShoppingApprovalRecord, record.approval_id, with_for_update=True
            )
            basket = await session.get(
                ShoppingBasketRecord, record.basket_id, with_for_update=True
            )
            if approval is None or basket is None:
                raise RuntimeError("Claimed checkout lost its approval or basket.")
            record.status = CheckoutStatus.CONFIRMED.value
            record.provider_order_id = provider_order_id
            record.provider_status = provider_status
            record.completed_at = completed_at
            approval.status = ApprovalStatus.CONSUMED.value
            approval.decided_at = completed_at
            basket.status = BasketStatus.ORDERED.value
            basket.updated_at = completed_at
            await session.flush()
            return _attempt(record)


class CheckoutService:
    def __init__(
        self,
        baskets: BasketRepository,
        approvals: ApprovalRepository,
        attempts: CheckoutAttemptRepository,
        quote_provider: QuoteProvider,
        gateway: ZeptoCodGateway,
        clock: Clock = utc_now,
        checkout_lock: asyncio.Lock | None = None,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.attempts = attempts
        self.quote_provider = quote_provider
        self.gateway = gateway
        self.clock = clock
        self.checkout_lock = checkout_lock

    async def checkout_cod(
        self,
        approval_id: str,
        household_id: str,
        idempotency_key: str,
        *,
        expected_fingerprint: str | None = None,
    ) -> CheckoutAttempt:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise ApprovalRefused("Provide a valid Idempotency-Key header.")
        key_hash = idempotency_key_hash(key)

        if self.checkout_lock is not None:
            async with self.checkout_lock:
                return await self._checkout_cod_serialized(
                    approval_id,
                    household_id,
                    key_hash,
                    expected_fingerprint,
                    coordinated=True,
                )
        return await self._checkout_cod_serialized(
            approval_id,
            household_id,
            key_hash,
            expected_fingerprint,
            coordinated=False,
        )

    async def _checkout_cod_serialized(
        self,
        approval_id: str,
        household_id: str,
        key_hash: str,
        expected_fingerprint: str | None,
        *,
        coordinated: bool,
    ) -> CheckoutAttempt:

        approval, basket = await self._owned(approval_id, household_id)
        if expected_fingerprint is not None and approval.fingerprint != expected_fingerprint:
            raise ApprovalRefused(
                "The displayed bill does not match this approval. Please review it again."
            )
        existing = await self.attempts.for_approval(approval_id)
        if existing is not None:
            if existing.idempotency_key_hash != key_hash:
                raise ApprovalRefused(
                    "This approval already has a checkout attempt with another idempotency key."
                )
            return existing

        self._validate_approved(approval, basket)
        quote = await self._fresh_quote(basket, coordinated=coordinated)
        if quote.payable_paise != approval.payable_paise:
            raise ApprovalRefused(
                "The payable amount changed. Please review and approve the new bill."
            )

        # Pricing is a network boundary. Re-read and revalidate all local state
        # before acquiring the one-shot dispatch claim.
        approval, basket = await self._owned(approval_id, household_id)
        if expected_fingerprint is not None and approval.fingerprint != expected_fingerprint:
            raise ApprovalRefused(
                "The displayed bill does not match this approval. Please review it again."
            )
        self._validate_approved(approval, basket)
        check_quote(basket, quote)
        if quote.payable_paise != approval.payable_paise:
            raise ApprovalRefused(
                "The payable amount changed. Please review and approve the new bill."
            )

        attempt = CheckoutAttempt(
            id=uuid.uuid4().hex,
            approval_id=approval.id,
            basket_id=basket.id,
            household_id=household_id,
            idempotency_key_hash=key_hash,
            payable_paise=approval.payable_paise,
            status=CheckoutStatus.DISPATCHING,
            created_at=self.clock(),
        )
        claimed, created = await self.attempts.claim(
            attempt, basket_revision=basket.revision
        )
        if not created:
            if claimed.approval_id != approval.id:
                raise ApprovalRefused(
                    "This idempotency key was already used for another checkout."
                )
            if claimed.idempotency_key_hash != key_hash:
                raise ApprovalRefused(
                    "This approval already has a checkout attempt with another idempotency key."
                )
            return claimed

        try:
            if coordinated:
                confirm_unlocked = getattr(self.gateway, "confirm_cod_unlocked", None)
                if not callable(confirm_unlocked):
                    raise RuntimeError("The Zepto gateway cannot participate in coordinated checkout.")
                result = await confirm_unlocked(basket)
            else:
                result = await self.gateway.confirm_cod(basket)
            order_id, provider_status = self._confirmed_result(result, attempt)
            return await self.attempts.confirm(
                attempt.id,
                provider_order_id=order_id,
                provider_status=provider_status,
                completed_at=self.clock(),
            )
        except asyncio.CancelledError:
            await self.attempts.mark_unknown(attempt.id, self.clock())
            raise
        except Exception:
            # The provider may have accepted the order. Persist uncertainty and
            # never retry the consequential call for this approval.
            return await self.attempts.mark_unknown(attempt.id, self.clock())

    async def _fresh_quote(self, basket: Basket, *, coordinated: bool = False) -> Quote:
        try:
            if coordinated:
                quote_unlocked = getattr(self.quote_provider, "quote_unlocked", None)
                if not callable(quote_unlocked):
                    raise RuntimeError("The Zepto quote provider cannot participate in coordinated checkout.")
                quote = await quote_unlocked(basket)
            else:
                quote = await self.quote_provider.quote(basket)
        except ShoppingError:
            raise
        except Exception as exc:
            raise ProviderUnavailable(
                "Zepto could not refresh this bill. No order was placed."
            ) from exc
        check_quote(basket, quote)
        return quote

    async def _owned(
        self, approval_id: str, household_id: str
    ) -> tuple[Approval, Basket]:
        approval = await self.approvals.get(approval_id)
        if approval is None:
            raise NotFound("Approval not found.")
        basket = await self.baskets.get(approval.basket_id)
        if basket is None or basket.household_id != household_id:
            # Do not disclose another household's approval identifier.
            raise NotFound("Approval not found.")
        return approval, basket

    def _validate_approved(self, approval: Approval, basket: Basket) -> None:
        if approval.status != ApprovalStatus.APPROVED:
            raise ApprovalRefused(
                f"This approval is {approval.status.value}; review the basket again."
            )
        if _utc(self.clock()) >= _utc(approval.expires_at):
            raise ApprovalRefused("This approval expired. Please review the basket again.")
        if basket.provider != "zepto":
            raise ApprovalRefused("This checkout endpoint only accepts Zepto baskets.")
        if basket.status != BasketStatus.APPROVED:
            raise ApprovalRefused("The basket is no longer approved.")
        if not basket.lines or basket.address_id is None:
            raise ApprovalRefused("The approved basket is incomplete.")
        if (
            basket.revision != approval.basket_revision
            or basket_fingerprint(basket, approval.payable_paise) != approval.fingerprint
        ):
            raise ApprovalRefused(
                "The basket changed after approval. Please review it again."
            )

    @staticmethod
    def _confirmed_result(
        result: object, attempt: CheckoutAttempt
    ) -> tuple[str, str]:
        order_id = getattr(result, "order_id", None)
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("Zepto confirmation did not contain an order id.")
        payable = getattr(result, "payable_paise", None)
        if not isinstance(payable, int) or payable != attempt.payable_paise:
            raise ValueError("Zepto confirmed an unexpected payable amount.")
        status = getattr(result, "status", "confirmed")
        if not isinstance(status, str) or not status.strip():
            status = "confirmed"
        return order_id.strip(), status.strip()[:80]


def _attempt(record: ZeptoCheckoutAttemptRecord) -> CheckoutAttempt:
    return CheckoutAttempt.model_validate(
        {
            "id": record.id,
            "approval_id": record.approval_id,
            "basket_id": record.basket_id,
            "household_id": record.household_id,
            "idempotency_key_hash": record.idempotency_key_hash,
            "payable_paise": record.payable_paise,
            "status": record.status,
            "provider_order_id": record.provider_order_id,
            "provider_status": record.provider_status,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
        }
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
