"""Structured household preferences: kinds, wording, and the sensitive-data policy.

Every preference the assistant writes goes through ``PreferenceSpec``. It stores a
structured value plus a short quote of the user's words, never a free-text
instruction. Provider, brand and product names are treated as data.

The policy is enforced here in code, not only in prompts:

- ``sensitive_category`` rejects health, religion/caste, finances, gender or
  sexuality, age, and family details beyond shopping needs, in every tier.
- ``stated_signal`` decides whether ordinary speech is a habit or rule
  ("hamesha", "kabhi nahi", "sirf", "mat dikhana") or a one-off choice
  ("is baar", "abhi ke liye"). Only habits may be saved without a read-back.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.database.models import MemoryItem, MemorySourceType

PreferenceKind = Literal[
    "prefer_brand",
    "prefer_pack_size",
    "prefer_store",
    "avoid_store",
    "avoid_product",
    "avoid_brand",
    "rule",
]
PreferenceGroup = Literal["pasand", "kabhi_nahi", "niyam"]
MemoryOrigin = Literal["explicit", "stated", "inferred"]

PREFERENCE_KINDS: tuple[str, ...] = (
    "prefer_brand",
    "prefer_pack_size",
    "prefer_store",
    "avoid_store",
    "avoid_product",
    "avoid_brand",
    "rule",
)

KIND_TO_TYPE: dict[str, str] = {
    "prefer_brand": "brand_preference",
    "prefer_pack_size": "pack_size_preference",
    "prefer_store": "store_preference",
    "avoid_store": "store_avoidance",
    "avoid_product": "product_avoidance",
    "avoid_brand": "brand_avoidance",
    "rule": "shopping_rule",
}
TYPE_TO_KIND: dict[str, str] = {value: key for key, value in KIND_TO_TYPE.items()}

# Older memory types that the extractor or sample seed may have written.
LEGACY_GROUPS: dict[str, PreferenceGroup] = {
    "quantity_preference": "pasand",
    "dietary_preference": "niyam",
}
GROUP_BY_KIND: dict[str, PreferenceGroup] = {
    "prefer_brand": "pasand",
    "prefer_pack_size": "pasand",
    "prefer_store": "pasand",
    "avoid_store": "kabhi_nahi",
    "avoid_product": "kabhi_nahi",
    "avoid_brand": "kabhi_nahi",
    "rule": "niyam",
}

VALUE_KEY_BY_KIND: dict[str, str] = {
    "prefer_brand": "brand",
    "avoid_brand": "brand",
    "prefer_pack_size": "pack_size",
    "prefer_store": "store",
    "avoid_store": "store",
    "avoid_product": "product",
    "rule": "rule",
}

# Every memory_type covered by the single "remember my preferences" switch.
HOUSEHOLD_PREFERENCE_TYPES: tuple[str, ...] = (
    *KIND_TO_TYPE.values(),
    "quantity_preference",
)
PREFERENCE_PURPOSE = "personalize_search"

STORE_LABELS: dict[str, str] = {"swiggy_instamart": "Swiggy Instamart", "zepto": "Zepto"}
_STORE_ALIASES: dict[str, str] = {
    "swiggy_instamart": "swiggy_instamart",
    "swiggy": "swiggy_instamart",
    "instamart": "swiggy_instamart",
    "swiggy instamart": "swiggy_instamart",
    "zepto": "zepto",
}

RULE_LABELS: dict[str, str] = {
    "prefer_cheapest": "Sabse sasta option pehle",
    "prefer_fastest_delivery": "Jaldi delivery wala option pehle",
    "prefer_larger_packs": "Bade pack pehle",
    "prefer_smaller_packs": "Chhote pack pehle",
    "ask_before_substitute": "Badalne se pehle poochna",
}

MAX_ITEM_CHARS = 60
MAX_VALUE_CHARS = 80
MAX_USER_WORDS_CHARS = 160

# Policy list: never remembered, whatever the tier or the model's framing.
SENSITIVE_TERMS: dict[str, tuple[str, ...]] = {
    "health": (
        "diabetes", "diabetic", "sugar patient", "sugar ki bimari", "blood pressure", "bp ki",
        "bp patient", "thyroid", "cholesterol", "cancer", "asthma", "allergy", "allergic",
        "pregnant", "pregnancy", "garbh", "period", "pcos", "pcod", "depression", "anxiety",
        "hiv", "kidney patient", "heart patient", "dil ki bimari", "bimari", "beemari", "illness",
        "disease", "medicine", "medication", "dawai", "dawa", "insulin",
        "prescription", "doctor ne", "weight loss", "obese",
    ),
    "religion": (
        "hindu", "muslim", "islam", "sikh", "christian", "jain", "buddhist", "parsi",
        "religion", "religious", "dharam", "dharm", "mazhab", "caste", "jaati", "jati",
        "brahmin", "dalit", "ramzan", "ramadan", "roza", "navratri", "vrat", "upvas",
        "fasting", "halal", "jhatka", "kosher", "pooja", "puja", "namaz", "church", "mandir",
        "masjid", "gurudwara",
    ),
    "finances": (
        "salary", "income", "tankhwah", "loan", "emi", "debt", "karz", "karza", "credit score",
        "credit card", "bank balance", "paise nahi", "paise kam", "garib", "gareeb",
        "ameer", "budget tight",
    ),
    "gender_or_sexuality": (
        "gender", "transgender", "gay", "lesbian", "bisexual", "queer", "lgbt",
        "sexual", "sexuality",
    ),
    "age": (
        "years old", "year old", "saal ka", "saal ki", "saal ke", "umar", "umr",
        "senior citizen", "budhapa", "teenager",
    ),
    "family_details": (
        "divorce", "talaq", "widow", "vidhwa", "shaadi", "marriage", "married", "boyfriend",
        "girlfriend", "affair", "pati", "patni", "husband", "wife", "saas", "sasur",
    ),
}

_HABIT_MARKERS: tuple[str, ...] = (
    "hamesha", "humesha", "hamesa", "har baar", "harbar", "har bar", "kabhi bhi", "kabhi nahi",
    "kabhi mat", "kabhi na", "hum toh", "hum to ", "hum log", "sirf", "bas yahi", "bas wahi", "hi lete", "hi leti", "hi lena", "hi lana",
    "hi chahiye", "hi mangwana", "hi dena", "mat karna", "mat karo", "mat mangao",
    "mat mangwana", "mat dikhana", "mat dikhao", "mat lena", "mat suggest", "mat bhejna",
    "nahi chahiye kabhi", "pasand nahi", "pasand hai", "pasand karte", "accha nahi lagta",
    "always", "never", "only", "usually", "every time", "don't ever", "do not ever",
    "don't show", "don't suggest", "do not suggest", "don't order", "do not order", "we prefer",
    "i prefer", "we always", "we only", "from now on", "ab se", "aage se", "yaad rakhna",
    "yaad rakho", "remember",
)
_ONE_OFF_MARKERS: tuple[str, ...] = (
    "is baar", "iss baar", "is bar", "iss bar", "abhi ke liye", "aaj ke liye",
    "this time", "for now", "just this", "only this time", "ek baar", "filhaal", "fil haal",
    "today", "aaj ka", "aaj ki",
)


def normalise_store(value: str) -> str | None:
    return _STORE_ALIASES.get(" ".join(value.casefold().replace("-", " ").split()))


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.replace("\n", " ").split())
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise ValueError(f"must be at most {limit} characters")
    if any(character in cleaned for character in "{}<>`\\"):
        raise ValueError("may not contain markup or code characters")
    return cleaned


class PreferenceSpec(BaseModel):
    """One structured household preference, validated before any write."""

    model_config = ConfigDict(extra="forbid")

    kind: PreferenceKind
    item: str | None = Field(default=None, max_length=MAX_ITEM_CHARS * 2)
    value: str = Field(min_length=1, max_length=MAX_VALUE_CHARS * 2)
    user_words: str | None = Field(default=None, max_length=MAX_USER_WORDS_CHARS * 2)

    @field_validator("item")
    @classmethod
    def clean_item(cls, value: str | None) -> str | None:
        return _clean(value, MAX_ITEM_CHARS)

    @field_validator("user_words")
    @classmethod
    def clean_words(cls, value: str | None) -> str | None:
        return _clean(value, MAX_USER_WORDS_CHARS)

    @field_validator("value")
    @classmethod
    def clean_value(cls, value: str) -> str:
        cleaned = _clean(value, MAX_VALUE_CHARS)
        if cleaned is None or len(cleaned) < 2:
            raise ValueError("value must name a brand, size, store, product or rule")
        return cleaned

    @model_validator(mode="after")
    def check_kind_value(self) -> PreferenceSpec:
        if self.kind in {"prefer_store", "avoid_store"}:
            store = normalise_store(self.value)
            if store is None:
                raise ValueError("store must be swiggy_instamart or zepto")
            self.value = store
        elif self.kind == "rule":
            if self.value not in RULE_LABELS:
                raise ValueError(f"rule must be one of {', '.join(RULE_LABELS)}")
        elif self.kind in {"prefer_brand", "prefer_pack_size"} and self.item is None:
            raise ValueError("item is required for a brand or pack-size preference")
        return self

    @property
    def memory_type(self) -> str:
        return KIND_TO_TYPE[self.kind]

    def texts(self) -> list[str]:
        return [text for text in (self.item, self.value, self.user_words) if text]

    def to_value(self, origin: MemoryOrigin) -> dict[str, Any]:
        value: dict[str, Any] = {"kind": self.kind, "origin": origin}
        if self.item:
            value["product_category"] = self.item
        if self.kind in {"prefer_brand", "avoid_brand"}:
            value["brand"] = self.value
        elif self.kind == "prefer_pack_size":
            value["pack_size"] = self.value
        elif self.kind in {"prefer_store", "avoid_store"}:
            value["store"] = self.value
        elif self.kind == "avoid_product":
            value["product"] = self.value
        else:
            value["rule"] = self.value
        if self.user_words:
            value["user_words"] = self.user_words
        return value

    def summary(self) -> str:
        return summary_for(self.kind, self.item, self.value)


def summary_for(kind: str, item: str | None, value: str) -> str:
    """A short Roman-Hinglish line built from structured fields only."""
    store = STORE_LABELS.get(value, value)
    scope = f" ({item})" if item else ""
    if kind == "prefer_brand":
        return f"{item}: hamesha {value}"
    if kind == "prefer_pack_size":
        return f"{item}: {value} wala pack"
    if kind == "prefer_store":
        return f"{item or 'Saamaan'}: {store} se"
    if kind == "avoid_store":
        return f"{store} se order nahi{scope}"
    if kind == "avoid_product":
        return f"{value} kabhi suggest nahi karna"
    if kind == "avoid_brand":
        return f"{value} brand nahi{scope}"
    return RULE_LABELS.get(value, value)


def sensitive_category(*texts: str | None) -> str | None:
    """The policy category a text falls under, or None when it is shopping data."""
    haystack = " ".join(text for text in texts if text).casefold()
    for category, terms in SENSITIVE_TERMS.items():
        for term in terms:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}s?(?![a-z0-9])", haystack):
                return category
    if re.search(r"\b\d{1,3}\s*(saal|sal|years?|yrs?)\b", haystack):
        return "age"
    return None


StatedSignal = Literal["habitual", "one_off", "none"]


def stated_signal(user_words: str | None) -> StatedSignal:
    """Whether ordinary speech states a habit/rule, a one-off choice, or neither."""
    if not user_words:
        return "none"
    folded = " " + " ".join(user_words.casefold().split()) + " "
    if any(marker in folded for marker in _ONE_OFF_MARKERS):
        return "one_off"
    if any(marker in folded for marker in _HABIT_MARKERS):
        return "habitual"
    return "none"


def kind_of(memory: MemoryItem) -> str | None:
    raw = memory.value.get("kind") if isinstance(memory.value, dict) else None
    if isinstance(raw, str) and raw in KIND_TO_TYPE:
        return raw
    return TYPE_TO_KIND.get(memory.memory_type)


def group_of(memory: MemoryItem) -> PreferenceGroup | None:
    kind = kind_of(memory)
    if kind is not None:
        return GROUP_BY_KIND[kind]
    return LEGACY_GROUPS.get(memory.memory_type)


def origin_of(memory: MemoryItem) -> MemoryOrigin:
    if memory.source_type in {MemorySourceType.OBSERVED, MemorySourceType.INFERRED}:
        return "inferred"
    raw = memory.value.get("origin") if isinstance(memory.value, dict) else None
    return "stated" if raw == "stated" else "explicit"


def item_of(memory: MemoryItem) -> str | None:
    raw = memory.value.get("product_category") if isinstance(memory.value, dict) else None
    return raw if isinstance(raw, str) and raw.strip() else None


def display_value_of(memory: MemoryItem) -> str | None:
    """The single editable value (brand, size, store, product or rule) of a memory."""
    kind = kind_of(memory)
    value = memory.value if isinstance(memory.value, dict) else {}
    key = VALUE_KEY_BY_KIND.get(kind or "")
    raw = value.get(key) if key else None
    return raw if isinstance(raw, str) and raw.strip() else None


def spec_of(memory: MemoryItem) -> PreferenceSpec | None:
    kind = kind_of(memory)
    value = display_value_of(memory)
    if kind is None or value is None:
        return None
    try:
        return PreferenceSpec(kind=kind, item=item_of(memory), value=value)  # type: ignore[arg-type]
    except ValueError:
        return None
