import pytest

from app.voice.prompts import (
    BACKEND_INSTRUCTIONS,
    LIVE_INSTRUCTIONS,
    VOICE_PROMPT_VERSION,
)
from evaluation.run_voice_prompt import build_input, load_suite, score_case

REGRESSION_CASE_IDS = {
    "pick_variant_single_word",
    "impatience_one_line",
    "smallest_size_no_reask",
    "no_haan_before_refusal",
    "repeat_back_no_filler_no_echo",
}


def _cases() -> dict[str, dict]:
    return {case["id"]: case for case in load_suite()["cases"]}


def test_suite_version_matches_prompt_and_contains_regressions() -> None:
    suite = load_suite()
    cases = _cases()

    assert suite["version"] == VOICE_PROMPT_VERSION
    assert len(cases) == len(suite["cases"])
    assert REGRESSION_CASE_IDS <= set(cases)
    for case in cases.values():
        assert case["user"].strip()
        assert case["required_any"]
        assert isinstance(case["forbidden_any"], list)
        assert 0 <= case["max_questions"] <= 1
        for turn in case.get("history", []):
            assert turn["role"] in {"user", "assistant"}
            assert turn["content"].strip()


def test_build_input_appends_latest_turn_after_history() -> None:
    case = _cases()["pick_variant_single_word"]
    built = build_input(case)

    assert isinstance(built, list)
    assert built[:-1] == case["history"]
    assert built[-1] == {"role": "user", "content": "Dark"}
    assert build_input(_cases()["no_otp_request"]) == _cases()["no_otp_request"]["user"]


@pytest.mark.parametrize(
    ("case_id", "good", "bad"),
    [
        (
            "pick_variant_single_word",
            "Theek hai, AXE Dark Temptation 150 ml Instamart basket mein add kar rahi hoon.",
            "AXE Dark Temptation, Apollo aur Signature hain. Kaunsa chahiye?",
        ),
        (
            "pick_variant_single_word",
            "AXE Dark Temptation 150 ml basket mein daal rahi hoon.",
            "AXE Dark Temptation 150 ml add ho gaya.",
        ),
        (
            "impatience_one_line",
            "Theek hai, AXE Dark Temptation 150 ml abhi add kar rahi hoon.",
            "Samajh gayi. AXE Dark Temptation hai. Apollo bhi hai. Signature bhi hai.",
        ),
        (
            "smallest_size_no_reask",
            "Sabse chhota AXE Dark Temptation 150 ml hai, do sau ninyanve rupaye; delivery time abhi result mein nahi dikha.",
            "150 ml aur 215 ml hain, kaunsa size chahiye?",
        ),
        (
            "no_haan_before_refusal",
            "Nahi, order main place nahi kar sakti; final review screen par hota hai.",
            "Haan, lekin order main place nahi kar sakti.",
        ),
        (
            "repeat_back_no_filler_no_echo",
            "Maine suna: Amul Taaza Toned Milk, 1 litre, ek pack.",
            "Ek second, dekh rahi hoon. Mujhe 'Amul Taaza Toned Milk, 1 L' chahiye.",
        ),
    ],
)
def test_regression_cases_score_good_and_bad_replies(
    case_id: str, good: str, bad: str
) -> None:
    case = _cases()[case_id]

    assert score_case(case, good)["passed"] is True
    assert score_case(case, bad)["passed"] is False


def test_haan_prefix_check_ignores_leading_quotes_and_case() -> None:
    result = score_case(_cases()["no_haan_before_refusal"], '"HAAN, order nahi ho sakta."')

    assert result["forbidden_prefix_matches"] == ["haan"]
    assert result["passed"] is False


def test_sentence_count_handles_hindi_danda() -> None:
    case = {**_cases()["impatience_one_line"], "max_sentences": 1}

    assert score_case(case, "AXE Dark add kar rahi hoon।")["sentence_count"] == 1
    assert score_case(case, "AXE Dark hai। Add kar rahi hoon।")["passed"] is False


def test_live_prompt_states_decisive_reply_rules() -> None:
    assert '"Dark" after AXE options' in LIVE_INSTRUCTIONS
    assert "Ask at most one question per turn" in LIVE_INSTRUCTIONS
    assert '"Sabse chhota" or "smallest"' in LIVE_INSTRUCTIONS
    assert "do not read the list again" in LIVE_INSTRUCTIONS
    assert "reply in one sentence and act" in LIVE_INSTRUCTIONS
    assert 'Never start with "haan"' in LIVE_INSTRUCTIONS
    assert '"Maine suna:' in LIVE_INSTRUCTIONS
    assert "Only when a search or basket call is actually about to happen" in LIVE_INSTRUCTIONS


def test_decisive_rules_keep_safety_rules() -> None:
    assert "Only after the basket tool confirms the add" in LIVE_INSTRUCTIONS
    assert "Never say that an item was added unless the basket tool confirmed it" in LIVE_INSTRUCTIONS
    assert "A spoken \"yes\" is not approval." in LIVE_INSTRUCTIONS
    assert "Only state prices, stock and delivery times" in LIVE_INSTRUCTIONS
    assert "## Decisive selection" in BACKEND_INSTRUCTIONS
    assert "that store's basket" in BACKEND_INSTRUCTIONS
    assert "Never invent option ids" in BACKEND_INSTRUCTIONS
