"""Deterministic effect of active household rules on search results.

Pure functions over the consented ``HouseholdMemory`` (see ``app.memory.recall``):

- ``avoid_store`` skips that store's search for matching items (all items when
  the rule names no item), unless the query itself names that store;
- ``avoid_product`` / ``avoid_brand`` drop matching options, or keep them flagged
  ``avoided`` when the user's own query asks for exactly that product or brand;
- ``prefer_brand`` / ``prefer_pack_size`` / ``prefer_store`` flag matching options
  ``preferred``.

``remembered`` (past purchases and confirmed choices, ``app.memory.remembered``)
is a separate annotation; these fields never replace it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.commerce.swiggy.models import Product
from app.memory.household import STORE_LABELS
from app.memory.recall import HouseholdMemory, item_terms, term_matcher
from app.memory.remembered import name_tokens, pack_key


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str
    item: str | None = None

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
        }.get(item.kind)
        if value:
            rules.append(Rule(item.kind, value, item.item))
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
    for rule in rules.of("avoid_product", "avoid_brand"):
        if rule.kind == "avoid_product":
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
    for rule in rules.of("prefer_brand", "prefer_pack_size", "prefer_store"):
        if not rule.item_matches(query, product.name):
            continue
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
    }.get(rule.kind, value)


def store_rule_label(rule: Rule) -> str:
    return _label(rule)
