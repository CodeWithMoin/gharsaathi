"""Deterministic descriptions of saved conversation states.

Used by the recent-chats drawer (title, emptiness) and by the assistant's
"previous chats" context. Saved state is untrusted: callers sanitise before
placing any of it in model instructions.
"""

from collections.abc import Mapping
from typing import Any

STORE_LABELS: dict[str, str] = {"swiggy_instamart": "Swiggy Instamart", "zepto": "Zepto"}
STAGE_LABELS: dict[str, str] = {
    "updated": "basket",
    "bill_ready": "bill made",
    "bill_approved": "bill approved",
    "ordered": "order confirmed",
}
STAGE_RANK = {"updated": 0, "bill_ready": 1, "bill_approved": 2, "ordered": 3}


def _messages(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = state.get("messages")
    return [item for item in messages if isinstance(item, Mapping)] if isinstance(messages, list) else []


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def message_count(state: object) -> int:
    """Messages with text: the unit the rolling summary counts turns in."""

    if not isinstance(state, Mapping):
        return 0
    return sum(1 for item in _messages(state) if _text(item.get("content")))


def searched_items(state: Mapping[str, Any]) -> list[str]:
    """Item queries searched in this chat, oldest first, without repeats."""

    queries: list[str] = []
    groups = state.get("result_groups")
    if isinstance(groups, list) and groups:
        ordered = sorted(
            (group for group in groups if isinstance(group, Mapping)),
            key=lambda group: group.get("seq") if isinstance(group.get("seq"), int) else 0,
        )
        queries = [_text(group.get("item_label")) or _text(group.get("query")) for group in ordered]
    elif state.get("searched") or state.get("products"):
        queries = [_text(state.get("submitted_query"))]
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        if query and query.casefold() not in seen:
            seen.add(query.casefold())
            unique.append(query)
    return unique


def discussed_items(state: object, limit: int = 5) -> list[str]:
    """Distinct items discussed, newest first: result-group labels, else product names."""

    if not isinstance(state, Mapping):
        return []
    items = list(reversed(searched_items(state)))
    if not items:
        products = state.get("products")
        seen: set[str] = set()
        for product in products if isinstance(products, list) else []:
            name = _text(product.get("name")) if isinstance(product, Mapping) else ""
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                items.append(name)
    return [item[:60] for item in items[:limit]]


def is_empty_conversation(state: object) -> bool:
    """Nothing the user would recognise: no message, search or basket card."""

    if not isinstance(state, Mapping):
        return True
    if any(_text(item.get("content")) for item in _messages(state)):
        return False
    if _text(state.get("submitted_query")) or searched_items(state):
        return False
    timeline = state.get("timeline")
    if isinstance(timeline, Mapping) and (
        timeline.get("live") or timeline.get("summaries") or timeline.get("orders")
    ):
        return False
    return True


def conversation_title(state: object, limit: int = 80) -> str:
    """The first user request, else the last search, else the items searched."""

    if not isinstance(state, Mapping):
        return "Shopping chat"
    first = next(
        (_text(item.get("content")) for item in _messages(state) if item.get("role") == "user" and _text(item.get("content"))),
        "",
    )
    if not first:
        first = _text(state.get("submitted_query")) or ", ".join(searched_items(state))
    return (first or "Shopping chat")[:limit]


def store_outcomes(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The furthest basket step reached per store, from the saved timeline."""

    timeline = state.get("timeline")
    if not isinstance(timeline, Mapping):
        return {}
    cards = [
        card
        for card in (timeline.get("summaries") or [])
        if isinstance(card, Mapping) and card.get("provider") in STORE_LABELS and card.get("stage") in STAGE_RANK
    ]
    best: dict[str, Mapping[str, Any]] = {}
    for card in sorted(cards, key=lambda card: card.get("seq") if isinstance(card.get("seq"), int) else 0):
        provider = str(card["provider"])
        current = best.get(provider)
        if current is None or STAGE_RANK[str(card["stage"])] >= STAGE_RANK[str(current["stage"])]:
            best[provider] = card
    for card in timeline.get("live") or []:
        if isinstance(card, Mapping) and card.get("provider") in STORE_LABELS and card["provider"] not in best:
            best[str(card["provider"])] = {"provider": card["provider"], "stage": "updated"}
    return best
