from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


PatternKind = Literal[
    "repeat_frequency",
    "repeat_cadence",
    "weekday_routine",
    "daypart_routine",
    "quantity_routine",
    "basket_affinity",
]

WEEKDAYS = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)


class PatternEvidence(BaseModel):
    kind: PatternKind
    confidence: float = Field(ge=0, le=1)
    observations: int = Field(ge=2)
    statement: str
    matches_current_context: bool = False


class RelatedProduct(BaseModel):
    product_id: UUID
    product_name: str
    shared_order_count: int = Field(ge=3)
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class PurchaseObservation:
    product_id: UUID
    product_name: str
    provider_item_id: str | None
    order_id: UUID
    ordered_at: datetime
    quantity: int
    local_weekday: int
    local_hour: int


@dataclass(frozen=True)
class MinedProductPattern:
    product_id: UUID
    product_name: str
    provider_item_id: str | None
    purchase_count: int
    total_quantity: int
    last_ordered_at: datetime
    confidence: float
    score: float
    features: dict[str, float]
    evidence: tuple[PatternEvidence, ...]
    related_products: tuple[RelatedProduct, ...]


def mine_purchase_patterns(
    observations: list[PurchaseObservation],
    completed_order_count: int,
    now: datetime,
    current_weekday: int,
    current_hour: int,
) -> list[MinedProductPattern]:
    """Discover supported routines; repetition alone is not treated as a pattern."""
    if completed_order_count < 3:
        return []

    by_product: dict[UUID, list[PurchaseObservation]] = defaultdict(list)
    by_order: dict[UUID, list[PurchaseObservation]] = defaultdict(list)
    for observation in observations:
        by_product[observation.product_id].append(observation)
        by_order[observation.order_id].append(observation)

    order_support = {
        product_id: len({item.order_id for item in items})
        for product_id, items in by_product.items()
    }
    affinity = _basket_affinities(by_order, by_product, order_support, completed_order_count)
    patterns: list[MinedProductPattern] = []

    for product_id, items in by_product.items():
        items = sorted(items, key=lambda item: item.ordered_at)
        count = order_support[product_id]
        evidence, features = _product_evidence(
            items,
            count,
            completed_order_count,
            current_weekday,
            current_hour,
        )
        related = affinity.get(product_id, ())
        if related:
            strongest = related[0]
            evidence.append(
                PatternEvidence(
                    kind="basket_affinity",
                    confidence=strongest.confidence,
                    observations=strongest.shared_order_count,
                    statement=(
                        f"It appeared with {strongest.product_name} in "
                        f"{strongest.shared_order_count} completed orders."
                    ),
                )
            )
            features["basket_affinity"] = strongest.confidence
        if not evidence:
            continue

        evidence.sort(
            key=lambda item: (item.matches_current_context, item.confidence),
            reverse=True,
        )
        confidence = mean(item.confidence for item in evidence[:3])
        context_bonus = 0.08 if any(item.matches_current_context for item in evidence) else 0
        days_since = max(0, (now - items[-1].ordered_at).days)
        evidence_freshness = max(0.0, 0.05 - min(days_since, 90) / 1800)
        features["evidence_freshness"] = round(evidence_freshness / 0.05, 3)
        score = min(1.0, confidence + context_bonus + evidence_freshness)
        first = items[0]
        patterns.append(
            MinedProductPattern(
                product_id=product_id,
                product_name=first.product_name,
                provider_item_id=first.provider_item_id,
                purchase_count=count,
                total_quantity=sum(item.quantity for item in items),
                last_ordered_at=items[-1].ordered_at,
                confidence=round(confidence, 3),
                score=round(score, 3),
                features=features,
                evidence=tuple(evidence),
                related_products=related,
            )
        )

    return sorted(
        patterns,
        key=lambda item: (item.score, item.last_ordered_at),
        reverse=True,
    )


def _product_evidence(
    items: list[PurchaseObservation],
    purchase_count: int,
    completed_order_count: int,
    current_weekday: int,
    current_hour: int,
) -> tuple[list[PatternEvidence], dict[str, float]]:
    evidence: list[PatternEvidence] = []
    support = purchase_count / completed_order_count
    features = {
        "frequency_support": round(support, 3),
        "repetition_strength": round(min(1.0, purchase_count / 8), 3),
        "cadence_stability": 0.0,
        "weekday_affinity": 0.0,
        "weekday_context_match": 0.0,
        "daypart_affinity": 0.0,
        "daypart_context_match": 0.0,
        "quantity_consistency": 0.0,
        "basket_affinity": 0.0,
    }
    if purchase_count >= 3 and support >= 0.25:
        confidence = min(0.95, 0.45 + support * 0.45 + (purchase_count - 3) * 0.03)
        evidence.append(
            PatternEvidence(
                kind="repeat_frequency",
                confidence=round(confidence, 3),
                observations=purchase_count,
                statement=(
                    f"It appeared in {purchase_count} of {completed_order_count} "
                    "completed orders."
                ),
            )
        )

    intervals = [
        max(1, (later.ordered_at - earlier.ordered_at).days)
        for earlier, later in zip(items, items[1:], strict=False)
    ]
    if len(intervals) >= 2:
        average_interval = mean(intervals)
        mean_deviation = mean(abs(interval - average_interval) for interval in intervals)
        stability = max(0.0, 1 - mean_deviation / average_interval)
        features["cadence_stability"] = round(stability, 3)
        if stability >= 0.6:
            evidence.append(
                PatternEvidence(
                    kind="repeat_cadence",
                    confidence=round(stability, 3),
                    observations=purchase_count,
                    statement=(
                        f"Its order intervals cluster around {round(average_interval)} "
                        f"days across {purchase_count} purchases."
                    ),
                )
            )

    weekdays = Counter(item.local_weekday for item in items)
    weekday, weekday_count = weekdays.most_common(1)[0]
    weekday_share = weekday_count / purchase_count
    features["weekday_affinity"] = round(weekday_share, 3)
    if weekday == current_weekday:
        features["weekday_context_match"] = round(weekday_share, 3)
    if purchase_count >= 3 and weekday_share >= 0.6:
        evidence.append(
            PatternEvidence(
                kind="weekday_routine",
                confidence=round(weekday_share, 3),
                observations=weekday_count,
                statement=(
                    f"{weekday_count} of {purchase_count} purchases were on "
                    f"{WEEKDAYS[weekday]}."
                ),
                matches_current_context=weekday == current_weekday,
            )
        )

    dayparts = Counter(_daypart(item.local_hour) for item in items)
    daypart, daypart_count = dayparts.most_common(1)[0]
    daypart_share = daypart_count / purchase_count
    features["daypart_affinity"] = round(daypart_share, 3)
    if daypart == _daypart(current_hour):
        features["daypart_context_match"] = round(daypart_share, 3)
    if purchase_count >= 3 and daypart_share >= 0.7:
        evidence.append(
            PatternEvidence(
                kind="daypart_routine",
                confidence=round(daypart_share, 3),
                observations=daypart_count,
                statement=(
                    f"{daypart_count} of {purchase_count} purchases happened in "
                    f"the {daypart}."
                ),
                matches_current_context=daypart == _daypart(current_hour),
            )
        )

    quantities = Counter(item.quantity for item in items)
    quantity, quantity_count = quantities.most_common(1)[0]
    quantity_share = quantity_count / purchase_count
    features["quantity_consistency"] = round(quantity_share, 3)
    if purchase_count >= 3 and quantity_share >= 0.7:
        evidence.append(
            PatternEvidence(
                kind="quantity_routine",
                confidence=round(quantity_share, 3),
                observations=quantity_count,
                statement=(
                    f"Quantity {quantity} was chosen in {quantity_count} of "
                    f"{purchase_count} purchases."
                ),
            )
        )
    return evidence, features


def _basket_affinities(
    by_order: dict[UUID, list[PurchaseObservation]],
    by_product: dict[UUID, list[PurchaseObservation]],
    order_support: dict[UUID, int],
    completed_order_count: int,
) -> dict[UUID, tuple[RelatedProduct, ...]]:
    pair_counts: Counter[tuple[UUID, UUID]] = Counter()
    for items in by_order.values():
        product_ids = sorted({item.product_id for item in items}, key=str)
        for index, left in enumerate(product_ids):
            for right in product_ids[index + 1 :]:
                pair_counts[(left, right)] += 1

    related: dict[UUID, list[RelatedProduct]] = defaultdict(list)
    names = {
        product_id: items[0].product_name for product_id, items in by_product.items()
    }
    for (left, right), shared_count in pair_counts.items():
        if shared_count < 3:
            continue
        for product_id, companion_id in ((left, right), (right, left)):
            conditional = shared_count / order_support[product_id]
            companion_support = order_support[companion_id] / completed_order_count
            lift = conditional / companion_support
            if conditional < 0.6 or lift < 1.1:
                continue
            confidence = min(1.0, conditional * min(1.0, lift / 2))
            related[product_id].append(
                RelatedProduct(
                    product_id=companion_id,
                    product_name=names[companion_id],
                    shared_order_count=shared_count,
                    confidence=round(confidence, 3),
                )
            )

    return {
        product_id: tuple(
            sorted(items, key=lambda item: item.confidence, reverse=True)[:3]
        )
        for product_id, items in related.items()
    }


def _daypart(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"
