import json
from pathlib import Path

from evaluation.run_voice_diversity import (
    _text_metrics,
    _threshold_errors,
    _validate_fixtures,
)


def test_text_metrics_support_single_smoke_variant() -> None:
    metrics = _text_metrics([{"variants": ["आटा dekh rahi hoon."]}])

    assert metrics["utterance_count"] == 1
    assert metrics["mean_within_case_jaccard"] == 0.0
    assert metrics["max_within_case_jaccard"] == 0.0


CASES_PATH = Path(__file__).parents[1] / "evaluation/voice_catalogue_cases.json"
REPOSITORY_ROOT = Path(__file__).parents[2]


def _cases() -> list[dict[str, object]]:
    suite = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return suite["cases"]


def test_mock_catalogue_covers_both_providers_and_pronunciation_classes() -> None:
    cases = _cases()
    providers = {case["provider"] for case in cases}
    titles = " ".join(str(case["title"]) for case in cases).casefold()

    assert providers == {"Swiggy Instamart", "Zepto"}
    assert len(cases) == 12
    for term in ("atta", "toor dal", "kachi ghani", "ghee", "jeera", "haldi"):
        assert term in titles
    assert "surf excel matic" in titles
    assert "harpic power plus" in titles


def test_mock_voice_fixtures_are_complete_and_diverse() -> None:
    cases = _cases()
    metrics = _text_metrics(cases)

    assert _validate_fixtures(cases) == []
    assert _threshold_errors(metrics) == []
    assert metrics["utterance_count"] == 36
    assert metrics["unique_response_ratio"] == 1.0
    assert float(metrics["unique_opener_ratio"]) >= 0.65
    assert float(metrics["distinct_2"]) >= 0.50
    assert float(metrics["mean_within_case_jaccard"]) <= 0.72


def test_short_realtime_copy_can_use_length_adjusted_bigram_gate() -> None:
    metrics = {
        "unique_response_ratio": 1.0,
        "unique_opener_ratio": 0.75,
        "distinct_2": 0.43,
        "mean_within_case_jaccard": 0.60,
        "max_within_case_jaccard": 0.80,
    }

    assert _threshold_errors(metrics, minimum_distinct_2=0.40) == []


def test_generated_voice_qa_manifest_has_playable_audio_assets() -> None:
    asset_dir = REPOSITORY_ROOT / "frontend/public/voice-qa"
    manifest = json.loads((asset_dir / "manifest.json").read_text(encoding="utf-8"))
    variants = [
        variant for case in manifest["cases"] for variant in case["variants"]
    ]

    assert manifest["voice"] == "Lekha (macOS hi-IN mock TTS)"
    assert len(variants) == 36
    assert all(1.0 <= variant["duration_seconds"] <= 15.0 for variant in variants)
    for variant in variants:
        audio_path = REPOSITORY_ROOT / "frontend/public" / variant["audio"].lstrip("/")
        assert audio_path.exists()
        assert audio_path.stat().st_size >= 1_000
