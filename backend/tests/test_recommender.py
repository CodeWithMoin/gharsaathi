from app.memory.recommender import (
    DEFAULT_BIAS,
    initial_weights,
    score_candidate,
    update_personal_model,
)


FEATURES = {
    "frequency_support": 0.7,
    "repetition_strength": 0.6,
    "cadence_stability": 0.9,
    "weekday_affinity": 0.8,
    "weekday_context_match": 0.8,
    "daypart_affinity": 0.7,
    "daypart_context_match": 0.7,
    "quantity_consistency": 1.0,
    "basket_affinity": 0.6,
    "evidence_freshness": 0.9,
}


def test_online_ranker_learns_opposite_personal_responses() -> None:
    weights = initial_weights()
    initial = score_candidate(FEATURES, weights, DEFAULT_BIAS).score

    positive_weights, positive_bias = update_personal_model(
        FEATURES, 1.0, weights, DEFAULT_BIAS, training_examples=0
    )
    negative_weights, negative_bias = update_personal_model(
        FEATURES, 0.0, weights, DEFAULT_BIAS, training_examples=0
    )

    positive = score_candidate(FEATURES, positive_weights, positive_bias).score
    negative = score_candidate(FEATURES, negative_weights, negative_bias).score
    assert positive > initial > negative
