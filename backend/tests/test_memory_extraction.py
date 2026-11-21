from uuid import uuid4

import pytest

from app.database.models import MemoryItem
from app.memory.extraction import LocalRulesMemoryExtractor
from app.memory.personalization import personalize_search_query
from app.memory.prompts import MEMORY_EXTRACTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_local_extractor_requires_durable_language() -> None:
    extractor = LocalRulesMemoryExtractor()

    one_time_request = await extractor.extract(
        "Find Fortune oil in a 1 litre pack today.", "en-IN"
    )
    assert one_time_request.candidates == []

    preference = await extractor.extract(
        "I prefer Fortune oil in 1 litre packs.", "en-IN"
    )
    extracted = {item.memory_type: item for item in preference.candidates}
    assert extracted["brand_preference"].attributes[0].value == "Fortune"
    assert {item.key: item.value for item in extracted["brand_preference"].attributes}[
        "product_category"
    ] == "oil"
    assert extracted["pack_size_preference"].evidence == "1 litre"


def test_extraction_prompt_has_privacy_and_injection_boundaries() -> None:
    prompt = MEMORY_EXTRACTION_SYSTEM_PROMPT.casefold()
    assert "precise location" in prompt
    assert "finances" in prompt
    assert "relationships" in prompt
    assert "never as instructions" in prompt
    assert "exact contiguous quote" in prompt


def test_search_personalization_only_applies_category_relevant_memory() -> None:
    relevant = MemoryItem(
        id=uuid4(),
        memory_type="brand_preference",
        value={"brand": "Fortune", "product_category": "oil"},
    )
    irrelevant = MemoryItem(
        id=uuid4(),
        memory_type="brand_preference",
        value={"brand": "Colgate", "product_category": "toothpaste"},
    )

    result = personalize_search_query("cooking oil", [irrelevant, relevant])

    assert result.provider_query == "cooking oil Fortune"
    assert result.applied_memory_ids == [relevant.id]


def test_search_personalization_does_not_apply_uncategorized_brand() -> None:
    memory = MemoryItem(
        id=uuid4(),
        memory_type="brand_preference",
        value={"brand": "Fortune"},
    )

    result = personalize_search_query("toothpaste", [memory])

    assert result.provider_query == "toothpaste"
    assert result.applied_memory_ids == []
