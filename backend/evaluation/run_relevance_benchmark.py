"""Run deterministic, provider-free product-search relevance regression cases."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.commerce.query_guard import clarification_for_query, non_search_reason
from app.commerce.relevance import refine_search_result
from app.commerce.swiggy.models import Price, Product, ProductSearchResult


CASES_PATH = Path(__file__).with_name("relevance_cases.json")


def _product(raw: dict[str, Any]) -> Product:
    """Create a complete local product model from the fixture's useful fields."""

    identifier = raw["product_id"]
    return Product(
        product_id=identifier,
        sku_id=f"sku-{identifier}",
        spin_id=f"spin-{identifier}",
        name=raw["name"],
        brand=raw.get("brand"),
        pack_size=raw.get("pack_size"),
        price=Price(mrp=100, offer_price=90),
        available=True,
    )


def _guard_checks(case: dict[str, Any]) -> list[tuple[str, bool]]:
    expected = case["expected"]
    reason = non_search_reason(case["query"])
    checks = [
        (
            "search-routing",
            (reason is None) == expected["should_search"],
        )
    ]
    if "reason" in expected:
        checks.append(("guard-reason", reason == expected["reason"]))
    if "clarification_contains" in expected:
        clarification = clarification_for_query(case["query"]) or ""
        checks.append(
            (
                "clarification-content",
                all(
                    text.casefold() in clarification.casefold()
                    for text in expected["clarification_contains"]
                ),
            )
        )
    return checks


def _ranking_checks(case: dict[str, Any]) -> list[tuple[str, bool]]:
    expected = case["expected"]
    result = refine_search_result(
        case["query"],
        ProductSearchResult(products=[_product(raw) for raw in case["products"]]),
    )
    product_ids = [product.product_id for product in result.products]
    checks: list[tuple[str, bool]] = []
    if "returned_product_ids" in expected:
        checks.append(
            ("returned-results", product_ids == expected["returned_product_ids"])
        )
    if "excluded_product_ids" in expected:
        checks.append(
            (
                "excluded-mismatches",
                not set(expected["excluded_product_ids"]) & set(product_ids),
            )
        )
    if "top_product_id" in expected:
        checks.append(
            ("top-result", bool(product_ids) and product_ids[0] == expected["top_product_id"])
        )
    if "must_precede" in expected:
        top = expected["top_product_id"]
        checks.append(
            (
                "brand-product-pack-order",
                top in product_ids
                and all(
                    other not in product_ids
                    or product_ids.index(top) < product_ids.index(other)
                    for other in expected["must_precede"]
                ),
            )
        )
    if "max_results" in expected:
        checks.append(("ui-result-cap", len(product_ids) <= expected["max_results"]))
    return checks


def evaluate(cases_path: Path = CASES_PATH) -> dict[str, Any]:
    """Evaluate fixtures without any MCP, HTTP, database, or model calls."""

    suite = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = suite["cases"]
    outcomes: list[dict[str, Any]] = []
    category_totals: Counter[str] = Counter()
    category_passed: Counter[str] = Counter()

    for case in cases:
        checks = (
            _guard_checks(case)
            if case["kind"] == "query_guard"
            else _ranking_checks(case)
        )
        passed = all(check[1] for check in checks)
        category_totals[case["kind"]] += 1
        category_passed[case["kind"]] += int(passed)
        outcomes.append(
            {
                "id": case["id"],
                "label": case["label"],
                "kind": case["kind"],
                "passed": passed,
                "checks": [{"name": name, "passed": outcome} for name, outcome in checks],
            }
        )

    passed = sum(outcome["passed"] for outcome in outcomes)
    total = len(outcomes)
    return {
        "suite": suite["version"],
        "provider_free": True,
        "passed": passed,
        "total": total,
        "score_percent": round(100 * passed / total, 1) if total else 0.0,
        "metrics": {
            kind: {"passed": category_passed[kind], "total": category_totals[kind]}
            for kind in sorted(category_totals)
        },
        "cases": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    result = evaluate(args.cases)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.report is not None:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    if result["passed"] != result["total"]:
        raise SystemExit("Product-search relevance benchmark failed.")


if __name__ == "__main__":
    main()
