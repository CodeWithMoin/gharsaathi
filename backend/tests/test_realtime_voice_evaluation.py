import asyncio
import subprocess
from pathlib import Path

import pytest

from evaluation.run_realtime_voice import (
    VARIANT_PROMPTS,
    _generate_turn_with_retry,
    _language_checks,
    _pcm_to_mp3,
    _realtime_threshold_errors,
)


def test_language_checks_reject_masculine_self_reference() -> None:
    checks = _language_checks(
        "Haan, aata search karta hoon.",
        ["आटा"],
    )

    assert checks["masculine_self_forms"] == ["karta hoon"]
    assert checks["feminine_self_forms"] == []
    assert checks["pronunciation_target_in_transcript"] is False


def test_language_checks_accept_feminine_target_phrase() -> None:
    checks = _language_checks(
        "Haan, आटा search kar rahi hoon.",
        ["आटा"],
    )

    assert checks == {
        "masculine_self_forms": [],
        "feminine_self_forms": ["kar rahi hoon"],
        "search_filler_forms": [],
        "customer_echo": False,
        "pronunciation_target_in_transcript": True,
        "question_count": 0,
    }


def test_language_checks_flag_filler_and_customer_echo_on_repeat_back() -> None:
    checks = _language_checks(
        "Ek second, dekh rahi hoon. Mujhe Amul Taaza 1 litre chahiye.",
        ["Amul"],
    )

    assert checks["search_filler_forms"] == ["ek second", "dekh rahi hoon"]
    assert checks["customer_echo"] is True


def test_language_checks_accept_assistant_voice_repeat_back() -> None:
    checks = _language_checks(
        "Maine suna: Amul Taaza Toned Milk, 1 litre, ek pack. Sahi hai?",
        ["Amul"],
    )

    assert checks["search_filler_forms"] == []
    assert checks["customer_echo"] is False
    assert checks["question_count"] == 1


def test_language_checks_flag_hindi_customer_echo() -> None:
    assert _language_checks("मुझे अमूल दूध चाहिए।", ["अमूल"])["customer_echo"] is True


def test_variant_prompts_exercise_different_response_structures() -> None:
    rendered = [prompt.format(title="Amul Pure Ghee, 1 L") for prompt in VARIANT_PROMPTS]

    assert len(rendered) == 3
    assert len(set(rendered)) == 3
    assert all("Amul Pure Ghee, 1 L" in prompt for prompt in rendered)
    assert "Tumne kya suna" in rendered[0]
    assert "Sahi samjhi" in rendered[1]
    assert "note karogi" in rendered[2]


def test_short_realtime_diversity_uses_moderate_naturalness_gates() -> None:
    metrics = {
        "unique_response_ratio": 1.0,
        "unique_opener_ratio": 0.50,
        "distinct_2": 0.37,
        "mean_within_case_jaccard": 0.54,
    }

    assert _realtime_threshold_errors(metrics) == []


@pytest.mark.asyncio
async def test_generate_turn_retries_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_generate_turn(*args: object, **kwargs: object) -> tuple[str, bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.05)
        return "आटा dekh rahi hoon", b"pcm"

    monkeypatch.setattr(
        "evaluation.run_realtime_voice._generate_turn", fake_generate_turn
    )

    result = await _generate_turn_with_retry(
        object(),
        model="test-model",
        voice="marin",
        user_text="test",
        timeout_seconds=0.01,
        attempts=2,
    )

    assert result == ("आटा dekh rahi hoon", b"pcm")
    assert calls == 2


def test_pcm_to_mp3_produces_expected_audio_format(
    tmp_path: Path,
) -> None:
    pcm_path = tmp_path / "silence.pcm"
    output_path = tmp_path / "silence.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            "1.2",
            "-f",
            "s16le",
            str(pcm_path),
        ],
        check=True,
    )

    metadata = _pcm_to_mp3(pcm_path.read_bytes(), output_path)

    assert metadata["codec"] == "mp3"
    assert metadata["sample_rate"] == 24000
    assert metadata["channels"] == 1
    assert 1.0 <= metadata["duration_seconds"] <= 1.4
