import pytest

from app.voice.prompts import (
    BACKEND_INSTRUCTIONS,
    LIVE_INSTRUCTIONS,
    VOICE_PROMPT_VERSION,
)
from evaluation.run_voice_prompt import (
    build_input,
    devanagari_ratio,
    load_suite,
    score_case,
)

REGRESSION_CASE_IDS = {
    "pick_variant_single_word",
    "impatience_one_line",
    "smallest_size_no_reask",
    "no_haan_before_refusal",
    "repeat_back_no_filler_no_echo",
    "devanagari_transcript_roman_hinglish_price",
    "devanagari_transcript_roman_hinglish_capability",
    "devanagari_transcript_roman_hinglish_off_topic",
    "full_english_reply_in_english",
    "explicit_hindi_request_reply_in_hindi",
    "explicit_hindi_choice_persists",
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
        (
            "devanagari_transcript_roman_hinglish_price",
            "AXE Dark Temptation 150 ml sabse sasta hai, ek sau bahattar rupaye, Zepto par.",
            "सबसे सस्ता AXE Dark Temptation 150 ml है—एक सौ बहत्तर रुपए, Zepto पर।",
        ),
        (
            "devanagari_transcript_roman_hinglish_off_topic",
            "Phone updates mein main madad nahi kar sakti; shopping mein kuch chahiye toh bataiye.",
            "ठीक है, किस app ya device का update दिख रहा है? मैं phone की screen नहीं देख सकती।",
        ),
        (
            "devanagari_transcript_roman_hinglish_capability",
            "Main products dhoondh sakti hoon, prices compare karungi aur basket plan karne mein madad karti hoon.",
            "ठीक है, मैं products ढूंढ सकती हूँ और basket में मदद करूँगी।",
        ),
        (
            "full_english_reply_in_english",
            "I can find products across your connected stores, compare prices and help you plan a basket.",
            "Main products dhoondh sakti hoon aur basket mein madad karti hoon.",
        ),
        (
            "explicit_hindi_request_reply_in_hindi",
            "मैं सामान ढूँढने, दाम मिलाने और basket बनाने में मदद कर सकती हूँ।",
            "Main saamaan dhoondhne aur basket banane mein madad kar sakti hoon.",
        ),
        (
            "explicit_hindi_choice_persists",
            "मैं राशन, घर का सामान और personal care products ढूँढ सकती हूँ।",
            "Main ration aur ghar ka saamaan dhoondh sakti hoon.",
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


def test_devanagari_ratio_measures_script_share() -> None:
    assert devanagari_ratio("Theek hai, AXE Dark Temptation 150 ml add ho gaya.") == 0.0
    assert devanagari_ratio("ठीक है") == 1.0
    assert devanagari_ratio("150 ₹") == 0.0
    mixed = devanagari_ratio("हाँ, AXE के भी मिल गए हैं।")
    assert 0.6 < mixed < 1.0


def test_english_case_rejects_hinglish_words_by_whole_word() -> None:
    case = _cases()["full_english_reply_in_english"]

    assert score_case(case, "I can help you find things in Chennai.")["passed"] is True
    result = score_case(case, "I can help you, kya chahiye?")
    assert result["forbidden_word_matches"] == ["kya"]
    assert result["passed"] is False


def test_live_prompt_defaults_to_roman_hinglish() -> None:
    assert "Default to Hinglish written in Roman script" in LIVE_INSTRUCTIONS
    assert "A Devanagari transcript is not a request for Hindi" in LIVE_INSTRUCTIONS
    assert '"Hindi mein bolo"' in LIVE_INSTRUCTIONS
    assert "keep using it for the rest of the session" in " ".join(LIVE_INSTRUCTIONS.split())
    assert "ek sau bahattar rupaye" in LIVE_INSTRUCTIONS
    assert "Mirror the user" not in LIVE_INSTRUCTIONS
    assert "Roman-script Hinglish, even when the user's transcript is" in BACKEND_INSTRUCTIONS
    assert "karti hoon" in LIVE_INSTRUCTIONS


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
