from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from uuid import UUID, uuid5

from app.memory.purchase_patterns import PurchaseObservation, mine_purchase_patterns
from app.memory.recommender import (
    DEFAULT_BIAS,
    initial_weights,
    score_candidate,
    update_personal_model,
)


NAMESPACE = UUID("7887297e-c6ce-4ed3-9124-5b3b01c153cc")
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)


def stable_id(value: str) -> UUID:
    return uuid5(NAMESPACE, value)


def observation(
    product: str,
    order_number: int,
    days_ago: int,
    quantity: int = 1,
) -> PurchaseObservation:
    ordered_at = NOW - timedelta(days=days_ago)
    return PurchaseObservation(
        product_id=stable_id(product),
        product_name=product,
        provider_item_id=f"provider-{product.casefold().replace(' ', '-')}",
        order_id=stable_id(f"order-{order_number}"),
        ordered_at=ordered_at,
        quantity=quantity,
        local_weekday=ordered_at.weekday(),
        local_hour=19,
    )


def main() -> None:
    observations = [
        observation("Milk 1 L", 1, 28),
        observation("Milk 1 L", 2, 21),
        observation("Milk 1 L", 3, 14),
        observation("Milk 1 L", 4, 7),
        observation("Dish soap", 1, 28),
        observation("Dish soap", 3, 14),
        observation("Impulse snack", 2, 21),
        observation("Impulse snack", 5, 3),
    ]
    candidates = mine_purchase_patterns(
        observations,
        completed_order_count=5,
        now=NOW,
        current_weekday=(NOW - timedelta(days=7)).weekday(),
        current_hour=19,
    )
    ranked = sorted(
        candidates,
        key=lambda item: score_candidate(
            item.features, initial_weights(), DEFAULT_BIAS
        ).score,
        reverse=True,
    )
    top_name = ranked[0].product_name if ranked else None

    milk = next(item for item in ranked if item.product_name == "Milk 1 L")
    weights = initial_weights()
    before = score_candidate(milk.features, weights, DEFAULT_BIAS).score
    positive_weights, positive_bias = update_personal_model(
        milk.features, 1.0, weights, DEFAULT_BIAS, 0
    )
    negative_weights, negative_bias = update_personal_model(
        milk.features, 0.0, weights, DEFAULT_BIAS, 0
    )
    after_positive = score_candidate(
        milk.features, positive_weights, positive_bias
    ).score
    after_negative = score_candidate(
        milk.features, negative_weights, negative_bias
    ).score

    checks = {
        "temporal_holdout_hit_at_1": top_name == "Milk 1 L",
        "two_repeat_noise_rejected": all(
            item.product_name != "Impulse snack" for item in candidates
        ),
        "positive_feedback_raises_score": after_positive > before,
        "negative_feedback_lowers_score": after_negative < before,
    }
    result = {
        "suite": "personal-hybrid-recommender-v1",
        "passed": sum(checks.values()),
        "total": len(checks),
        "checks": checks,
        "top_candidate": top_name,
        "personalization_probe": {
            "before": before,
            "after_positive": after_positive,
            "after_negative": after_negative,
        },
    }
    print(json.dumps(result, indent=2))
    if not all(checks.values()):
        raise SystemExit("Recommender evaluation failed.")


if __name__ == "__main__":
    main()
