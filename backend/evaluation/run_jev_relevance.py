"""Evaluate Jev against the labelled shortlist without calling commerce providers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.commerce.jev import JevProductReranker
from app.commerce.relevance import refine_search_result
from app.commerce.swiggy.models import ProductSearchResult
from app.core.config import Settings
from evaluation.run_relevance_benchmark import CASES_PATH, _product


async def evaluate() -> dict[str, Any]:
    settings = Settings()
    if not settings.jev_api_key.get_secret_value().strip():
        raise SystemExit("JEV_API_KEY is not configured.")

    suite = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    reranker = JevProductReranker(
        api_key=settings.jev_api_key,
        base_url=settings.jev_base_url,
        model=settings.jev_model,
        timeout_seconds=settings.jev_timeout_seconds,
    )
    outcomes: list[dict[str, Any]] = []
    resolved_models: set[str] = set()
    for case in suite["cases"]:
        if case["kind"] != "ranking":
            continue
        baseline = refine_search_result(
            case["query"],
            ProductSearchResult(
                products=[_product(raw) for raw in case["products"]]
            ),
        )
        outcome = await reranker.rerank_with_diagnostics(case["query"], baseline)
        if outcome.resolved_model:
            resolved_models.add(outcome.resolved_model)
        baseline_ids = [product.product_id for product in baseline.products]
        result_ids = [product.product_id for product in outcome.result.products]
        baseline_by_id = {
            product.product_id: product.model_dump() for product in baseline.products
        }
        result_by_id = {
            product.product_id: product.model_dump()
            for product in outcome.result.products
        }
        expected = case["expected"]
        checks = {
            "jev_applied": outcome.applied,
            "membership_preserved": sorted(result_ids) == sorted(baseline_ids),
            "provider_fields_preserved": result_by_id == baseline_by_id,
            "availability_partition_preserved": result_ids
            == sorted(
                result_ids,
                key=lambda product_id: not result_by_id[product_id]["available"],
            ),
            "excluded_mismatches_stay_excluded": not (
                set(expected.get("excluded_product_ids", [])) & set(result_ids)
            ),
            "result_cap_preserved": len(result_ids) <= expected.get("max_results", 6),
        }
        top_product_id = expected.get("top_product_id")
        if top_product_id is not None:
            checks["labelled_best_is_top"] = bool(result_ids) and result_ids[0] == top_product_id
        outcomes.append(
            {
                "id": case["id"],
                "passed": all(checks.values()),
                "checks": checks,
            }
        )

    passed = sum(case["passed"] for case in outcomes)
    return {
        "suite": suite["version"],
        "provider_calls": 0,
        "model_requested": settings.jev_model,
        "models_resolved": sorted(resolved_models),
        "passed": passed,
        "total": len(outcomes),
        "score_percent": round(100 * passed / len(outcomes), 1) if outcomes else 0.0,
        "cases": outcomes,
    }


async def main() -> None:
    result = await evaluate()
    print(json.dumps(result, indent=2))
    if result["passed"] != result["total"]:
        raise SystemExit("Jev relevance evaluation failed.")


if __name__ == "__main__":
    asyncio.run(main())
