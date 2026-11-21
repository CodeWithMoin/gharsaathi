import re
from typing import Protocol

from openai import AsyncOpenAI

from app.memory.prompts import MEMORY_EXTRACTION_SYSTEM_PROMPT
from app.memory.schemas import (
    ExtractedMemoryCandidate,
    MemoryAttribute,
    MemoryExtractionResult,
)


class MemoryExtractor(Protocol):
    name: str

    async def extract(self, message: str, locale: str) -> MemoryExtractionResult: ...


class OpenAIMemoryExtractor:
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def extract(self, message: str, locale: str) -> MemoryExtractionResult:
        response = await self.client.responses.parse(
            model=self.model,
            instructions=MEMORY_EXTRACTION_SYSTEM_PROMPT,
            input=(
                f"Locale: {locale}\n"
                "Extract only explicit durable shopping preferences.\n"
                f"<user_message>{message}</user_message>"
            ),
            text_format=MemoryExtractionResult,
            store=False,
        )
        if response.output_parsed is None:
            return MemoryExtractionResult(candidates=[])
        return response.output_parsed


class LocalRulesMemoryExtractor:
    """High-precision fallback for common English and Hinglish preference phrases."""

    name = "local_rules"
    _durable_marker = re.compile(
        r"\b(prefer|preferred|usually|always|never|avoid|pasand|hamesha|mat lena|"
        r"nahi chahiye)\b",
        re.IGNORECASE,
    )
    _pack_size = re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(kg|g|gram|grams|l|litre|liter|litres|liters|ml)\b",
        re.IGNORECASE,
    )
    _brand_after_prefer = re.compile(
        r"\b(?:prefer|pasand(?:\s+hai)?|usually\s+(?:buy|use|get))\s+"
        r"(?P<brand>[A-Za-z][A-Za-z0-9&.-]{1,39})\b",
        re.IGNORECASE,
    )
    _brand_before_pasand = re.compile(
        r"\b(?P<brand>[A-Za-z][A-Za-z0-9&.-]{1,39})\s+"
        r"(?:oil|rice|atta|flour|milk|detergent|soap|toothpaste|tea|coffee|"
        r"biscuits?|bread|dal|pulses?|shampoo|cleaner)\s+pasand(?:\s+hai)?\b",
        re.IGNORECASE,
    )
    _avoidance = re.compile(
        r"\b(?:never\s+(?:buy|use|get)|avoid|mat\s+lena|nahi\s+chahiye)\s+"
        r"(?P<product>[^,.!?]{2,80})",
        re.IGNORECASE,
    )
    _product_category = re.compile(
        r"\b(oil|rice|atta|flour|milk|detergent|soap|toothpaste|tea|coffee|"
        r"biscuits?|bread|dal|pulses?|shampoo|cleaner)\b",
        re.IGNORECASE,
    )

    async def extract(self, message: str, locale: str) -> MemoryExtractionResult:
        del locale
        if not self._durable_marker.search(message):
            return MemoryExtractionResult(candidates=[])

        candidates: list[ExtractedMemoryCandidate] = []
        avoidance = self._avoidance.search(message)
        if avoidance:
            product = avoidance.group("product").strip()
            candidates.append(
                self._candidate(
                    "product_avoidance",
                    [MemoryAttribute(key="product", value=product)],
                    f"Avoids {product}.",
                    avoidance.group(0),
                    0.9,
                )
            )

        brand = self._brand_after_prefer.search(message) or self._brand_before_pasand.search(
            message
        )
        if brand:
            brand_name = brand.group("brand").strip()
            attributes = [MemoryAttribute(key="brand", value=brand_name)]
            category = self._product_category.search(message)
            if category:
                attributes.append(
                    MemoryAttribute(
                        key="product_category", value=category.group(0).strip()
                    )
                )
            candidates.append(
                self._candidate(
                    "brand_preference",
                    attributes,
                    f"Prefers the brand {brand_name}.",
                    brand.group(0),
                    0.82,
                )
            )

        pack = self._pack_size.search(message)
        if pack:
            pack_size = pack.group(0).strip()
            attributes = [MemoryAttribute(key="pack_size", value=pack_size)]
            category = self._product_category.search(message)
            if category:
                attributes.append(
                    MemoryAttribute(
                        key="product_category", value=category.group(0).strip()
                    )
                )
            candidates.append(
                self._candidate(
                    "pack_size_preference",
                    attributes,
                    f"Prefers {pack_size} packs.",
                    pack.group(0),
                    0.85,
                )
            )

        return MemoryExtractionResult(candidates=candidates[:5])

    @staticmethod
    def _candidate(
        memory_type: str,
        attributes: list[MemoryAttribute],
        summary: str,
        evidence: str,
        confidence: float,
    ) -> ExtractedMemoryCandidate:
        return ExtractedMemoryCandidate.model_validate(
            {
                "memory_type": memory_type,
                "attributes": attributes,
                "summary": summary,
                "evidence": evidence,
                "confidence": confidence,
            }
        )


def create_memory_extractor(api_key: str, model: str) -> MemoryExtractor:
    if api_key.strip():
        return OpenAIMemoryExtractor(api_key, model)
    return LocalRulesMemoryExtractor()
