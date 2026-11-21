from evaluation.run_relevance_benchmark import CASES_PATH, evaluate


def test_relevance_benchmark_is_complete_and_passing() -> None:
    result = evaluate()

    assert CASES_PATH.exists()
    assert result["provider_free"] is True
    assert result["passed"] == result["total"] == 11
    assert result["score_percent"] == 100.0
    assert result["metrics"] == {
        "query_guard": {"passed": 8, "total": 8},
        "ranking": {"passed": 3, "total": 3},
    }


def test_relevance_benchmark_case_ids_and_labels_are_stable() -> None:
    result = evaluate()
    cases = {case["id"]: case for case in result["cases"]}

    assert cases["generic-capabilities-question-does-not-search"]["passed"] is True
    assert cases["amul-brand-only-needs-clarification"]["passed"] is True
    assert cases["axe-does-not-show-denver"]["passed"] is True
    assert cases["amul-milk-one-litre-beats-wrong-product-and-pack"]["passed"] is True
    assert cases["ui-presentation-is-capped-at-six"]["passed"] is True
