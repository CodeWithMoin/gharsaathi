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
    "diet",
    "avoid_ingredient",
    "usual_quantity",
    "shopping_style",
    "household",
    "delivery_note",
    "usual_item",
    "routine",
    "goal",
]
PreferenceGroup = Literal["pasand", "kabhi_nahi", "niyam", "ghar"]
MemoryOrigin = Literal["explicit", "stated", "inferred"]

# Kinds the voice/typed remember/update tools may write (the tool enum).
PREFERENCE_KINDS: tuple[str, ...] = (
    "prefer_brand",
    "prefer_pack_size",
    "prefer_store",
    "avoid_store",
    "avoid_product",
    "avoid_brand",
    "rule",
)
# Household facts the user told us directly (today: only the owner's manual
# demo seed, app.memory.demo_seed). Shown, edited and deleted like the rest.
HOUSEHOLD_KINDS: tuple[str, ...] = (
    "diet",
    "avoid_ingredient",
    "usual_quantity",
    "shopping_style",
    "household",
    "delivery_note",
    "usual_item",
    "routine",
    "goal",
)
ALL_KINDS: tuple[str, ...] = (*PREFERENCE_KINDS, *HOUSEHOLD_KINDS)

KIND_TO_TYPE: dict[str, str] = {
    "prefer_brand": "brand_preference",
    "prefer_pack_size": "pack_size_preference",
    "prefer_store": "store_preference",
    "avoid_store": "store_avoidance",
    "avoid_product": "product_avoidance",
    "avoid_brand": "brand_avoidance",
    "rule": "shopping_rule",
    "diet": "diet_preference",
    "avoid_ingredient": "ingredient_avoidance",
    "usual_quantity": "quantity_preference",
    "shopping_style": "shopping_style",
    "household": "household_fact",
    "delivery_note": "delivery_note",
    "usual_item": "usual_item",
    "routine": "household_routine",
    "goal": "shopping_goal",
}

# A food filter the household chose. The value is a code-controlled enum, so
# "jain" here is a diet (no meat, eggs, onion, garlic or root vegetables), not
# a stored religious trait; free words about faith are still refused.
DIET_LABELS: dict[str, str] = {
    "vegetarian": "Vegetarian",
    "eggetarian": "Vegetarian, eggs are fine",
    "jain": "Jain food (no onion, garlic or root vegetables)",
    "non_vegetarian": "Non-vegetarian",
}
SHOPPING_STYLE_LABELS: dict[str, str] = {
    "cheapest": "Show the cheapest option first",
    "balanced": "Balance price and quality",
    "premium": "Prefers premium brands",
}
# household facts: item names the attribute, value is a small enum or count.
HOUSEHOLD_FACTS: dict[str, tuple[str, ...]] = {
    "people": tuple(str(number) for number in range(1, 16)),
    "kids": ("yes", "no"),
    "baby": ("yes", "no"),
    "pets": ("none", "dog", "cat", "dog_and_cat", "other"),
}
HOUSEHOLD_FACT_LABELS: dict[str, str] = {
    "people": "People at home",
    "kids": "Children at home",
    "baby": "Baby at home",
    "pets": "Pets",
}
MAX_USUAL_QUANTITY = 20
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
    "diet": "niyam",
    "avoid_ingredient": "kabhi_nahi",
    "usual_quantity": "pasand",
    "shopping_style": "niyam",
    "household": "ghar",
    "delivery_note": "ghar",
    "usual_item": "pasand",
    "routine": "ghar",
    "goal": "niyam",
}

VALUE_KEY_BY_KIND: dict[str, str] = {
    "prefer_brand": "brand",
    "avoid_brand": "brand",
    "prefer_pack_size": "pack_size",
    "prefer_store": "store",
    "avoid_store": "store",
    "avoid_product": "product",
    "rule": "rule",
    "diet": "diet",
    "avoid_ingredient": "ingredient",
    "usual_quantity": "quantity",
    "shopping_style": "style",
    "household": "fact",
    "delivery_note": "note",
    "usual_item": "usual_product",
    "routine": "routine",
    "goal": "goal",
}
# Kinds whose value may be a sentence ("Morning: milk with oats ...").
LONG_VALUE_KINDS: frozenset[str] = frozenset({"routine", "delivery_note"})
MAX_LONG_VALUE_CHARS = 200
# Kinds whose value is a code-controlled enum; the policy word list is not
# applied to that enum value (see DIET_LABELS).
ENUM_VALUE_KINDS: frozenset[str] = frozenset({"diet", "shopping_style", "household"})

# Every memory_type covered by the single "remember my preferences" switch.
HOUSEHOLD_PREFERENCE_TYPES: tuple[str, ...] = tuple(
    dict.fromkeys((*KIND_TO_TYPE.values(), "quantity_preference"))
)
PREFERENCE_PURPOSE = "personalize_search"
# The Settings switch toggles these categories (frontend memory-api.ts
# PREFERENCE_CATEGORIES mirrors app.memory.recall.PREFERENCE_TYPES); preference
# memory counts as "on" while any of them is allowed.
CONSENT_TYPES: tuple[str, ...] = (
    "brand_preference",
    "pack_size_preference",
    "quantity_preference",
    "product_avoidance",
)

STORE_LABELS: dict[str, str] = {"swiggy_instamart": "Swiggy Instamart", "zepto": "Zepto"}
_STORE_ALIASES: dict[str, str] = {
    "swiggy_instamart": "swiggy_instamart",
    "swiggy": "swiggy_instamart",
    "instamart": "swiggy_instamart",
    "swiggy instamart": "swiggy_instamart",
    "zepto": "zepto",
}

RULE_LABELS: dict[str, str] = {
    "prefer_cheapest": "Show the cheapest option first",
    "prefer_fastest_delivery": "Show the fastest delivery first",
    "prefer_larger_packs": "Prefer larger packs",
    "prefer_smaller_packs": "Prefer smaller packs",
    "ask_before_substitute": "Ask before substituting an item",
    "prefer_best_unit_value": "Prefer the best value per unit",
    "substitute_closest_and_tell": "If something is out of stock, pick the closest alternative and say so",
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
    value: str = Field(min_length=1, max_length=MAX_LONG_VALUE_CHARS * 2)
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
        cleaned = _clean(value, MAX_LONG_VALUE_CHARS)
        if cleaned is None:
            raise ValueError("value must name a brand, size, store, product or rule")
        return cleaned

    @model_validator(mode="after")
    def check_kind_value(self) -> PreferenceSpec:
        # Counts ("4 people", "2 packs") may be one character; names may not.
        if len(self.value) < 2 and self.kind not in {"household", "usual_quantity"}:
            raise ValueError("value must name a brand, size, store, product or rule")
        if len(self.value) > MAX_VALUE_CHARS and self.kind not in LONG_VALUE_KINDS:
            raise ValueError(f"must be at most {MAX_VALUE_CHARS} characters")
        if self.kind in {"prefer_store", "avoid_store"}:
            store = normalise_store(self.value)
            if store is None:
                raise ValueError("store must be swiggy_instamart or zepto")
            self.value = store
        elif self.kind == "rule":
            if self.value not in RULE_LABELS:
                raise ValueError(f"rule must be one of {', '.join(RULE_LABELS)}")
        elif self.kind in {"prefer_brand", "prefer_pack_size", "usual_quantity", "usual_item"} and self.item is None:
            raise ValueError("item is required for a brand, pack-size or quantity preference")
        elif self.kind == "diet":
            self.value = _enum_value(self.value, DIET_LABELS, "diet")
        elif self.kind == "shopping_style":
            self.value = _enum_value(self.value, SHOPPING_STYLE_LABELS, "value preference")
        elif self.kind == "household":
            allowed = HOUSEHOLD_FACTS.get((self.item or "").casefold())
            if allowed is None:
                raise ValueError(f"household item must be one of {', '.join(HOUSEHOLD_FACTS)}")
            self.item = (self.item or "").casefold()
            self.value = _enum_value(self.value, {value: value for value in allowed}, self.item)
        if self.kind == "usual_quantity":
            if not self.value.isdigit() or not 1 <= int(self.value) <= MAX_USUAL_QUANTITY:
                raise ValueError(f"quantity must be a whole number from 1 to {MAX_USUAL_QUANTITY}")
            self.value = str(int(self.value))
        if self.kind in ENUM_VALUE_KINDS:
            # Enum facts are told, not quoted: no free words are kept with them.
            self.user_words = None
        return self

    @property
    def memory_type(self) -> str:
        return KIND_TO_TYPE[self.kind]

    def texts(self) -> list[str]:
        return [text for text in (self.item, self.value, self.user_words) if text]

    def policy_texts(self) -> list[str]:
        """Texts the sensitive-data policy checks; an enum value is code-controlled."""
        if self.kind in ENUM_VALUE_KINDS:
            return [text for text in (self.user_words,) if text]
        return self.texts()

    def to_value(self, origin: MemoryOrigin) -> dict[str, Any]:
        value: dict[str, Any] = {"kind": self.kind, "origin": origin}
        if self.item:
            value["product_category"] = self.item
        value[VALUE_KEY_BY_KIND[self.kind]] = self.value
        if self.user_words:
            value["user_words"] = self.user_words
        return value

    def summary(self) -> str:
        return summary_for(self.kind, self.item, self.value)


def _enum_value(raw: str, allowed: dict[str, str], label: str) -> str:
    key = "_".join(raw.casefold().replace("-", " ").split())
    if key not in allowed:
        raise ValueError(f"{label} must be one of {', '.join(allowed)}")
    return key


def summary_for(kind: str, item: str | None, value: str) -> str:
    """A short, language-neutral English line built from structured fields only.

    The assistant rephrases it in the user's reply language; it is never spoken verbatim.
    """
    store = STORE_LABELS.get(value, value)
    scope = f" ({item})" if item else ""
    if kind == "prefer_brand":
        return f"{item}: always {value}"
    if kind == "prefer_pack_size":
        return f"{item}: {value} pack"
    if kind == "prefer_store":
        return f"{item or 'Shopping'}: order from {store}"
    if kind == "avoid_store":
        return f"Never order from {store}{scope}"
    if kind == "avoid_product":
        return f"Never suggest {value}"
    if kind == "avoid_brand":
        return f"Avoid {value}{scope}"
    if kind == "diet":
        return f"Diet: {DIET_LABELS.get(value, value)}"
    if kind == "avoid_ingredient":
        return f"Keep out: {value}"
    if kind == "usual_quantity":
        return f"{item}: usually {value}"
    if kind == "shopping_style":
        return SHOPPING_STYLE_LABELS.get(value, value)
    if kind == "household":
        label = HOUSEHOLD_FACT_LABELS.get(item or "", item or "Home")
        return f"{label}: {value.replace('_', ' ')}"
    if kind == "delivery_note":
        return f"Delivery note: {value}"
    if kind == "usual_item":
        return f"{item}: usually {value}"
    if kind == "routine":
        return f"Routine: {value}"
    if kind == "goal":
        return f"Goal: {value}"
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


def structured_kind(memory_type: str, value: dict[str, Any]) -> str | None:
    """The household-fact kind of a memory (diet, keep-outs, usual quantity...), else None.

    Older kinds keep their free-form edit path, so this needs the stored kind.
    """
    raw_kind = value.get("kind")
    if isinstance(raw_kind, str) and raw_kind in HOUSEHOLD_KINDS:
        return raw_kind
    return None


def text_for_value(kind: str, value: dict[str, Any]) -> str:
    """The stored one-line text of a structured memory, from its fields only."""
    item = value.get("product_category")
    main = value.get(VALUE_KEY_BY_KIND[kind])
    text = summary_for(kind, item if isinstance(item, str) else None, str(main or ""))
    if kind != "prefer_pack_size" and isinstance(value.get("pack_size"), str):
        text += f", {value['pack_size']}"
    if kind != "usual_quantity" and value.get("quantity"):
        text += f", usually {value['quantity']}"
    return text


def checked_correction(memory_type: str, value: dict[str, Any]) -> dict[str, Any]:
    """Validate an on-screen edit of a structured memory; returns the cleaned value.

    Raises ValueError (bad shape or enum) or returns the value unchanged for
    older free-form memory types. The sensitive-data policy is checked on the
    same texts a new memory of that kind would be checked on.
    """
    kind = structured_kind(memory_type, value)
    if kind is None:
        return value
    key = VALUE_KEY_BY_KIND[kind]
    main = value.get(key)
    item = value.get("product_category")
    spec = PreferenceSpec(
        kind=kind,  # type: ignore[arg-type]
        item=item if isinstance(item, str) else None,
        value=str(main) if main is not None else "",
    )
    cleaned = dict(value)
    cleaned[key] = spec.value
    if spec.item:
        cleaned["product_category"] = spec.item
    for extra in ("pack_size", "product_name", "quantity"):
        if extra in cleaned and extra != key:
            text = _clean(str(cleaned[extra]), MAX_VALUE_CHARS)
            if text is None:
                del cleaned[extra]
            elif extra == "quantity" and (
                not text.isdigit() or not 1 <= int(text) <= MAX_USUAL_QUANTITY
            ):
                raise ValueError(f"quantity must be a whole number from 1 to {MAX_USUAL_QUANTITY}")
            else:
                cleaned[extra] = text
    category = sensitive_category(
        *spec.policy_texts(),
        *(str(cleaned[extra]) for extra in ("pack_size", "product_name") if extra in cleaned),
    )
    if category is not None:
        raise SensitiveValueError(category)
    return cleaned


class SensitiveValueError(ValueError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category
