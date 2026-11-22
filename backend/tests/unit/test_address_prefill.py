import json
import re

import pytest

from app.voice.address_prefill import (
    KNOWN_SAMPLE_VALUES,
    RecentUserWords,
    field_was_said,
    filter_prefill,
)
from app.voice.address_tools import ADDRESS_TOOL_DEFINITIONS
from app.voice.prompts import BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS


@pytest.mark.parametrize(
    ("value", "said"),
    [
        ("RMZ The Loft", "rmz the loft"),
        ("RMZ The Loft", "naya address add karo, आरएमज़ेड द लॉफ्ट"),
        ("Gachibowli", "lagado floor 3 gachi bowli"),
        ("Gachibowli", "गचीबोवली"),
        ("3", "floor teen"),
        ("3", "तीसरी मंज़िल"),
        ("Home", "isko home naam do"),
        ("मंदिर के पास", "मंदिर के पास वाला"),
    ],
)
def test_said_values_are_kept_across_scripts(value: str, said: str) -> None:
    assert field_was_said(value, said)


@pytest.mark.parametrize(
    ("value", "said"),
    [
        ("Lake View Apartments", "rmz the loft"),
        ("Indiranagar, Bengaluru, Karnataka", "rmz the loft gachibowli"),
        ("Near metro station", "floor 3"),
        ("4B", "floor 3"),
    ],
)
def test_unsaid_values_are_dropped(value: str, said: str) -> None:
    assert not field_was_said(value, said)


def test_owner_session_prefill_maps_to_said_fields_only() -> None:
    said = "rmz the loft. lagado floor 3, Gachibowli. Number mera bhar dena"
    kept, dropped = filter_prefill(
        {
            "building_name": "RMZ The Loft",
            "floor": "3",
            "short_address": "Gachibowli",
            "flat_details": "Flat 4B",
            "landmark": "Near metro station",
            "formatted_address": "Indiranagar, Bengaluru, Karnataka",
            "contact_name": "9876543210",
        },
        said,
    )
    assert kept == {"building_name": "RMZ The Loft", "floor": "3", "short_address": "Gachibowli"}
    assert dropped == ["contact_name", "flat_details", "formatted_address", "landmark"]


def test_without_user_words_only_samples_phones_and_coordinates_are_dropped() -> None:
    kept, dropped = filter_prefill(
        {"type": "HOME", "building_name": "Lake View Apartments", "floor": "2",
         "landmark": "12.971599, 77.594566", "contact_name": "+91 98765 43210"},
        None,
    )
    assert kept == {"type": "HOME", "floor": "2"}
    assert dropped == ["building_name", "contact_name", "landmark"]


async def test_recent_user_words_combines_live_and_saved_text() -> None:
    async def saved() -> str:
        return "pehle ka message"

    words = RecentUserWords(saved)
    words.add("RMZ The Loft")
    words.add_partial("floor ")
    words.add_partial("3")
    text = await words.text()
    assert "pehle ka message" in text and "RMZ The Loft" in text and "floor 3" in text


def _text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).lower()


def test_tool_description_and_examples_carry_no_sample_addresses() -> None:
    description = _text(ADDRESS_TOOL_DEFINITIONS)
    examples = (BACKEND_INSTRUCTIONS + LIVE_INSTRUCTIONS).lower()
    for sample in KNOWN_SAMPLE_VALUES:
        if len(sample) > 4:
            assert sample not in description, sample
            assert sample not in examples, sample
    assert not re.search(r"flat \d|\d{6}", description)
    # The description tells the model the phone number is the user's to type.
    assert "phone numbers" in description and "contact number" in description
    for prompt in (BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS):
        assert "contact number" in prompt
