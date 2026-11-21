from __future__ import annotations

from dataclasses import dataclass
import math


MODEL_VERSION = "personal-hybrid-logistic-v1"
FEATURE_NAMES = (
    "frequency_support",
    "repetition_strength",
    "cadence_stability",
    "weekday_affinity",
    "weekday_context_match",
    "daypart_affinity",
    "daypart_context_match",
    "quantity_consistency",
    "basket_affinity",
    "evidence_freshness",
)
DEFAULT_WEIGHTS = {
    "frequency_support": 0.9,
    "repetition_strength": 0.7,
    "cadence_stability": 0.8,
    "weekday_affinity": 0.25,
    "weekday_context_match": 0.65,
    "daypart_affinity": 0.2,
    "daypart_context_match": 0.55,
    "quantity_consistency": 0.2,
    "basket_affinity": 0.75,
    "evidence_freshness": 0.25,
}
DEFAULT_BIAS = -1.6


@dataclass(frozen=True)
class RankResult:
    score: float
    model_version: str


def score_candidate(
    features: dict[str, float],
    weights: dict[str, float],
    bias: float,
) -> RankResult:
    logit = bias + sum(
        weights.get(name, DEFAULT_WEIGHTS[name]) * _bounded(features.get(name, 0.0))
        for name in FEATURE_NAMES
    )
    return RankResult(
        score=round(1 / (1 + math.exp(-max(-20.0, min(20.0, logit)))), 4),
        model_version=MODEL_VERSION,
    )


def update_personal_model(
    features: dict[str, float],
    label: float,
    weights: dict[str, float],
    bias: float,
    training_examples: int,
) -> tuple[dict[str, float], float]:
    """One bounded online-logistic update from an explicit recommendation response."""
    prediction = score_candidate(features, weights, bias).score
    error = _bounded(label) - prediction
    learning_rate = 0.25 / math.sqrt(training_examples + 1)
    regularization = 0.002
    updated = dict(weights)
    for name in FEATURE_NAMES:
        current = updated.get(name, DEFAULT_WEIGHTS[name])
        feature = _bounded(features.get(name, 0.0))
        value = current + learning_rate * (error * feature - regularization * current)
        updated[name] = round(max(-4.0, min(4.0, value)), 6)
    updated_bias = bias + learning_rate * error
    return updated, round(max(-4.0, min(4.0, updated_bias)), 6)


def initial_weights() -> dict[str, float]:
    return dict(DEFAULT_WEIGHTS)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))
