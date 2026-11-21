import argparse
import asyncio
import base64
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.voice.prompts import VOICE_PROMPT_VERSION, VOICE_SYSTEM_PROMPT
from evaluation.run_voice_diversity import (
    _audio_errors,
    _probe_audio,
    _text_metrics,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).with_name("voice_catalogue_cases.json")
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "frontend/public/voice-qa-openai"
DEFAULT_REPORT_PATH = Path(__file__).with_name("realtime_voice_report.json")
MASCULINE_SELF_FORMS = (
    "karta hoon",
    "karunga",
    "dekh raha hoon",
    "dhoondh raha hoon",
    "laya hoon",
    "samajh gaya",
    "करता हूँ",
    "करूँगा",
    "देख रहा हूँ",
    "ढूँढ रहा हूँ",
    "लाया हूँ",
    "समझ गया",
)
FEMININE_SELF_FORMS = (
    "karti hoon",
    "kar rahi hoon",
    "karungi",
    "dhoondh leti hoon",
    "dhoondh rahi hoon",
    "dhoondhungi",
    "dekh rahi hoon",
    "dekhungi",
    "note kar leti hoon",
    "note kar rahi hoon",
    "samajh gayi",
    "करती हूँ",
    "कर रही हूँ",
    "करूँगी",
    "ढूँढ लेती हूँ",
    "ढूँढ रही हूँ",
    "देख रही हूँ",
    "समझ गई",
)
VARIANT_PROMPTS = (
    (
        "Mujhe '{title}' chahiye. Tumne kya suna? Ek short Hinglish line mein "
        "product aur quantity repeat karo."
    ),
    (
        "Meri shopping request '{title}' hai. Sahi samjhi? Product aur quantity "
        "ek short Hinglish line mein repeat karo."
    ),
    (
        "'{title}' abhi ke liye note karogi? Product repeat karke ek short "
        "feminine Hinglish confirmation do."
    ),
)


def _contains(text: str, value: str) -> bool:
    return re.search(re.escape(value), text, re.IGNORECASE) is not None


def _language_checks(text: str, targets: list[str]) -> dict[str, Any]:
    masculine = [form for form in MASCULINE_SELF_FORMS if _contains(text, form)]
    feminine = [form for form in FEMININE_SELF_FORMS if _contains(text, form)]
    return {
        "masculine_self_forms": masculine,
        "feminine_self_forms": feminine,
        "pronunciation_target_in_transcript": any(
            _contains(text, target) for target in targets
        ),
        "question_count": text.count("?") + text.count("？"),
    }


def _realtime_threshold_errors(metrics: dict[str, float | int]) -> list[str]:
    """Moderate diversity gates for intentionally short spoken confirmations."""
    minimums = {
        "unique_response_ratio": 0.75,
        "unique_opener_ratio": 0.45,
        "distinct_2": 0.35,
    }
    errors = [
        f"{name}={metrics[name]} is below {minimum}."
        for name, minimum in minimums.items()
        if float(metrics[name]) < minimum
    ]
    if float(metrics["mean_within_case_jaccard"]) > 0.80:
        errors.append(
            "mean_within_case_jaccard="
            f"{metrics['mean_within_case_jaccard']} is above 0.8."
        )
    return errors


def _pcm_to_mp3(pcm: bytes, output_path: Path) -> dict[str, Any]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Realtime audio QA requires ffmpeg and ffprobe.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".pcm") as source:
        source.write(pcm)
        source.flush()
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                "-i",
                source.name,
                "-b:a",
                "64k",
                str(output_path),
            ],
            check=True,
        )
    return _probe_audio(output_path)


async def _generate_turn(
    client: AsyncOpenAI,
    *,
    model: str,
    voice: str,
    user_text: str,
) -> tuple[str, bytes]:
    transcript = ""
    audio = bytearray()
    async with client.realtime.connect(model=model) as connection:
        await connection.session.update(
            session={
                "type": "realtime",
                "instructions": VOICE_SYSTEM_PROMPT,
                "output_modalities": ["audio"],
                "max_output_tokens": 2048,
                "audio": {
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": voice,
                        "speed": 1.0,
                    }
                },
            }
        )
        await connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            }
        )
        await connection.response.create(response={"output_modalities": ["audio"]})

        async for event in connection:
            if event.type == "response.output_audio.delta":
                audio.extend(base64.b64decode(event.delta))
            elif event.type == "response.output_audio_transcript.done":
                transcript = event.transcript.strip()
            elif event.type == "error":
                raise RuntimeError(f"Realtime API error: {event.error.message}")
            elif event.type == "response.done":
                if event.response.status != "completed":
                    details = getattr(event.response, "status_details", None)
                    raise RuntimeError(
                        "Realtime response ended with "
                        f"{event.response.status}: {details}."
                    )
                break

    if not transcript:
        raise RuntimeError("Realtime response contained no output transcript.")
    if not audio:
        raise RuntimeError("Realtime response contained no audio.")
    return transcript, bytes(audio)


async def _generate_turn_with_retry(
    client: AsyncOpenAI,
    *,
    model: str,
    voice: str,
    user_text: str,
    timeout_seconds: float,
    attempts: int,
) -> tuple[str, bytes]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with asyncio.timeout(timeout_seconds):
                return await _generate_turn(
                    client,
                    model=model,
                    voice=voice,
                    user_text=user_text,
                )
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(0.5 * attempt)
    raise RuntimeError(
        f"Realtime turn failed after {attempts} attempts: {last_error}"
    ) from last_error


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    suite: dict[str, Any] = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = suite["cases"][: args.case_limit or None]
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    generated_cases: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    errors: list[str] = []

    for case in cases:
        variants: list[str] = []
        rendered_variants: list[dict[str, Any]] = []
        for variant in range(1, args.variants + 1):
            template = VARIANT_PROMPTS[(variant - 1) % len(VARIANT_PROMPTS)]
            user_text = template.format(title=case["title"])
            transcript, pcm = await _generate_turn_with_retry(
                client,
                model=settings.voice_realtime_model,
                voice=args.voice,
                user_text=user_text,
                timeout_seconds=args.timeout_seconds,
                attempts=args.attempts,
            )
            filename = f"{case['id']}-{variant}.mp3"
            metadata = _pcm_to_mp3(pcm, args.output_dir / filename)
            checks = _language_checks(transcript, case["pronunciation_targets"])
            if checks["masculine_self_forms"]:
                errors.append(
                    f"{case['id']} variant {variant}: masculine self-reference "
                    f"{checks['masculine_self_forms']}."
                )
            if not checks["pronunciation_target_in_transcript"]:
                errors.append(
                    f"{case['id']} variant {variant}: product pronunciation "
                    "target is missing from the transcript."
                )
            if checks["question_count"] > 1:
                errors.append(
                    f"{case['id']} variant {variant}: more than one question."
                )
            variants.append(transcript)
            asset = {
                "case_id": case["id"],
                "variant": variant,
                "path": f"/voice-qa-openai/{filename}",
                **metadata,
            }
            assets.append(asset)
            rendered_variants.append(
                {
                    "id": variant,
                    "text": transcript,
                    "audio": asset["path"],
                    "duration_seconds": metadata["duration_seconds"],
                    "checks": checks,
                }
            )
        generated_cases.append(
            {
                "id": case["id"],
                "provider": case["provider"],
                "title": case["title"],
                "pronunciation_targets": case["pronunciation_targets"],
                "variants": variants,
                "rendered_variants": rendered_variants,
            }
        )

    metrics = _text_metrics(generated_cases)
    expected_audio = len(cases) * args.variants
    errors.extend(_audio_errors(assets, expected_audio))
    if args.variants >= 3 and len(cases) >= 4:
        errors.extend(_realtime_threshold_errors(metrics))

    manifest = {
        "version": VOICE_PROMPT_VERSION,
        "model": settings.voice_realtime_model,
        "voice": args.voice,
        "disclaimer": (
            "Generated by the actual OpenAI Realtime model and production voice "
            "prompt. Transcript checks catch grammar and target-word regressions; "
            "listen to each clip for phonetic pronunciation."
        ),
        "metrics": metrics,
        "cases": [
            {
                **{
                    key: value
                    for key, value in case.items()
                    if key not in {"variants", "rendered_variants"}
                },
                "variants": case["rendered_variants"],
            }
            for case in generated_cases
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report = {
        "prompt_version": VOICE_PROMPT_VERSION,
        "model": settings.voice_realtime_model,
        "voice": args.voice,
        "metrics": metrics,
        "audio": {
            "file_count": len(assets),
            "total_duration_seconds": round(
                sum(asset["duration_seconds"] for asset in assets), 3
            ),
        },
        "errors": errors,
    }
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--variants", type=int, default=3)
    parser.add_argument("--case-limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--attempts", type=int, default=2)
    args = parser.parse_args()
    if (
        args.variants < 1
        or args.case_limit < 0
        or args.timeout_seconds <= 0
        or args.attempts < 1
    ):
        parser.error(
            "variants, timeout-seconds, and attempts must be positive; "
            "case-limit cannot be negative"
        )
    report = asyncio.run(_run(args))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["errors"]:
        raise SystemExit("Realtime voice evaluation failed.")


if __name__ == "__main__":
    main()
