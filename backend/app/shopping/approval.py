import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from app.shopping.basket import OPEN_APPROVAL_STATUSES, Clock, same_variant, utc_now
from app.shopping.errors import ApprovalRefused, NotFound, ProviderUnavailable, ShoppingError
from app.shopping.models import (
    Approval,
    ApprovalStatus,
    Basket,
    BasketStatus,
    ProviderName,
    Quote,
    basket_fingerprint,
)
from app.shopping.ports import ApprovalRepository, BasketRepository, QuoteProvider


APPROVAL_TTL = timedelta(minutes=10)
ProviderLookup = Callable[[ProviderName], QuoteProvider]


@dataclass(frozen=True)
class ApprovalRequest:
    approval: Approval
    quote: Quote
    basket: Basket


def check_quote(basket: Basket, quote: Quote) -> None:
    """Refuse quotes that are not for the exact current basket revision."""

    if (
        quote.basket_id != basket.id
        or quote.provider != basket.provider
        or quote.basket_revision != basket.revision
    ):
        raise ApprovalRefused(
            "The basket changed while the store was pricing it. Please review it again."
        )
    if quote.unavailable_refs:
        names = [
            line.name
            for line in basket.lines
            if any(same_variant(line.ref, ref) for ref in quote.unavailable_refs)
        ]
        raise ApprovalRefused(
            f"Not available right now: {', '.join(names) if names else 'Some items'}. "
            "Remove or replace them, then ask for approval again."
        )


class ApprovalService:
    def __init__(
        self,
        baskets: BasketRepository,
        approvals: ApprovalRepository,
        providers: ProviderLookup,
        clock: Clock = utc_now,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.providers = providers
        self.clock = clock

    async def request(self, basket_id: str) -> ApprovalRequest:
        basket = await self._basket(basket_id)
        if basket.status == BasketStatus.ABANDONED:
            raise ApprovalRefused("This basket can no longer be approved.")
        if not basket.lines:
            raise ApprovalRefused("The basket is empty.")
        if basket.address_id is None:
            raise ApprovalRefused("Choose a delivery address first.")

        try:
            quote = await self.providers(basket.provider).quote(basket)
        except ShoppingError:
            raise
        except Exception as exc:
            raise ProviderUnavailable(
                "The store could not price this basket right now. Please try again."
            ) from exc

        current = await self._basket(basket_id)
        if current.revision != basket.revision:
            raise ApprovalRefused(
                "The basket changed while the store was pricing it. Please review it again."
            )
        check_quote(current, quote)

        now = self.clock()
        await self._supersede_open(current.id)
        approval = Approval(
            id=uuid.uuid4().hex,
            basket_id=current.id,
            basket_revision=current.revision,
            fingerprint=basket_fingerprint(current, quote.payable_paise),
            payable_paise=quote.payable_paise,
            created_at=now,
            expires_at=now + APPROVAL_TTL,
        )
        await self.approvals.save(approval)
        current.status = BasketStatus.AWAITING_APPROVAL
        await self.baskets.save(current)
        return ApprovalRequest(approval=approval, quote=quote, basket=current)

    async def approve(
        self, approval_id: str, fingerprint: str, household_id: str | None = None
    ) -> Approval:
        approval = await self._approval(approval_id)
        if household_id is not None:
            await self._owned_basket(approval.basket_id, household_id)
        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalRefused(f"This approval is {approval.status.value}; ask for a fresh one.")
        now = self.clock()
        if now >= approval.expires_at:
            await self._close(approval, ApprovalStatus.EXPIRED)
            raise ApprovalRefused("This approval expired. Please review the basket again.")
        if fingerprint != approval.fingerprint:
            raise ApprovalRefused(
                "What you approved does not match the current bill. Please review it again."
            )
        basket = await self._basket(approval.basket_id)
        if (
            basket.revision != approval.basket_revision
            or basket_fingerprint(basket, approval.payable_paise) != approval.fingerprint
        ):
            await self._close(approval, ApprovalStatus.SUPERSEDED)
            raise ApprovalRefused(
                "The basket changed after this bill was shown. Please review it again."
            )
        approval.status = ApprovalStatus.APPROVED
        approval.decided_at = now
        await self.approvals.save(approval)
        basket.status = BasketStatus.APPROVED
        await self.baskets.save(basket)
        return approval

    async def reject(self, approval_id: str, household_id: str | None = None) -> Approval:
        approval = await self._approval(approval_id)
        if household_id is not None:
            await self._owned_basket(approval.basket_id, household_id)
        if approval.status not in OPEN_APPROVAL_STATUSES:
            raise ApprovalRefused(f"This approval is already {approval.status.value}.")
        await self._close(approval, ApprovalStatus.REJECTED)
        basket = await self.baskets.get(approval.basket_id)
        if basket is not None and basket.status in {
            BasketStatus.AWAITING_APPROVAL,
            BasketStatus.APPROVED,
        }:
            basket.status = BasketStatus.DRAFT
            await self.baskets.save(basket)
        return approval

    async def _supersede_open(self, basket_id: str) -> None:
        for approval in await self.approvals.for_basket(basket_id):
            if approval.status in OPEN_APPROVAL_STATUSES:
                await self._close(approval, ApprovalStatus.SUPERSEDED)

    async def _close(self, approval: Approval, status: ApprovalStatus) -> None:
        approval.status = status
        approval.decided_at = self.clock()
        await self.approvals.save(approval)

    async def _basket(self, basket_id: str) -> Basket:
        basket = await self.baskets.get(basket_id)
        if basket is None:
            raise NotFound("Basket not found.")
        return basket

    async def _approval(self, approval_id: str) -> Approval:
        approval = await self.approvals.get(approval_id)
        if approval is None:
            raise NotFound("Approval not found.")
        return approval

    async def _owned_basket(self, basket_id: str, household_id: str) -> Basket:
        basket = await self._basket(basket_id)
        if basket.household_id != household_id:
            # Do not reveal that another household's approval exists.
            raise NotFound("Approval not found.")
        return basket
