from evaluation.run_instamart_workflows import CASES_PATH, evaluate


def test_official_instamart_workflows_have_an_honest_readiness_result() -> None:
    result = evaluate()

    assert CASES_PATH.exists()
    assert result["provider_free"] is True
    assert result["passed"] == result["total"] == 6
    readiness = {case["id"]: case["readiness"] for case in result["cases"]}
    assert readiness == {
        "recipe-to-cart": "partial",
        "dietary-compliant-shopping": "partial",
        "weekly-meal-prep": "supported_with_confirmation",
        "party-shopping": "partial",
        "budget-shopping": "partial",
        "brand-comparison": "supported_with_confirmation",
    }


def test_workflow_audit_never_claims_live_provider_coverage() -> None:
    result = evaluate()

    assert all(case["missing_capabilities"] == [] for case in result["cases"])
    assert "checkout" not in result["available_capabilities"]
    assert "order_placement" not in result["available_capabilities"]
