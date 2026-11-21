import re
import unicodedata
from typing import Literal


NonSearchReason = Literal[
    "ambiguous_brand",
    "capabilities",
    "connection_status",
    "greeting",
]


_BROAD_BRANDS = {
    "aashirvaad",
    "amul",
    "britannia",
    "dabur",
    "fortune",
    "haldiram",
    "maggi",
    "nestle",
    "parle",
    "patanjali",
    "surf excel",
    "tata",
}


def non_search_reason(query: str) -> NonSearchReason | None:
    normalized = _normalize(query)
    if normalized in {
        "hi",
        "hello",
        "hey",
        "namaste",
        "thanks",
        "thank you",
        "shukriya",
    }:
        return "greeting"
    if re.search(
        r"\b(?:what can you do|how can you help|who are you|"
        r"kya kar sakte ho|kya kar sakti ho|"
        r"(?:tum|aap) kya kar sakt(?:e|i) ho|(?:tum|aap) kaun ho)\b",
        normalized,
    ):
        return "capabilities"
    if re.search(r"\b(?:swiggy|zepto)\b", normalized) and re.search(
        r"\b(?:connected|connect|linked|link|access|see|dekh|dikh)\b",
        normalized,
    ):
        return "connection_status"
    if _requested_subject(normalized) in _BROAD_BRANDS:
        return "ambiguous_brand"
    return None


def clarification_for_query(query: str) -> str | None:
    normalized = _normalize(query)
    brand = _requested_subject(normalized)
    if brand not in _BROAD_BRANDS:
        return None
    display_brand = " ".join(part.capitalize() for part in brand.split())
    if brand == "amul":
        return (
            "Amul ka kya chahiye—milk, butter, paneer, ya kuch aur? "
            "Pack size bhi bata dijiye."
        )
    return (
        f"{display_brand} ka kaunsa product chahiye? Product aur pack size "
        "bata dijiye."
    )


def _requested_subject(normalized: str) -> str:
    value = normalized
    wrappers = (
        r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
        r"(?:order|buy|get|find|show|search)(?:\s+me)?\s+",
        r"^(?:please\s+)?(?:mujhe\s+)?(?:mangwa\s+do|mangao|manga\s+do|dila\s+do)\s+",
        r"^(?:mujhe\s+)?",
    )
    for wrapper in wrappers:
        value = re.sub(wrapper, "", value).strip()
    value = re.sub(
        r"\s+(?:please|chahiye|milega|milega kya|mangwa do|manga do|mangao|"
        r"order karo|order kar do|le aao)$",
        "",
        value,
    ).strip()
    return value


def _normalize(query: str) -> str:
    value = unicodedata.normalize("NFKC", query).casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())
