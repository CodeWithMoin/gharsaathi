"""Deterministic effect of active household rules on search results.

Pure functions over the consented ``HouseholdMemory`` (see ``app.memory.recall``):

- ``avoid_store`` skips that store's search for matching items (all items when
  the rule names no item), unless the query itself names that store;
- ``avoid_product`` / ``avoid_brand`` drop matching options, or keep them flagged
  ``avoided`` when the user's own query asks for exactly that product or brand;
- ``prefer_brand`` / ``prefer_pack_size`` / ``prefer_store`` flag matching options
  ``preferred``;
- ``diet`` (vegetarian / eggetarian / jain) and ``avoid_ingredient`` (a keep-out
  such as peanuts) drop options whose name clearly contains a conflicting word,
  and keep them flagged ``avoided`` when the user asked for that word or the
  name is ambiguous (a "chicken masala" spice mix). Store names do not list every
  ingredient, so a keep-out also asks for a label check (``ingredient_check``);
- ``usual_quantity`` (or a quantity saved with a brand/pack preference) is the
  default pack count for an add when the user named none;
- ``shopping_style: cheapest`` or ``rule: prefer_cheapest`` orders options by
  price within the same rank; ``prefer_fastest_delivery`` by delivery time;
  ``prefer_best_unit_value`` by price per ml / g / piece (pack size from the name);
- ``usual_item`` (a usual buy without a brand, "almonds") flags matching options
  ``preferred``.

``remembered`` (past purchases and confirmed choices, ``app.memory.remembered``)
is a separate annotation; these fields never replace it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.commerce.swiggy.models import Product
from app.memory.household import DIET_LABELS, STORE_LABELS
from app.memory.recall import HouseholdMemory, item_terms, term_matcher
from app.memory.remembered import name_tokens, pack_key


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str
    item: str | None = None
    quantity: int | None = None

    def item_matches(self, *texts: str) -> bool:
        if not self.item:
            return True
        terms = item_terms(self.item)
        if not terms:
            return False
        matches = term_matcher(terms)
        return any(matches(text) for text in texts if text)


@dataclass
class HouseholdRules:
    rules: list[Rule] = field(default_factory=list)

    def of(self, *kinds: str) -> list[Rule]:
        return [rule for rule in self.rules if rule.kind in kinds]

    def __bool__(self) -> bool:
        return bool(self.rules)


def rules_from_memory(memory: HouseholdMemory | None) -> HouseholdRules:
    if memory is None:
        return HouseholdRules()
    rules: list[Rule] = []
    for item in memory.items:
        if item.source != "confirmed_preference" or not item.kind:
            continue
        value = {
            "prefer_brand": item.brand,
            "avoid_brand": item.brand,
            "prefer_pack_size": item.pack_size,
            "prefer_store": item.store_id,
            "avoid_store": item.store_id,
            "avoid_product": item.product,
            "rule": item.rule,
        }.get(item.kind, item.value)
        if value:
            rules.append(Rule(item.kind, value, item.item, item.quantity))
    return HouseholdRules(rules)


# Words that name a store in a query, so "Zepto se hi chocolate" overrides "never Zepto".
STORE_NAME_WORDS: dict[str, frozenset[str]] = {
    "zepto": frozenset({"zepto", "ज़ेप्टो", "जेप्टो"}),
    "swiggy_instamart": frozenset({"swiggy", "instamart", "स्विगी", "इंस्टामार्ट"}),
}


def names_store(query: str, store_id: str) -> bool:
    text = query.casefold()
    return any(word in text for word in STORE_NAME_WORDS.get(store_id, frozenset()))


def without_store_names(query: str) -> str:
    """The query with store names removed, for the store's own product search."""
    words = set().union(*STORE_NAME_WORDS.values())
    kept = [word for word in query.split() if word.casefold().strip(".,!?") not in words]
    return " ".join(kept) if len(" ".join(kept)) >= 2 else query


def avoided_store_rule(rules: HouseholdRules, store_id: str, query: str) -> Rule | None:
    if names_store(query, store_id):
        return None
    for rule in rules.of("avoid_store"):
        if rule.value == store_id and rule.item_matches(query):
            return rule
    return None


def _words_in(words: str, haystack: str) -> bool:
    wanted = name_tokens(words)
    return bool(wanted) and wanted <= name_tokens(haystack)


def _haystack(product: Product) -> str:
    return f"{product.brand or ''} {product.name}"


def product_decision(
    rules: HouseholdRules, product: Product, store_id: str, query: str
) -> dict[str, Any] | None:
    """None (no effect), {"drop": True}, or annotation fields for one option."""
    haystack = _haystack(product)
    food = food_decision(rules, haystack, query)
    if food is not None:
        return food
    for rule in rules.of("avoid_product", "avoid_brand"):
        if rule.kind == "avoid_product":
            special = avoid_category(rule.value)
            if special is not None:
                hit = special(haystack)
                asked = (_sugary_drink_named if special is _sugary_drink else special)(query)
            else:
                terms = item_terms(rule.value)
                hit = bool(terms) and term_matcher(terms)(haystack)
                asked = bool(terms) and term_matcher(terms)(query)
        else:
            hit = _words_in(rule.value, haystack) and rule.item_matches(query, product.name)
            asked = _words_in(rule.value, query)
        if hit:
            if not asked:
                return {"drop": True}
            return {"avoided": True, "avoided_because": _label(rule)}
    reasons: list[str] = []
    for rule in rules.of("prefer_brand", "prefer_pack_size", "prefer_store", "usual_item"):
        if not rule.item_matches(query, product.name):
            continue
        if rule.kind == "usual_item" and _words_in(rule.value, haystack):
            reasons.append(_label(rule))
        if rule.kind == "prefer_brand" and _words_in(rule.value, haystack):
            reasons.append(_label(rule))
        elif rule.kind == "prefer_pack_size":
            wanted = pack_key(rule.value)
            if wanted is not None and wanted == pack_key(f"{product.pack_size or ''} {product.name}"):
                reasons.append(_label(rule))
        elif rule.kind == "prefer_store" and rule.value == store_id:
            reasons.append(_label(rule))
    if reasons:
        return {"preferred": True, "preferred_because": reasons[:2]}
    return None


def _label(rule: Rule) -> str:
    scope = f" for {rule.item}" if rule.item else ""
    value = STORE_LABELS.get(rule.value, rule.value)
    return {
        "prefer_brand": f"prefers {value}{scope}",
        "prefer_pack_size": f"prefers {value} packs{scope}",
        "prefer_store": f"prefers {value}{scope}",
        "avoid_store": f"never order from {value}{scope}",
        "avoid_product": f"never suggest {value}",
        "avoid_brand": f"avoids {value}{scope}",
        "diet": f"{DIET_LABELS.get(rule.value, rule.value).lower()} household",
        "avoid_ingredient": f"keeps out {value}",
        "usual_item": f"usually buys {value}",
    }.get(rule.kind, value)


def store_rule_label(rule: Rule) -> str:
    return _label(rule)


# Words that clearly name meat, fish or egg in a product name.
_MEAT_WORDS = (
    "chicken", "mutton", "lamb", "goat", "beef", "pork", "ham", "bacon", "salami",
    "sausage", "pepperoni", "keema", "kheema", "meat", "fish", "prawn", "prawns", "shrimp",
    "crab", "lobster", "tuna", "salmon", "surmai", "rohu", "pomfret", "basa", "squid",
    "non veg", "non-veg", "nonveg", "gelatin", "gelatine",
)
_EGG_WORDS = ("egg", "eggs", "anda", "ande", "omelette", "omelet", "mayonnaise")
_ROOT_WORDS = (
    "onion", "onions", "pyaz", "pyaaz", "garlic", "lahsun", "lehsun", "potato", "potatoes",
    "aloo", "alu", "carrot", "carrots", "gajar", "beetroot", "radish", "mooli", "ginger",
    "adrak", "sweet potato", "shakarkandi", "arbi", "yam", "jimikand", "turnip", "shalgam",
)
# A name with these words often only names a flavour or a spice mix: flag, never hide.
_AMBIGUOUS_WORDS = ("masala", "seasoning", "flavour", "flavor", "flavoured", "flavored", "style", "spice")
# "Eggless", "egg free", "no onion no garlic", "Jain": the name says it is safe.
_DIET_SAFE_MARKERS = ("eggless", "jain", "no onion", "without onion", "vegan")

DIET_CONFLICTS: dict[str, tuple[str, ...]] = {
    "vegetarian": (*_MEAT_WORDS, *_EGG_WORDS),
    "eggetarian": _MEAT_WORDS,
    "jain": (*_MEAT_WORDS, *_EGG_WORDS, *_ROOT_WORDS),
}
# Small alias groups so a keep-out also catches common Hindi/English names.
INGREDIENT_ALIASES: dict[str, tuple[str, ...]] = {
    "peanut": ("peanut", "peanuts", "groundnut", "groundnuts", "moongphali", "moongfali", "mungfali"),
    "tree nut": ("almond", "almonds", "badam", "cashew", "cashews", "kaju", "walnut", "walnuts", "akhrot", "pista", "pistachio", "hazelnut", "nuts"),
    "gluten": ("gluten", "wheat", "maida", "atta", "sooji", "suji", "rava", "semolina", "barley", "oats"),
    "milk": ("milk", "dairy", "paneer", "cheese", "curd", "dahi", "butter", "ghee", "cream", "lassi", "khoa", "khoya", "whey"),
    "egg": _EGG_WORDS,
    "soy": ("soy", "soya", "tofu"),
    "sesame": ("sesame", "til"),
    "shellfish": ("prawn", "prawns", "shrimp", "crab", "lobster"),
    "fish": ("fish", "tuna", "salmon", "rohu", "pomfret", "basa", "surmai"),
    "mustard": ("mustard", "sarson", "rai"),
}
_INGREDIENT_KEYS: dict[str, str] = {
    "peanuts": "peanut", "groundnut": "peanut", "nuts": "tree nut", "tree nuts": "tree nut",
    "dry fruits": "tree nut", "wheat": "gluten", "dairy": "milk", "lactose": "milk",
    "eggs": "egg", "soya": "soy", "til": "sesame",
}


def _word_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(word) for word in sorted(set(words), key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


def ingredient_words(value: str) -> tuple[str, ...]:
    folded = " ".join(value.casefold().split())
    key = _INGREDIENT_KEYS.get(folded, folded)
    return INGREDIENT_ALIASES.get(key, (folded, f"{folded}s"))


def _safe_name(text: str, words: tuple[str, ...], diet: bool = False) -> bool:
    """The name says it is free of the conflicting thing: "eggless", "peanut free"."""
    folded = f" {text.casefold()} "
    if diet and any(marker in folded for marker in _DIET_SAFE_MARKERS):
        return True
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(word)}[\s-]*(free|less)(?![a-z0-9])", folded)
        for word in words
    )


def food_decision(rules: HouseholdRules, haystack: str, query: str) -> dict[str, Any] | None:
    """Diet and keep-out effect on one option: drop, flag as avoided, or None."""
    name = haystack.casefold()
    asked_text = query.casefold()
    for rule in rules.of("diet", "avoid_ingredient"):
        if rule.kind == "diet":
            words = DIET_CONFLICTS.get(rule.value)
            if not words:
                continue
        else:
            words = ingredient_words(rule.value)
        pattern = _word_pattern(words)
        if not pattern.search(name):
            continue
        if rule.kind == "diet" and _word_pattern(_MEAT_WORDS).search(name):
            pass  # "eggless chicken ..." is still meat
        elif _safe_name(name, words, diet=rule.kind == "diet"):
            continue
        asked = bool(pattern.search(asked_text))
        ambiguous = rule.kind == "diet" and bool(_word_pattern(_AMBIGUOUS_WORDS).search(name))
        if asked or ambiguous:
            return {"avoided": True, "avoided_because": _label(rule)}
        return {"drop": True}
    return None


def keep_outs(rules: HouseholdRules) -> list[str]:
    return [rule.value for rule in rules.of("avoid_ingredient")]


def usual_quantity(rules: HouseholdRules, query: str, product: Product) -> int | None:
    """The pack count this household told us for this item, when exactly one applies."""
    found = {
        rule.quantity
        for rule in rules.rules
        if rule.quantity and rule.item and rule.item_matches(query, product.name)
        and (rule.kind != "prefer_brand" or _words_in(rule.value, _haystack(product)))
    }
    found |= {
        int(rule.value)
        for rule in rules.of("usual_quantity")
        if rule.value.isdigit() and rule.item_matches(query, product.name)
    }
    return found.pop() if len(found) == 1 else None


def sort_mode(rules: HouseholdRules) -> str | None:
    """"cheapest", "fastest" or None: the household's own ordering choice."""
    for rule in rules.rules:
        if (rule.kind == "shopping_style" and rule.value == "cheapest") or (
            rule.kind == "rule" and rule.value == "prefer_cheapest"
        ):
            return "cheapest"
    if any(rule.kind == "rule" and rule.value == "prefer_fastest_delivery" for rule in rules.rules):
        return "fastest"
    if any(rule.kind == "rule" and rule.value == "prefer_best_unit_value" for rule in rules.rules):
        return "unit_value"
    return None


# Avoid phrases that name a kind of product rather than one word to match.
# "sugary cold drinks" must hide Coca-Cola and Sprite but keep Diet Coke and
# Coke Zero; matching any of its words would also hide "Diet Coke Soft Drink".
_SOFT_DRINK_BRANDS = (
    "cola", "coke", "coca cola", "coca-cola", "pepsi", "thums up", "sprite", "fanta", "limca",
    "mountain dew", "7up", "7 up", "mirinda", "maaza", "frooti", "slice", "appy", "red bull",
    "sting", "monster",
)
_SOFT_DRINK_WORDS = (
    *_SOFT_DRINK_BRANDS, "soft drink", "soft drinks", "cold drink", "cold drinks", "soda",
    "tonic water", "energy drink", "iced tea", "ice tea", "juice drink", "fruit drink",
)
_SUGAR_FREE_WORDS = ("diet", "zero", "sugar free", "sugar-free", "sugarfree", "no sugar", "no added sugar", "zero sugar", "unsweetened")
_PORK_WORDS = ("pork", "ham", "bacon", "prosciutto", "pepperoni", "lard", "pork sausage", "chorizo")
_GENERIC_AVOID_WORDS = {"products", "product", "items", "item", "stuff", "things", "anything"}


def _sugary_drink(text: str, words: tuple[str, ...] = _SOFT_DRINK_WORDS) -> bool:
    folded = text.casefold()
    return bool(_word_pattern(words).search(folded)) and not _word_pattern(_SUGAR_FREE_WORDS).search(folded)


def _sugary_drink_named(query: str) -> bool:
    """The query names a sugary drink itself ("Thums Up"), not just "cold drink"."""
    return _sugary_drink(query, _SOFT_DRINK_BRANDS)


def avoid_category(value: str):
    """A matcher for an avoid phrase that names a product kind, or None for plain words."""
    words = [word for word in value.casefold().replace("-", " ").split() if word not in _GENERIC_AVOID_WORDS]
    phrase = " ".join(words)
    if ("sugar" in phrase or "sugary" in phrase) and any(
        word in phrase for word in ("drink", "soda", "cola", "beverage")
    ):
        return _sugary_drink
    if phrase in {"pork", "pig", "pig meat"}:
        pattern = _word_pattern(_PORK_WORDS)
        return lambda text: bool(pattern.search(text.casefold()))
    if len(words) != len(value.split()) and words:
        # "pork products" -> "pork": match the remaining words only.
        terms = item_terms(phrase)
        if terms:
            return term_matcher(terms)
    return None
