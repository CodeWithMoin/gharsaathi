"""Audit official Swiggy Instamart examples against GharSaathi's safe tool surface.

This is intentionally provider-free. It verifies that the tool contracts needed
for each workflow exist, and records product gaps without pretending that a live
catalogue or a model response was tested.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.voice.basket_tools import BASKET_TOOL_DEFINITIONS
from app.voice.prompts import BACKEND_INSTRUCTIONS
from app.voice.tools import SEARCH_PRODUCTS_TOOL


CASES_PATH = Path(__file__).with_name("instamart_workflow_cases.json")


def detected_capabilities() -> set[str]:
    tool_names = {
        tool["name"] for tool in [SEARCH_PRODUCTS_TOOL, *BASKET_TOOL_DEFINITIONS]
    }
    capabilities: set[str] = set()
    if "search_products" in tool_names:
        capabilities.update({"provider_price_fields", "product_search"})
    if "search_products" in tool_names and "For several items, search each one separately" in BACKEND_INSTRUCTIONS:
        capabilities.add("multi_item_search")
    if "add_to_basket" in tool_names:
        capabilities.add("basket_add")
    if "view_basket" in tool_names:
        capabilities.update({"basket_review", "deterministic_basket_total"})
    return capabilities


def evaluate(cases_path: Path = CASES_PATH) -> dict[str, Any]:
    suite = json.loads(cases_path.read_text(encoding="utf-8"))
    available = detected_capabilities()
    outcomes = []
    for case in suite["cases"]:
        missing = sorted(set(case["required_capabilities"]) - available)
        actual = (
            "unsupported"
            if missing
            else "partial"
            if case["known_gaps"]
            else "supported_with_confirmation"
        )
        outcomes.append(
            {
                "id": case["id"],
                "title": case["title"],
                "example": case["example"],
                "readiness": actual,
                "expected_readiness": case["expected_readiness"],
                "missing_capabilities": missing,
                "known_gaps": case["known_gaps"],
                "passed": actual == case["expected_readiness"],
            }
        )
    return {
        "suite": suite["version"],
        "source": suite["source"],
        "provider_free": True,
        "available_capabilities": sorted(available),
        "passed": sum(item["passed"] for item in outcomes),
        "total": len(outcomes),
        "cases": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = evaluate(args.cases)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    if report["passed"] != report["total"]:
        raise SystemExit("Instamart workflow audit failed.")


if __name__ == "__main__":
    main()
