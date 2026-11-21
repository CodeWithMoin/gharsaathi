import argparse
import itertools
import json
import math
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).with_name("voice_catalogue_cases.json")
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "frontend/public/voice-qa"
DEFAULT_REPORT_PATH = Path(__file__).with_name("voice_diversity_report.json")
TOKEN_PATTERN = re.compile(r"[\w\u0900-\u097f]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.casefold())


def _distinct_n(token_lists: list[list[str]], size: int) -> float:
    grams = [
        tuple(tokens[index : index + size])
        for tokens in token_lists
        for index in range(len(tokens) - size + 1)
    ]
    return len(set(grams)) / len(grams) if grams else 0.0


def _jaccard(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 1.0


def _text_metrics(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    utterances = [variant for case in cases for variant in case["variants"]]
    token_lists = [_tokens(utterance) for utterance in utterances]
    openers = [tuple(tokens[:4]) for tokens in token_lists]
    similarities = [
        _jaccard(left, right)
        for case in cases
        for left, right in itertools.combinations(case["variants"], 2)
    ]
    return {
        "case_count": len(cases),
        "utterance_count": len(utterances),
        "unique_response_ratio": round(len(set(utterances)) / len(utterances), 4),
        "unique_opener_ratio": round(len(set(openers)) / len(openers), 4),
        "distinct_1": round(_distinct_n(token_lists, 1), 4),
        "distinct_2": round(_distinct_n(token_lists, 2), 4),
        "mean_within_case_jaccard": round(
            sum(similarities) / len(similarities), 4
        ) if similarities else 0.0,
        "max_within_case_jaccard": round(max(similarities), 4)
        if similarities else 0.0,
    }


def _validate_fixtures(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        errors.append("Case IDs must be unique.")
    for case in cases:
        if len(case["variants"]) < 3:
            errors.append(f"{case['id']}: at least three variants are required.")
        for index, variant in enumerate(case["variants"], start=1):
            if not any(target.casefold() in variant.casefold() for target in case["pronunciation_targets"]):
                errors.append(
                    f"{case['id']} variant {index}: missing pronunciation target."
                )
            if variant.count("?") > 1:
                errors.append(f"{case['id']} variant {index}: too many questions.")
    return errors


def _probe_audio(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return {
        "duration_seconds": round(float(data["format"]["duration"]), 3),
        "codec": stream["codec_name"],
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "bytes": path.stat().st_size,
    }


def _generate_audio(
    cases: list[dict[str, Any]], output_dir: Path, voice: str, rate: int
) -> list[dict[str, Any]]:
    if not shutil.which("say") or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("Audio generation requires say, ffmpeg, and ffprobe.")
    output_dir.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="gharsaathi-voice-") as temp_dir:
        for case in cases:
            for index, text in enumerate(case["variants"], start=1):
                stem = f"{case['id']}-{index}"
                aiff_path = Path(temp_dir) / f"{stem}.aiff"
                mp3_path = output_dir / f"{stem}.mp3"
                subprocess.run(
                    [
                        "say",
                        "-v",
                        voice,
                        "-r",
                        str(rate),
                        "-o",
                        str(aiff_path),
                        text,
                    ],
                    check=True,
                )
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(aiff_path),
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        "-b:a",
                        "64k",
                        str(mp3_path),
                    ],
                    check=True,
                )
                metadata = _probe_audio(mp3_path)
                assets.append(
                    {
                        "case_id": case["id"],
                        "variant": index,
                        "path": f"/voice-qa/{mp3_path.name}",
                        **metadata,
                    }
                )
    return assets


def _audio_errors(assets: list[dict[str, Any]], expected_count: int) -> list[str]:
    errors: list[str] = []
    if len(assets) != expected_count:
        errors.append(f"Expected {expected_count} audio files, found {len(assets)}.")
    for asset in assets:
        label = f"{asset['case_id']} variant {asset['variant']}"
        if not 1.0 <= asset["duration_seconds"] <= 15.0:
            errors.append(f"{label}: duration is outside 1–15 seconds.")
        if asset["codec"] != "mp3" or asset["sample_rate"] != 24000 or asset["channels"] != 1:
            errors.append(f"{label}: expected mono 24 kHz MP3.")
        if asset["bytes"] < 1_000:
            errors.append(f"{label}: audio file is unexpectedly small.")
    return errors


def _load_existing_audio(output_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return [], None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        for variant in case["variants"]:
            path = output_dir / Path(variant["audio"]).name
            assets.append(
                {
                    "case_id": case["id"],
                    "variant": variant["id"],
                    "path": variant["audio"],
                    **_probe_audio(path),
                }
            )
    return assets, manifest.get("voice")


def _threshold_errors(
    metrics: dict[str, float | int], *, minimum_distinct_2: float = 0.50
) -> list[str]:
    thresholds = {
        "unique_response_ratio": (0.95, math.inf),
        "unique_opener_ratio": (0.65, math.inf),
        "distinct_2": (minimum_distinct_2, math.inf),
        "mean_within_case_jaccard": (0.0, 0.72),
        "max_within_case_jaccard": (0.0, 0.85),
    }
    return [
        f"{name}={metrics[name]} is outside [{minimum}, {maximum}]."
        for name, (minimum, maximum) in thresholds.items()
        if not minimum <= float(metrics[name]) <= maximum
    ]


def _manifest(
    suite: dict[str, Any], assets: list[dict[str, Any]], metrics: dict[str, Any]
) -> dict[str, Any]:
    asset_index = {
        (asset["case_id"], asset["variant"]): asset for asset in assets
    }
    return {
        "version": suite["version"],
        "voice": "Lekha (macOS hi-IN mock TTS)",
        "disclaimer": (
            "These files validate mock catalogue phrasing and app playback. "
            "They do not prove OpenAI Realtime pronunciation."
        ),
        "metrics": metrics,
        "cases": [
            {
                "id": case["id"],
                "provider": case["provider"],
                "title": case["title"],
                "variants": [
                    {
                        "id": index,
                        "text": text,
                        "audio": asset_index[(case["id"], index)]["path"],
                        "duration_seconds": asset_index[(case["id"], index)][
                            "duration_seconds"
                        ],
                    }
                    for index, text in enumerate(case["variants"], start=1)
                ],
            }
            for case in suite["cases"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate-audio", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--voice", default="Lekha")
    parser.add_argument("--rate", type=int, default=175)
    args = parser.parse_args()

    suite: dict[str, Any] = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = suite["cases"]
    fixture_errors = _validate_fixtures(cases)
    metrics = _text_metrics(cases)
    threshold_errors = _threshold_errors(metrics)
    assets: list[dict[str, Any]] = []
    audio_voice: str | None = None
    audio_errors: list[str] = []

    if args.generate_audio:
        assets = _generate_audio(cases, args.output_dir, args.voice, args.rate)
        audio_voice = args.voice
        audio_errors = _audio_errors(
            assets, sum(len(case["variants"]) for case in cases)
        )
        manifest = _manifest(suite, assets, metrics)
        (args.output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        assets, audio_voice = _load_existing_audio(args.output_dir)
        if assets:
            audio_errors = _audio_errors(
                assets, sum(len(case["variants"]) for case in cases)
            )

    report = {
        "suite_version": suite["version"],
        "source": "curated mock catalogue fixtures",
        "metrics": metrics,
        "audio": {
            "generated": bool(assets),
            "voice": audio_voice,
            "file_count": len(assets),
            "total_duration_seconds": round(
                sum(asset["duration_seconds"] for asset in assets), 3
            ),
        },
        "errors": fixture_errors + threshold_errors + audio_errors,
    }
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["errors"]:
        raise SystemExit("Voice diversity evaluation failed.")


if __name__ == "__main__":
    main()
