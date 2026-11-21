"""Voice tool that recalls the verified household's remembered choices for an item.

Read-only. The household identity comes from the verified session, never from
model arguments. Results describe past explicit preferences or past purchases;
they never mean an item is needed or out of stock at home.
"""

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.memory.recall import PreferenceRecaller, item_terms
from app.voice.tools import ToolOutcome

logger = logging.getLogger(__name__)

RECALL_HOUSEHOLD_PREFERENCES = "recall_household_preferences"
MEMORY_TOOL_NAMES = {RECALL_HOUSEHOLD_PREFERENCES}

MEMORY_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": RECALL_HOUSEHOLD_PREFERENCES,
        "description": (
            "Before searching for an item request without a pack size or variant (a kind of "
            "item such as 'deo order karna hai', or a brand/product such as 'Diet Coke'), or "
            "when the user refers to an earlier purchase, look up this household's own "
            "confirmed preferences and, if they allowed it, past purchases of that item. "
            "Read-only. A past purchase never means the item is needed or out of stock now."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": (
                        "The generic item in short English, 2 to 80 characters, for example "
                        "'deodorant', 'cooking oil', 'milk'. Do not include order words."
                    ),
                }
            },
            "required": ["item"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]


class RecallArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=2, max_length=80)

    @field_validator("item")
    @classmethod
    def normalise(cls, value: str) -> str:
        stripped = " ".join(value.split())
        if not item_terms(stripped):
            raise ValueError("item must name a product type")
        return stripped


NO_MEMORY_NOTE = (
    "Nothing remembered for this item. Ask one short question for brand and size, for "
    "example 'Kaunsa brand aur size chahiye?', unless the user already said them."
)
HISTORY_OFF_NOTE = (
    " Purchase history is off. At most once per conversation you may add, as a statement "
    "and not a second question: 'Chahein to Settings mein history import on kar sakte hain, "
    "phir pichli pasand yaad rahegi.'"
)
FOUND_NOTE = (
    "These are past choices, not current stock or need. Offer the best match (most recent "
    "or most bought) in one question naming its size, for example 'Pichli baar aapne Diet "
    "Coke 330 ml can li thi; wahi ek add karun?'. Search only after the user answers; on "
    "yes, search that exact product and add it in the same turn. Prices come only from search."
)


class MemoryToolExecutor:
    def __init__(self, recaller: PreferenceRecaller | None, household_id: str) -> None:
        if not household_id:
            raise ValueError("A verified household identity is required for memory recall.")
        self.recaller = recaller
        self.household_id = household_id

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name not in MEMORY_TOOL_NAMES:
            return ToolOutcome(model_output={"status": "error", "message": f"Unknown tool {name}."})
        try:
            arguments = RecallArguments.model_validate(json.loads(raw_arguments))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return ToolOutcome(
                model_output={
                    "status": "error",
                    "message": "Invalid arguments. Send a generic item of 2 to 80 characters.",
                }
            )
        if self.recaller is None:
            return ToolOutcome(model_output=self._unavailable(arguments.item))
        try:
            recall = await self.recaller.recall(self.household_id, arguments.item)
        except Exception as exc:  # noqa: BLE001 - memory must never break a voice turn
            logger.warning("Household memory recall failed: %s", type(exc).__name__)
            return ToolOutcome(model_output=self._unavailable(arguments.item))

        candidates = [
            candidate.model_dump(exclude_none=True) for candidate in recall.candidates
        ]
        consent = recall.consent.model_dump()
        if candidates:
            note = FOUND_NOTE
        else:
            note = NO_MEMORY_NOTE
        if recall.consent.purchase_history == "off":
            note += HISTORY_OFF_NOTE
        return ToolOutcome(
            model_output={
                "status": "ok" if candidates else "no_memory",
                "item": arguments.item,
                "consent": consent,
                "candidates": candidates,
                "note": note,
            }
        )

    @staticmethod
    def _unavailable(item: str) -> dict[str, Any]:
        return {
            "status": "no_memory",
            "item": item,
            "consent": {"saved_preferences": "off", "purchase_history": "off"},
            "candidates": [],
            "note": NO_MEMORY_NOTE,
        }
