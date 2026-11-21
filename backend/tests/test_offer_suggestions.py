from datetime import UTC, datetime, timedelta

import pytest

from app.shopping.models import Basket, BasketLine, ProductRef
from app.shopping.offer_suggestions import (
    LivePurchaseCandidate,
    ThresholdOffer,
    suggest_threshold_additions,
)


NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


def ref(product_id: str, variant_id: str = "standard") -> ProductRef:
    return ProductRef(
        provider="zepto",
        product_id=product_id,
        variant_id=variant_id,
        sku_id=f"sku-{variant_id}",
    )


def line(
    product_id: str,
    *,
    price: int = 6_000,
    quantity: int = 1,
    available: bool = True,
    provider: str = "zepto",
) -> BasketLine:
    item_ref = ref(product_id)
    if provider != "zepto":
        item_ref = ProductRef(
            provider=provider,  # type: ignore[arg-type]
            product_id=product_id,
            variant_id="standard",
            sku_id="sku-standard",
        )
    return BasketLine(
        ref=item_ref,
        name=f"Product {product_id}",
        unit_price_paise=price,
        quantity=quantity,
        available=available,
    )


def basket(*lines: BasketLine) -> Basket:
    return Basket(
        id="basket-1",
        household_id="household-1",
        provider="zepto",
        lines=list(lines),
        created_at=NOW,
        updated_at=NOW,
    )


def offer(**updates: object) -> ThresholdOffer:
    values: dict[str, object] = {
        "id": "free-delivery-99",
        "label": "Free delivery above ₹99",
        "minimum_qualifying_total_paise": 20_000,
        "confirmed_benefit_paise": 3_000,
        "is_actionable_for_selected_payment": True,
    }
    values.update(updates)
    return ThresholdOffer(**values)


def candidate(
    live_line: BasketLine,
    *,
    historical_ref: ProductRef | None = None,
    explicitly_useful: bool = True,
    last_purchased_at: datetime = NOW - timedelta(days=7),
) -> LivePurchaseCandidate:
    return LivePurchaseCandidate(
        historical_ref=historical_ref or live_line.ref,
        live_line=live_line,
        last_purchased_at=last_purchased_at,
        explicitly_useful=explicitly_useful,
    )


def test_suggestion_uses_integer_paise_and_never_mutates_the_basket() -> None:
    current_basket = basket(line("rice", price=15_000))
    chocolate = line("chocolate", price=6_000)

    suggestions = suggest_threshold_additions(
        current_basket,
        current_qualifying_total_paise=15_000,
        offer=offer(),
        candidates=[candidate(chocolate)],
        now=NOW,
    )

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.remaining_to_unlock_paise == 5_000
    assert suggestion.threshold_coverage_paise == 5_000
    assert suggestion.remaining_after_add_paise == 0
    assert suggestion.candidate_total_paise == 6_000
    assert suggestion.confirmed_benefit_paise == 3_000
    assert suggestion.net_incremental_cost_paise == 3_000
    assert suggestion.requires_explicit_add is True
    assert suggestion.requires_fresh_quote is True
    assert current_basket.lines == [line("rice", price=15_000)]


@pytest.mark.parametrize(
    "blocked_candidate",
    [
        lambda: candidate(line("not-explicit"), explicitly_useful=False),
        lambda: candidate(line("stale"), last_purchased_at=NOW - timedelta(days=91)),
        lambda: candidate(
            line("different-live-product"), historical_ref=ref("historic-product")
        ),
        lambda: candidate(line("unavailable", available=False)),
        lambda: candidate(line("partial", price=4_999)),
        lambda: candidate(line("other-provider", provider="swiggy_instamart")),
    ],
)
def test_filters_candidates_that_are_not_safe_or_relevant(blocked_candidate: object) -> None:
    result = suggest_threshold_additions(
        basket(),
        current_qualifying_total_paise=15_000,
        offer=offer(),
        candidates=[blocked_candidate()],  # type: ignore[operator]
        now=NOW,
    )

    assert result == []


def test_excludes_a_current_basket_line_even_when_it_is_a_useful_recent_usual() -> None:
    chocolate = line("chocolate")

    result = suggest_threshold_additions(
        basket(chocolate),
        current_qualifying_total_paise=15_000,
        offer=offer(),
        candidates=[candidate(chocolate)],
        now=NOW,
    )

    assert result == []


@pytest.mark.parametrize(
    "offer_update",
    [
        {"is_actionable_for_selected_payment": False},
        {"confirmed_benefit_paise": 0},
        {"minimum_qualifying_total_paise": 15_000},
    ],
)
def test_does_not_claim_savings_without_a_live_actionable_benefit(
    offer_update: dict[str, object],
) -> None:
    result = suggest_threshold_additions(
        basket(),
        current_qualifying_total_paise=15_000,
        offer=offer(**offer_update),
        candidates=[candidate(line("chocolate"))],
        now=NOW,
    )

    assert result == []


def test_prefers_lowest_true_net_incremental_cost() -> None:
    result = suggest_threshold_additions(
        basket(),
        current_qualifying_total_paise=15_000,
        offer=offer(),
        candidates=[
            candidate(line("larger", price=8_000)),
            candidate(line("smaller", price=6_000)),
        ],
        now=NOW,
    )

    assert [item.line.ref.product_id for item in result] == ["smaller", "larger"]
    assert [item.net_incremental_cost_paise for item in result] == [3_000, 5_000]


def test_rejects_a_timezone_free_purchase_timestamp_without_guessing() -> None:
    result = suggest_threshold_additions(
        basket(),
        current_qualifying_total_paise=15_000,
        offer=offer(),
        candidates=[
            candidate(line("chocolate"), last_purchased_at=datetime(2026, 11, 21, 12))
        ],
        now=NOW,
    )

    assert result == []
