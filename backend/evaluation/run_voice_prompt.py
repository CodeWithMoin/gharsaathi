import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.voice.prompts import VOICE_PROMPT_VERSION, VOICE_SYSTEM_PROMPT


SUITE_PATH = Path(__file__).with_name("voice_prompt_cases.json")
# The live model delegates through the session, which a text-only call cannot
# do. Without this note it treats every request as "no tools here" and never
# reaches the reply it would actually speak.
SIMULATION_NOTE = """

# Evaluation note
This is a text simulation of the voice session. Delegation to the backend is
available: when you delegate, it runs right after your reply. Reply with only
what you would say aloud at this moment.
"""
_SENTENCE_END = re.compile(r"[.!?\u0964\uff1f]+(?:\s|$)")
_DEVANAGARI_LETTER = re.compile(r"[\u0900-\u097f]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(re.escape(pattern), text, re.IGNORECASE) for pattern in patterns)


def _question_count(text: str) -> int:
    return text.count("?") + text.count("\uff1f")


def _sentence_count(text: str) -> int:
    pieces = [piece for piece in _SENTENCE_END.split(text.strip()) if piece.strip()]
    return len(pieces)


def devanagari_ratio(text: str) -> float:
    """Share of script letters that are Devanagari (0.0 for Roman-only text)."""
    devanagari = len(_DEVANAGARI_LETTER.findall(text))
    latin = len(_LATIN_LETTER.findall(text))
    total = devanagari + latin
    return devanagari / total if total else 0.0


def _word_matches(text: str, words: list[str]) -> list[str]:
    return [
        word
        for word in words
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE)
    ]


def _starts_with_any(text: str, prefixes: list[str]) -> list[str]:
    opening = text.lstrip(" \t\n\"'\u201c\u2018").casefold()
    return [prefix for prefix in prefixes if opening.startswith(prefix.casefold())]


def build_input(case: dict[str, Any]) -> str | list[dict[str, str]]:
    """Return the user turn, preceded by any scripted conversation history."""
    history = case.get("history") or []
    if not history:
        return case["user"]
    return [*history, {"role": "user", "content": case["user"]}]


def score_case(case: dict[str, Any], output: str) -> dict[str, Any]:
    required_ok = _matches_any(output, case["required_any"])
    forbidden = [item for item in case["forbidden_any"] if _matches_any(output, [item])]
    forbidden_prefix = _starts_with_any(output, case.get("forbidden_prefix", []))
    questions = _question_count(output)
    sentences = _sentence_count(output)
    max_sentences = case.get("max_sentences")
    sentences_ok = max_sentences is None or sentences <= max_sentences
    forbidden_words = _word_matches(output, case.get("forbidden_words", []))
    fillers = sum(
        len(re.findall(re.escape(filler), output, re.IGNORECASE)) for filler in case.get("fillers", [])
    )
    fillers_ok = fillers <= case.get("max_fillers", fillers)
    words = len(output.split())
    words_ok = words <= case.get("max_words", words)
    ratio = devanagari_ratio(output)
    script_ok = ratio <= case.get("max_devanagari_ratio", 1.0) and ratio >= case.get(
        "min_devanagari_ratio", 0.0
    )
    return {
        "id": case["id"],
        "passed": required_ok
        and not forbidden
        and not forbidden_prefix
        and questions <= case["max_questions"]
        and sentences_ok
        and not forbidden_words
        and fillers_ok
        and words_ok
        and script_ok,
        "output": output,
        "required_match": required_ok,
        "forbidden_matches": forbidden,
        "forbidden_prefix_matches": forbidden_prefix,
        "question_count": questions,
        "sentence_count": sentences,
        "forbidden_word_matches": forbidden_words,
        "filler_count": fillers,
        "word_count": words,
        "devanagari_ratio": round(ratio, 3),
    }


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    suite: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if suite["version"] != VOICE_PROMPT_VERSION:
        raise SystemExit("Evaluation suite and voice prompt versions do not match.")
    return suite


async def main(case_ids: list[str] | None = None, model: str | None = None) -> None:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
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

    suite = load_suite()
    selected = set(case_ids or [])
    cases = [case for case in suite["cases"] if not selected or case["id"] in selected]
    if selected - {case["id"] for case in cases}:
        raise SystemExit(f"Unknown case ids: {sorted(selected - {c['id'] for c in cases})}")

    model = model or settings.live_backend_model
    client = AsyncOpenAI(api_key=api_key)
    results: list[dict[str, Any]] = []
    for case in cases:
        response = await client.responses.create(
            model=model,
            instructions=VOICE_SYSTEM_PROMPT + SIMULATION_NOTE,
            input=build_input(case),  # type: ignore[arg-type]
            max_output_tokens=1000,
            store=False,
        )
        results.append(score_case(case, response.output_text.strip()))

    passed = sum(1 for result in results if result["passed"])
    report = {
        "prompt_version": VOICE_PROMPT_VERSION,
        "model": model,
        "passed": passed,
        "total": len(results),
        "results": results,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if passed != len(results):
        raise SystemExit("Voice prompt evaluation did not pass every case.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", dest="cases", help="Run only this case id.")
    parser.add_argument("--model", help="Responses model; defaults to LIVE_BACKEND_MODEL.")
    args = parser.parse_args()
    asyncio.run(main(args.cases, args.model))
