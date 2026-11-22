"""Voice/typed tool that adds one of the user's own saved lists to the basket.

"Weekly list mangwa do" → add_saved_list("weekly list"). Each item is re-checked
at its store (same variant, available, today's price) before it is added; the
rest is reported. It never requests a bill, approves or orders.
"""

import json
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.shopping.basket import BasketService, item_total_paise
from app.shopping.models import Basket, ProviderName
from app.shopping.money import money_fields
from app.shopping.saved_lists import (
    MAX_NAME_LENGTH,
    STORE_LABELS,
    SavedListStore,
    StoreTarget,
    add_items,
)
from app.voice.basket_tools import BasketStore
from app.voice.basket_tools import _basket_ui as basket_ui
from app.voice.tools import ToolOutcome


SAVED_LIST_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "add_saved_list",
        "description": (
            "Add every item of one of the user's own saved lists (for example 'Weekly list', "
            "'Monthly ration') to its store's basket. Use when the user names a saved list. "
            "Items are re-checked at the store first; unavailable or missing items are "
            "reported, not replaced. This does not request a bill or place an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The list name the user said."},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]
SAVED_LIST_TOOL_NAMES = frozenset(tool["name"] for tool in SAVED_LIST_TOOL_DEFINITIONS)

SKIPPED_REASONS = {
    "unavailable": "unavailable at the store right now",
    "not_found": "not found at the store today",
    "store_not_ready": "store not connected or no delivery address chosen",
    "store_error": "the store could not be reached",
    "basket_locked": "that store's basket is locked while an order is being checked",
}


class SavedListArguments(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class SavedListToolExecutor:
    def __init__(
        self,
        lists: SavedListStore | None,
        baskets: BasketService,
        targets: Callable[[], Mapping[ProviderName, StoreTarget]],
        household_id: str,
    ) -> None:
        self.lists = lists
        self.baskets = baskets
        self.targets = targets
        self.household_id = household_id

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name not in SAVED_LIST_TOOL_NAMES:
            return _error(f"Unknown tool {name}.")
        try:
            arguments = SavedListArguments.model_validate(json.loads(raw_arguments or "{}"))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return _error("Invalid arguments. Send the list name.")
        if self.lists is None:
            return _error("Saved lists are not available here.")
        saved, every = await self.lists.find(self.household_id, arguments.name)
        if saved is None:
            return ToolOutcome(
                model_output={
                    "status": "list_not_found",
                    "saved_lists": [item.name for item in every],
                    "message": (
                        "No saved list has that name. Say which of saved_lists the user means, "
                        "or that they can save one from the basket ('Save basket as list')."
                        if every
                        else "The user has no saved lists yet. They can save one from the basket."
                    ),
                }
            )
        if not saved.items:
            return ToolOutcome(
                model_output={"status": "list_empty", "list": saved.name, "message": "This list has no items yet. Nothing was added."}
            )
        report = await add_items(self.baskets, self.household_id, saved.items, self.targets())
        touched = sorted(
            report.baskets.items(),
            key=lambda entry: -sum(1 for item in report.added if item.provider == entry[0]),
        )
        return ToolOutcome(
            model_output={
                "status": "added" if report.added else "nothing_added",
                "list": saved.name,
                "added": [
                    {"name": item.name, "quantity": item.quantity, "store": item.store}
                    for item in report.added
                ],
                "not_added": [
                    {"name": item.name, "store": item.store, "reason": SKIPPED_REASONS[item.status]}
                    for item in report.skipped
                ],
                "baskets": [
                    {"store": STORE_LABELS[provider], "store_id": provider, **money_fields("item_total", item_total_paise(basket))}
                    for provider, basket in report.baskets.items()
                ],
                "note": (
                    "Name what was not added and why, briefly. Then offer the bill "
                    "(request_approval) for the store. Nothing was ordered."
                ),
            },
            # The store with most items added shows last (newest); any other store first.
            extra_ui_events=[
                {"type": "basket.updated", "provider": provider, "basket": _basket_ui(basket)}
                for provider, basket in reversed(touched[1:])
            ],
            ui_event=(
                {
                    "type": "basket.updated",
                    "provider": touched[0][0],
                    "basket": _basket_ui(touched[0][1]),
                }
                if touched
                else None
            ),
        )


def _basket_ui(basket: Basket) -> dict[str, Any]:
    return basket_ui(basket, BasketStore(basket.provider, STORE_LABELS[basket.provider], basket.address_id))


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(model_output={"status": "error", "message": message})
