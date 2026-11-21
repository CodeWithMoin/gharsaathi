import asyncio
import json
from pathlib import Path

from app.core.config import get_settings
from app.memory.extraction import create_memory_extractor


async def main() -> None:
    cases_path = Path(__file__).with_name("memory_extraction_cases.json")
    suite = json.loads(cases_path.read_text(encoding="utf-8"))
    settings = get_settings()
    extractor = create_memory_extractor(
        settings.openai_api_key,
        settings.memory_extraction_model,
    )
    passed = 0
    failures: list[dict[str, object]] = []
    for case in suite["cases"]:
        result = await extractor.extract(case["message"], "en-IN")
        actual = sorted(item.memory_type for item in result.candidates)
        expected = sorted(case["expected_types"])
        if actual == expected:
            passed += 1
        else:
            failures.append(
                {"message": case["message"], "expected": expected, "actual": actual}
            )

    total = len(suite["cases"])
    accuracy = passed / total if total else 0.0
    print(
        json.dumps(
            {
                "prompt_version": suite["version"],
                "extractor": extractor.name,
                "passed": passed,
                "total": total,
                "exact_match_accuracy": accuracy,
                "failures": failures,
            },
            indent=2,
        )
    )
    if accuracy < 0.8:
        raise SystemExit("Extraction accuracy is below the 0.80 minimum.")


if __name__ == "__main__":
    asyncio.run(main())
