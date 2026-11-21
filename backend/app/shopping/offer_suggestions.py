"""Pure, conservative offer-addition suggestions.

This module deliberately has no database or provider dependency.  Callers must
resolve a historical product to the *exact* currently-live product variant and
price before passing it in.  A purchase record alone is never enough to create
a suggestion: the caller must also have an explicit usefulness signal from the
household.

The output is advice only.  It does not mutate a basket and always requires a
fresh provider quote and a separate user action before an item may be added.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, Sequence

from pydantic import BaseModel, Field

from app.shopping.basket import same_variant
from app.shopping.models import Basket, BasketLine, ProductRef


DEFAULT_MAX_PURCHASE_AGE = timedelta(days=90)


class ThresholdOffer(BaseModel):
    """A provider-confirmed, currently actionable threshold benefit.

    ``minimum_qualifying_total_paise`` and the caller's current qualifying
    total must use the same provider-defined basis (normally the item subtotal,
    not an estimated final payable amount).  ``confirmed_benefit_paise`` is
    zero unless the provider has supplied a concrete benefit for the selected
    payment and delivery context.
    """

    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=500)
    minimum_qualifying_total_paise: int = Field(ge=0)
    confirmed_benefit_paise: int = Field(ge=0)
    is_actionable_for_selected_payment: bool = False


class LivePurchaseCandidate(BaseModel):
    """One safely-resolved product that could be offered to the household.

    ``historical_ref`` must exactly match ``live_line.ref``.  This intentionally
    rejects fuzzy name matching: prices and availability belong to a specific
    provider variant, not a product name.  ``explicitly_useful`` is reserved
    for an explicit preference, saved usual, or a direct user request; it must
    never be inferred from purchase history alone.
    """

    historical_ref: ProductRef
    live_line: BasketLine
    last_purchased_at: datetime
    explicitly_useful: bool = False


class OfferSuggestion(BaseModel):
    """An informational suggestion; never an instruction to mutate a basket."""

    offer_id: str
    offer_label: str
    line: BasketLine
    remaining_to_unlock_paise: int = Field(ge=0)
    threshold_coverage_paise: int = Field(ge=0)
    remaining_after_add_paise: int = Field(ge=0)
    candidate_total_paise: int = Field(ge=0)
    confirmed_benefit_paise: int = Field(ge=0)
    net_incremental_cost_paise: int
    requires_explicit_add: Literal[True] = True
    requires_fresh_quote: Literal[True] = True


def suggest_threshold_additions(
    basket: Basket,
    *,
    current_qualifying_total_paise: int,
    offer: ThresholdOffer,
    candidates: Sequence[LivePurchaseCandidate],
    now: datetime,
    max_purchase_age: timedelta = DEFAULT_MAX_PURCHASE_AGE,
) -> list[OfferSuggestion]:
    """Return useful, exact, live candidates that individually unlock an offer.

    The function intentionally does not combine several products to reach a
    threshold.  A combined recommendation would add pressure to buy items that
    are not independently useful.  All arithmetic is performed in integer
    paise.  Invalid, stale, unavailable, mismatched, already-in-basket, or
    merely historical candidates are ignored rather than guessed about.
    """

    if current_qualifying_total_paise < 0:
        raise ValueError("current_qualifying_total_paise cannot be negative")
    if max_purchase_age < timedelta(0):
        raise ValueError("max_purchase_age cannot be negative")

    # A zero/unknown benefit or unavailable payment method cannot support an
    # honest savings claim.  Likewise, an already-unlocked offer needs no add-on.
    if (
        not offer.is_actionable_for_selected_payment
        or offer.confirmed_benefit_paise <= 0
        or current_qualifying_total_paise >= offer.minimum_qualifying_total_paise
    ):
        return []

    now_utc = _as_utc(now)
    remaining_to_unlock = (
        offer.minimum_qualifying_total_paise - current_qualifying_total_paise
    )
    suggestions: list[OfferSuggestion] = []

    for candidate in candidates:
        if not _is_eligible_candidate(
            basket=basket,
            candidate=candidate,
            now=now_utc,
            max_purchase_age=max_purchase_age,
        ):
            continue

        candidate_total = candidate.live_line.unit_price_paise * candidate.live_line.quantity
        # Do not nudge someone part-way to a threshold.  The candidate itself
        # must unlock the confirmed benefit.
        if candidate_total < remaining_to_unlock:
            continue

        suggestions.append(
            OfferSuggestion(
                offer_id=offer.id,
                offer_label=offer.label,
                line=candidate.live_line.model_copy(deep=True),
                remaining_to_unlock_paise=remaining_to_unlock,
                threshold_coverage_paise=min(candidate_total, remaining_to_unlock),
                remaining_after_add_paise=0,
                candidate_total_paise=candidate_total,
                confirmed_benefit_paise=offer.confirmed_benefit_paise,
                net_incremental_cost_paise=(
                    candidate_total - offer.confirmed_benefit_paise
                ),
            )
        )

    return sorted(
        suggestions,
        key=lambda suggestion: (
            suggestion.net_incremental_cost_paise,
            suggestion.candidate_total_paise,
            suggestion.line.name.casefold(),
            suggestion.line.ref.product_id,
            suggestion.line.ref.variant_id,
        ),
    )


def _is_eligible_candidate(
    *,
    basket: Basket,
    candidate: LivePurchaseCandidate,
    now: datetime,
    max_purchase_age: timedelta,
) -> bool:
    line = candidate.live_line
    if not candidate.explicitly_useful or not line.available:
        return False
    if line.ref.provider != basket.provider:
        return False
    # Exact identity prevents a historical name match from suggesting a
    # different pack, flavour, or provider listing.
    if candidate.historical_ref != line.ref:
        return False
    try:
        purchased_at = _as_utc(candidate.last_purchased_at)
    except ValueError:
        return False
    if purchased_at > now or now - purchased_at > max_purchase_age:
        return False
    return not any(same_variant(existing.ref, line.ref) for existing in basket.lines)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)
