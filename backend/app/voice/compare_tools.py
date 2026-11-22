"""Voice/typed tool that compares 2-4 options already shown on the user's screen.

"Difference kya hai?", "kaunsa better hai?", "Zepto vs Instamart": the model names
the options (option ids, or an item's card numbers) and code works out every
price, unit price and difference (``app/shopping/compare.py``). The model only
repeats those strings. Read-only: nothing is searched, added or ordered.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.shopping.compare import CompareItem, compare
from app.shopping.models import rupees_to_paise
from app.voice.tools import SearchOption, ShownGroups, ToolOutcome, item_key

MAX_COMPARE = 4

COMPARE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "compare_options",
        "description": (
            "Compare 2 to 4 product options already shown to the user: price difference, "
            "pack size, unit price (per 100 g / 100 ml / piece), stock, delivery time and "
            "store. Use for any price, size, value or store comparison ('difference kya hai', "
            "'kaunsa better hai', 'Zepto vs Instamart', 'per 100 ml kitna'). Every amount "
            "and difference is worked out in code; repeat its strings exactly. Read-only: "
            "it never searches, adds or orders."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "option_ids": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "2 to 4 option_id values from search results in this conversation, "
                        "in the order the user named them. Null to use item and positions."
                    ),
                },
                "item": {
                    "type": ["string", "null"],
                    "description": (
                        "The searched item whose cards to compare (its query, e.g. 'Amul lassi'). "
                        "Null means the latest search results."
                    ),
                },
                "positions": {
                    "type": ["array", "null"],
                    "items": {"type": "integer"},
                    "description": (
                        "2 to 4 card numbers of that item, as on screen (1 = first card). "
                        "Null compares its first cards, up to 4."
                    ),
                },
                "aspect": {
                    "type": "string",
                    "enum": ["price", "unit_price", "pack", "store", "all"],
                    "description": "What the user asked about; 'all' when unclear.",
                },
            },
            "required": ["option_ids", "item", "positions", "aspect"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]
COMPARE_TOOL_NAMES = frozenset(tool["name"] for tool in COMPARE_TOOL_DEFINITIONS)

NOTE = (
    "All amounts and differences here were worked out in code. Say only what the user "
    "asked (aspect) in one line, copying price, price_line, unit_price, unit_price_line "
    "and better_value strings exactly; never compute, round or convert an amount "
    "yourself. 'unit price not comparable' means do not compare per 100 g / ml for those. "
    "An option with available false is out of stock: say so and never call it the better "
    "buy. The comparison card is on screen with Add buttons; nothing was added."
)


class CompareArguments(BaseModel):
    option_ids: list[str] | None = Field(default=None, max_length=MAX_COMPARE)
    item: str | None = Field(default=None, max_length=120)
    positions: list[int] | None = Field(default=None, max_length=MAX_COMPARE)
    aspect: Literal["price", "unit_price", "pack", "store", "all"] = "all"

    @field_validator("option_ids", "positions")
    @classmethod
    def distinct(cls, value: list[Any] | None) -> list[Any] | None:
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        if len(unique) < 2:
            raise ValueError("name at least two different options")
        return unique


class CompareToolExecutor:
    def __init__(
        self,
        options: Mapping[str, SearchOption],
        shown: ShownGroups,
        stale: Callable[[str], bool] = lambda option_id: False,
    ) -> None:
        self.options = options
        self.shown = shown
        self.stale = stale

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name not in COMPARE_TOOL_NAMES:
            return _error(f"Unknown tool {name}.")
        try:
            arguments = CompareArguments.model_validate(json.loads(raw_arguments or "{}"))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return _error(
                "Invalid arguments. Send 2 to 4 option_ids, or an item with 2 to 4 card positions, and an aspect."
            )
        resolved = self._resolve(arguments)
        if isinstance(resolved, ToolOutcome):
            return resolved
        ids, note = resolved
        items = [_item(ref, option_id, self.options[option_id], self.shown) for ref, option_id in enumerate(ids, 1)]
        facts = compare(items)
        model_output: dict[str, Any] = {
            "status": "ok",
            "aspect": arguments.aspect,
            **facts,
            "note": NOTE + (f" {note}" if note else ""),
        }
        return ToolOutcome(model_output=model_output, ui_event=_ui_event(arguments.aspect, ids, self.options, facts, self.shown))

    def _resolve(self, arguments: CompareArguments) -> tuple[list[str], str] | ToolOutcome:
        if arguments.option_ids is not None:
            missing = [option_id for option_id in arguments.option_ids if option_id not in self.options]
            stale = [option_id for option_id in arguments.option_ids if self.stale(option_id)]
            if missing or stale:
                return _not_on_screen(
                    "Those options are not on screen any more"
                    + (" (they were priced for the previous delivery address)" if stale and not missing else "")
                    + ". Search the item again, then compare the new results."
                )
            return arguments.option_ids, ""
        key = item_key(arguments.item) if arguments.item else self.shown.latest
        if key is None or key not in self.shown.groups:
            return _not_on_screen(
                "No results for that item are on screen in this conversation. Search it first, then compare."
            )
        group = self.shown.groups[key]
        if arguments.positions is None:
            usable = [option_id for option_id in group if option_id is not None]
            ids, note = usable[:MAX_COMPARE], (
                f"Compared the first {MAX_COMPARE} of {len(usable)} cards." if len(usable) > MAX_COMPARE else ""
            )
        else:
            outside = [position for position in arguments.positions if not 1 <= position <= len(group)]
            if outside:
                return ToolOutcome(
                    model_output={
                        "status": "no_such_card",
                        "cards_on_screen": len(group),
                        "message": f"That item has {len(group)} cards on screen; ask which ones to compare.",
                    }
                )
            picked = [group[position - 1] for position in arguments.positions]
            if any(option_id is None for option_id in picked):
                return _not_on_screen("One of those cards cannot be compared here. Ask which other cards to compare.")
            ids, note = [option_id for option_id in picked if option_id is not None], ""
        if len(ids) < 2:
            return ToolOutcome(
                model_output={
                    "status": "need_two_options",
                    "message": "Only one option is on screen for that item, so there is nothing to compare.",
                }
            )
        if any(self.stale(option_id) for option_id in ids):
            return _not_on_screen(
                "Those cards were priced for the previous delivery address. Search the item again, then compare."
            )
        return ids, note


def _item(ref: int, option_id: str, option: SearchOption, shown: ShownGroups) -> CompareItem:
    product = option.product
    group = shown.group_of(option_id)
    position = shown.groups[group].index(option_id) + 1 if group else None
    return CompareItem(
        ref=ref,
        name=product.name,
        store=option.store,
        store_id=option.provider,
        price_paise=rupees_to_paise(product.price.offer_price),
        mrp_paise=rupees_to_paise(product.price.mrp),
        available=product.available,
        pack_size=product.pack_size,
        delivery_eta=(
            f"{product.delivery_value} {product.delivery_unit}"
            if product.delivery_value and product.delivery_unit
            else None
        ),
        position=position,
        item_label=shown.labels.get(group) if group else None,
        option_id=option_id,
    )


def _ui_event(
    aspect: str,
    ids: list[str],
    options: Mapping[str, SearchOption],
    facts: dict[str, Any],
    shown: ShownGroups,
) -> dict[str, Any]:
    """The comparison card: the products (addable as-is) plus code-made strings and badges."""

    cheapest = (facts.get("cheapest") or {}).get("ref")
    better = (facts.get("better_value") or {}).get("ref")
    columns: list[dict[str, Any]] = []
    for entry, option_id in zip(facts["options"], ids):
        option = options[option_id]
        badges = []
        if entry["ref"] == cheapest:
            badges.append("Cheapest")
        if entry["ref"] == better:
            badges.append(facts["better_value"]["badge"])
        columns.append(
            {
                "product": {
                    **option.product.model_dump(),
                    "provider": option.provider,
                    "provider_label": option.store,
                    **({"position": entry["position"]} if "position" in entry else {}),
                },
                "label": entry["label"],
                "unit_price": entry["unit_price"],
                "badges": badges,
            }
        )
    groups = {shown.group_of(option_id) for option_id in ids}
    label = shown.labels.get(next(iter(groups)) or "", "") if len(groups) == 1 else ""
    headline = ""
    if len(facts["pairs"]) == 1:
        pair = facts["pairs"][0]
        headline = pair["unit_price_line"] if aspect == "unit_price" else pair["price_line"]
    elif facts.get("all_in_stock_same_price"):
        headline = "same price"
    elif facts.get("cheapest"):
        headline = f"Cheapest: {facts['cheapest']['label']}, {facts['cheapest']['price']}"
    return {
        "type": "comparison.ready",
        "item_label": label,
        "aspect": aspect,
        "headline": headline[:1].upper() + headline[1:],
        "options": columns,
    }


def _not_on_screen(message: str) -> ToolOutcome:
    return ToolOutcome(model_output={"status": "options_not_on_screen", "message": message})


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(model_output={"status": "error", "message": message})
