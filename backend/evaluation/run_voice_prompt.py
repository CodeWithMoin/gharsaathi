import asyncio
import json
import re
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.voice.prompts import VOICE_PROMPT_VERSION, VOICE_SYSTEM_PROMPT


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(re.escape(pattern), text, re.IGNORECASE) for pattern in patterns)


async def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key.strip():
        print(
            json.dumps(
                {
                    "prompt_version": VOICE_PROMPT_VERSION,
                    "status": "skipped",
                    "reason": "OPENAI_API_KEY is not configured",
                },
                indent=2,
            )
        )
        return

    suite_path = Path(__file__).with_name("voice_prompt_cases.json")
    suite: dict[str, Any] = json.loads(suite_path.read_text(encoding="utf-8"))
    if suite["version"] != VOICE_PROMPT_VERSION:
        raise SystemExit("Evaluation suite and voice prompt versions do not match.")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    results: list[dict[str, Any]] = []
    for case in suite["cases"]:
        response = await client.responses.create(
            model=settings.voice_evaluation_model,
            instructions=VOICE_SYSTEM_PROMPT,
            input=case["user"],
            max_output_tokens=300,
            store=False,
        )
        output = response.output_text.strip()
        required_ok = _matches_any(output, case["required_any"])
        forbidden = [
            item for item in case["forbidden_any"] if _matches_any(output, [item])
        ]
        questions_ok = output.count("?") <= case["max_questions"]
        results.append(
            {
                "id": case["id"],
                "passed": required_ok and not forbidden and questions_ok,
                "output": output,
                "required_match": required_ok,
                "forbidden_matches": forbidden,
                "question_count": output.count("?"),
            }
        )

    passed = sum(1 for result in results if result["passed"])
    report = {
        "prompt_version": VOICE_PROMPT_VERSION,
        "model": settings.voice_evaluation_model,
        "passed": passed,
        "total": len(results),
        "results": results,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if passed != len(results):
        raise SystemExit("Voice prompt evaluation did not pass every case.")


if __name__ == "__main__":
    asyncio.run(main())
