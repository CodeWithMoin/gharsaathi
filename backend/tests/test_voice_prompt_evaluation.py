import json
import re

import pytest

from app.shopping.money import format_rupees
from app.voice.address_tools import OpenAddressFormArguments
from app.voice.basket_tools import AddArguments, QuantityArguments, StoreArguments
from app.voice.memory_tools import RecallArguments
from app.voice.memory_write_tools import (
    DecisionArguments,
    ForgetArguments,
    ListArguments,
    RememberArguments,
    UpdateArguments,
)
from app.voice.prompts import (
    BACKEND_INSTRUCTIONS,
    LIVE_INSTRUCTIONS,
    VOICE_PROMPT_VERSION,
)
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS
from app.voice.tools import SearchProductsArguments
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
    "refuse_order_after_bill_on_screen",
    "brand_only_one_question",
    "two_store_bill_asks_store",
    "new_address_roman_no_saved_claim",
    "brand_reference_single_question",
    "smallest_then_only_variant_question",
    "gender_neutral_user_address",
    "price_repeated_as_exact_amount",
    "explicit_add_same_item_two_stores_picks_cheaper",
    "branded_item_delegates_without_confirm",
    "promo_answered_from_bill_offers_no_drift",
    "misheard_product_confirms_closest_grocery",
    "mixed_hinglish_numbers_and_verbs_stay_roman",
    "show_only_after_dikha_do",
    "staples_delegate_without_brand_size_question",
    "self_correction_drops_cancelled_item",
    "vague_khaana_one_question_with_examples",
    "named_request_never_bare_acknowledgement",
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
            "Sabse chhota AXE Dark Temptation 150 ml hai, ₹299; delivery time abhi result mein nahi dikha.",
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
            "AXE Dark Temptation 150 ml sabse sasta hai, ₹172, Zepto par.",
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
        (
            "refuse_order_after_bill_on_screen",
            "Order main place nahi kar sakti; bill screen par hai, wahin review kijiye.",
            "Haan, order place kar rahi hoon.",
        ),
        (
            "refuse_order_after_bill_on_screen",
            "Order main place nahi kar sakti; bill screen par review kijiye.",
            "Theek hai, lekin order main place nahi kar sakti.",
        ),
        (
            "brand_only_one_question",
            "Amul mein kya chahiye, doodh, butter ya kuch aur?",
            "Ek second, Amul ke products dekh rahi hoon.",
        ),
        (
            "two_store_bill_asks_store",
            "Kis store ka bill chahiye, Instamart ya Zepto?",
            "Dono stores ka bill bana diya, order ho gaya.",
        ),
        (
            "new_address_roman_no_saved_claim",
            "Theek hai, naya address form screen par khol rahi hoon.",
            "नया address save ho गया है।",
        ),
        (
            "pick_variant_single_word",
            "AXE Dark Temptation 150 ml Instamart basket mein daal rahi hoon.",
            "AXE Dark Temptation 150 ml, ₹172, Zepto par.",
        ),
        (
            "brand_reference_single_question",
            "AXE ke options screen par hain; aapko Dark Temptation chahiye ya Apollo?",
            "AXE Dark Temptation ya Apollo chahiye? 150 ml ya 215 ml?",
        ),
        (
            "smallest_then_only_variant_question",
            "Sabse chhota 150 ml hai; delivery time abhi result mein nahi aaya. Dark Temptation ya Apollo?",
            "Sabse chhota 150 ml hai. Delivery time nahi dikh raha. Kaunsa size chahiye? Dark ya Apollo?",
        ),
        (
            "gender_neutral_user_address",
            "Aapko spray chahiye ya roll-on?",
            "Aap spray deodorant dhoondh rahi hain ya roll-on?",
        ),
        (
            "price_repeated_as_exact_amount",
            "AXE Apollo 150 ml ₹199 ka hai, Instamart par.",
            "AXE Apollo 150 ml ek sau ninyanve rupaye ka hai.",
        ),
        (
            "explicit_add_same_item_two_stores_picks_cheaper",
            "Instamart par sasta hai, ₹549; wahin add kar rahi hoon.",
            "Surf Excel Matic 2 kg dono par hai, kaunse store ke basket mein add karun?",
        ),
        (
            "branded_item_delegates_without_confirm",
            "Ek second, dekh rahi hoon.",
            "Diet Coke chahiye kya aapko?",
        ),
        (
            "promo_answered_from_bill_offers_no_drift",
            "Zepto ne is bill mein Free Delivery aur ek discount lagaya hai; alag promo code daalne ka option abhi mere paas nahi hai.",
            "Main to bas ye soch rahi thi ki aapne jo samaan liya tha, woh aapko poora mil gaya?",
        ),
        (
            "misheard_product_confirms_closest_grocery",
            "Diet Coke?",
            "Bed ke liye mattress chahiye ya bedsheet?",
        ),
        (
            "mixed_hinglish_numbers_and_verbs_stay_roman",
            "Teen can ka pack ka price screen par hai; pichhli baar aapne single can li thi.",
            "Teen can ka pack डेढ़ sau rupaye ka hai; pichhli baar aapne single can ली थी।",
        ),
        (
            "show_only_after_dikha_do",
            "Haan, dahi ke options screen par hain.",
            "Desi Farms Malai Dahi 200 g ikattis rupaye, Amul Masti Dahi 400 g paintees rupaye; screen par hain.",
        ),
        (
            "staples_delegate_without_brand_size_question",
            "Ek second, atta aur dahi dekh rahi hoon.",
            "Atta ka kaunsa brand aur size chahiye?",
        ),
        (
            "self_correction_drops_cancelled_item",
            "Theek hai, sirf atta aur dahi dekh rahi hoon.",
            "Theek hai, atta, dahi aur doodh bhi dekh rahi hoon.",
        ),
        (
            "vague_khaana_one_question_with_examples",
            "Khane mein kya chahiye: atta, dal, ya ready-to-eat?",
            "Aapko cooked food chahiye? Ya groceries?",
        ),
        (
            "named_request_never_bare_acknowledgement",
            "Ek second, Amul dahi aur Aashirvaad atta dekh rahi hoon.",
            "Haan.",
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
    assert "use the exact amount strings from the backend results" in " ".join(LIVE_INSTRUCTIONS.split())
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


def _examples(prompt: str, heading: str) -> list[str]:
    block = prompt.split(heading, 1)[1]
    return [line.strip() for line in block.splitlines() if line.strip()]


_EXAMPLE_BLOCKS = [
    pytest.param(LIVE_INSTRUCTIONS, "\n# Examples\n", id="live"),
    pytest.param(BACKEND_INSTRUCTIONS, "\n## Examples\n", id="backend"),
]


@pytest.mark.parametrize(("prompt", "heading"), _EXAMPLE_BLOCKS)
def test_few_shot_examples_are_labelled_and_roman_except_explicit_hindi(
    prompt: str, heading: str
) -> None:
    lines = _examples(prompt, heading)
    assert "Illustrative patterns only, not facts." in " ".join(lines)
    assert "Never reuse them" in " ".join(lines)

    last_user = ""
    hindi_replies = 0
    assistant_lines = 0
    for line in lines:
        if line.startswith("User:"):
            last_user = line
        elif line.startswith("Assistant:"):
            assistant_lines += 1
            ratio = devanagari_ratio(line.removeprefix("Assistant:"))
            if "hindi mein" in last_user.casefold():
                hindi_replies += 1
                assert ratio >= 0.3, line
            else:
                assert ratio == 0.0, line
    assert 6 <= assistant_lines <= 22
    assert hindi_replies == 1


def test_live_examples_cover_decisive_patterns_without_tool_sketches() -> None:
    lines = _examples(LIVE_INSTRUCTIONS, "\n# Examples\n")
    text = "\n".join(lines)

    assert not any(line.startswith(("Tool call:", "Tool result:")) for line in lines)
    assert "User: Dark" in text
    assert "Assistant: Maine suna:" in text
    assert "Instamart ya Zepto?" in text
    # Every spoken add confirmation follows a bracketed backend confirmation.
    confirmations = [i for i, line in enumerate(lines) if "add ho gaya" in line]
    assert len(confirmations) >= 2
    for index in confirmations:
        assert lines[index - 1].startswith("[Backend"), lines[index]
    refusal = next(line for line in lines if "place nahi kar sakti" in line)
    assert not refusal.removeprefix("Assistant:").strip().casefold().startswith(("haan", "ji haan", "yes"))


def test_backend_examples_use_real_tools_with_valid_arguments() -> None:
    definitions = {tool["name"]: tool for tool in VOICE_TOOL_DEFINITIONS}
    validators = {
        "search_products": SearchProductsArguments,
        "add_to_basket": AddArguments,
        "set_basket_quantity": QuantityArguments,
        "view_basket": StoreArguments,
        "request_approval": StoreArguments,
        "open_address_form": OpenAddressFormArguments,
        "recall_household_preferences": RecallArguments,
        "remember_preference": RememberArguments,
        "update_preference": UpdateArguments,
        "forget_preference": ForgetArguments,
        "list_preferences": ListArguments,
        "confirm_memory": DecisionArguments,
    }
    assert set(validators) == set(definitions)

    issued_options: set[str] = set()
    used: set[str] = set()
    for line in _examples(BACKEND_INSTRUCTIONS, "\n## Examples\n"):
        if line.startswith("Tool call:"):
            name, raw = line.removeprefix("Tool call:").strip().split(" ", 1)
            assert name in definitions, name
            arguments = json.loads(raw)
            parameters = definitions[name]["parameters"]
            assert set(arguments) <= set(parameters["properties"])
            assert set(parameters["required"]) <= set(arguments)
            validators[name].model_validate(arguments)
            if name == "add_to_basket":
                assert arguments["option_id"] in issued_options
            used.add(name)
        elif line.startswith("Tool result:"):
            result = json.loads(line.removeprefix("Tool result:"))
            issued_options.update(
                item["option_id"] for item in result.get("results", []) if "option_id" in item
            )
    assert {"search_products", "add_to_basket", "request_approval", "open_address_form"} <= used
    lines = _examples(BACKEND_INSTRUCTIONS, "\n## Examples\n")
    for index, line in enumerate(lines):
        if line.startswith("Assistant:") and "add ho gaya" in line:
            previous = json.loads(lines[index - 1].removeprefix("Tool result:"))
            assert previous["status"] == "added", line


_AMOUNT = re.compile(r"-?₹\d+(?:,\d+)*(?:\.\d{2})?")
_WORD_PRICE = re.compile(r"\b(?:rupaye|rupaiye|rupay|rupees?|rupaya|rs)\b|रुपये|रुपए|रुपया", re.IGNORECASE)
_GENDERED_USER_ADDRESS = re.compile(
    r"\b(?:rahi|rahe|chahti|chahte|karti|karte|leti|lete|sakti|sakte) hain\b", re.IGNORECASE
)


@pytest.mark.parametrize(("prompt", "heading"), _EXAMPLE_BLOCKS)
def test_example_replies_copy_amount_strings_and_address_user_neutrally(
    prompt: str, heading: str
) -> None:
    """Every amount an example reply states appears verbatim in an earlier tool result.

    Backend examples quote ``Tool result:`` lines; live examples quote the
    bracketed backend lines. No reply spells a price in words or states an
    amount the backend did not return.
    """

    sources: list[str] = []
    stated = 0
    for line in _examples(prompt, heading):
        if line.startswith("Tool result:") or line.startswith("[Backend"):
            sources.append(line)
        elif line.startswith("Assistant:"):
            reply = line.removeprefix("Assistant:")
            assert not _WORD_PRICE.search(reply), line
            assert not re.search(r"\d\s*(?:/-|rupaye|rupees|rs\b)", reply, re.IGNORECASE), line
            for amount in _AMOUNT.findall(reply):
                stated += 1
                assert any(
                    re.search(rf"{re.escape(amount)}(?!\d|[,.]\d)", source) for source in sources
                ), (amount, line)
            assert not _GENDERED_USER_ADDRESS.search(line), line
    assert stated >= 3


def test_backend_example_amounts_are_display_strings_of_their_paise() -> None:
    for line in _examples(BACKEND_INSTRUCTIONS, "\n## Examples\n"):
        if not line.startswith("Tool result:"):
            continue

        def walk(value: object) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.endswith("_paise"):
                        assert value[key.removesuffix("_paise")] == format_rupees(item), line
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        result = json.loads(line.removeprefix("Tool result:"))
        walk(result)
        assert "price_rupees" not in line


def test_backend_example_adds_cheaper_store_without_asking() -> None:
    lines = _examples(BACKEND_INSTRUCTIONS, "\n## Examples\n")
    start = next(i for i, line in enumerate(lines) if "seedha Surf Excel 2 kg add karo" in line)
    search = json.loads(lines[start + 2].removeprefix("Tool result:"))["results"]
    cheapest = min((item for item in search if item["available"]), key=lambda item: item["price_paise"])
    add = lines[start + 3]
    assert add.startswith("Tool call: add_to_basket")
    assert json.loads(add.split(" ", 3)[3])["option_id"] == cheapest["option_id"]
    reply = lines[start + 5]
    assert reply.startswith("Assistant:") and "?" not in reply and "sasta" in reply
