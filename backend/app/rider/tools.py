"""The four tools the rider assistant may call, and nothing else.

There is no tool to change, cancel or pay for an order, to read an OTP or to
call anyone. Every output is cleaned by ``app.rider.safety`` before the model
sees it, so a code that slipped into stored data is never spoken.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.rider.orders import ActiveOrderReader
from app.rider.safety import clean_value, redact_secrets
from app.rider.store import HANDOFF_TEXT, DeliveryNotes

logger = logging.getLogger(__name__)

GET_ACTIVE_ORDER = "get_active_order"
GET_DELIVERY_NOTES = "get_delivery_notes"
NOTIFY_OWNER = "notify_owner"
END_CALL = "end_call"
MAX_NOTIFICATIONS = 3

RIDER_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": GET_ACTIVE_ORDER,
        "description": (
            "Read the owner's current undelivered order(s) from GharSaathi's records: store, "
            "status, item count, payment (cash_on_delivery or paid_online) and the delivery "
            "address label and address. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": GET_DELIVERY_NOTES,
        "description": (
            "Read the owner's delivery notes for riders: landmark, gate/flat directions, how to "
            "hand over the order and the preferred language. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": NOTIFY_OWNER,
        "description": (
            "Send the owner a short in-app message about this call (no phone call). Use for a "
            "question you could not answer, an OTP or money request, or a message from the caller."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One line, under 200 characters, what the caller said or asked.",
                },
                "urgency": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": ["summary", "urgency"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": END_CALL,
        "description": "End the call after your short goodbye has been said.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["answered", "message_taken", "not_delivery", "caller_left", "time_limit"],
                }
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]
RIDER_TOOL_NAMES = frozenset(tool["name"] for tool in RIDER_TOOL_DEFINITIONS)


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotifyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=240)
    urgency: Literal["low", "normal", "high"]


class EndArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["answered", "message_taken", "not_delivery", "caller_left", "time_limit"]


@dataclass
class OwnerNote:
    summary: str
    urgency: str


@dataclass
class ToolResult:
    output: dict[str, Any]
    end_call: bool = False


@dataclass
class RiderToolbox:
    user_id: str
    orders: ActiveOrderReader
    notes: Callable[[], Awaitable[DeliveryNotes]]
    # Delivers the message to the owner's app now; returns False if it could not be saved.
    on_notify: Callable[[OwnerNote], Awaitable[bool]] | None = None
    used: set[str] = field(default_factory=set)
    notifications: list[OwnerNote] = field(default_factory=list)
    end_reason: str | None = None

    async def execute(self, name: str, arguments: str) -> ToolResult:
        try:
            raw = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return ToolResult({"status": "error", "message": "Arguments were not valid JSON."})
        if not isinstance(raw, dict):
            raw = {}
        try:
            if name == GET_ACTIVE_ORDER:
                _NoArguments.model_validate(raw)
                return ToolResult(await self._active_order())
            if name == GET_DELIVERY_NOTES:
                _NoArguments.model_validate(raw)
                return ToolResult(await self._delivery_notes())
            if name == NOTIFY_OWNER:
                return ToolResult(await self._notify(NotifyArguments.model_validate(raw)))
            if name == END_CALL:
                self.end_reason = EndArguments.model_validate(raw).reason
                return ToolResult({"status": "ending"}, end_call=True)
        except ValidationError:
            return ToolResult({"status": "error", "message": "Invalid arguments for this tool."})
        except Exception as exc:  # noqa: BLE001 - a broken read must not end the call
            logger.warning("Rider tool %s failed: %s", name, type(exc).__name__)
            return ToolResult(
                {
                    "status": "error",
                    "message": "Could not read this right now. Tell the caller you will message the owner.",
                }
            )
        return ToolResult({"status": "error", "message": "Unknown tool."})

    async def _active_order(self) -> dict[str, Any]:
        self.used.add("order")
        orders = await self.orders.active_orders(self.user_id)
        if not orders:
            return {
                "status": "none",
                "message": "No undelivered order in GharSaathi's records. Do not guess; offer to message the owner.",
            }
        items = []
        for order in orders:
            entry = {
                "store": order.store,
                "status": clean_value(order.status, store_text=True),
                "items_count": order.items_count,
                "payment": order.payment,
            }
            if order.address_label:
                entry["address_label"] = clean_value(order.address_label)
            if order.address:
                entry["address"] = clean_value(order.address)
            items.append(entry)
        return {"status": "ok", "orders": items}

    async def _delivery_notes(self) -> dict[str, Any]:
        self.used.add("notes")
        notes = await self.notes()
        output: dict[str, Any] = {
            "status": "ok",
            "handoff": HANDOFF_TEXT.get(notes.handoff, HANDOFF_TEXT["call_again"]),
            "preferred_language": notes.preferred_language,
        }
        if notes.landmark:
            output["landmark"] = notes.landmark
        if notes.directions:
            output["directions"] = notes.directions
        if not notes.landmark and not notes.directions:
            output["message"] = "The owner saved no directions. Do not invent any."
        return clean_value(output)  # type: ignore[return-value]

    async def _notify(self, arguments: NotifyArguments) -> dict[str, Any]:
        if len(self.notifications) >= MAX_NOTIFICATIONS:
            return {"status": "already_notified", "message": "The owner already has this call's messages."}
        summary = redact_secrets(" ".join(arguments.summary.split()))[:240]
        note = OwnerNote(summary=summary, urgency=arguments.urgency)
        self.notifications.append(note)
        if self.on_notify is not None and not await self.on_notify(note):
            return {
                "status": "error",
                "message": "The message could not be saved. Tell the caller to try calling the owner again later.",
            }
        return {"status": "sent", "message": "The owner will see this in the GharSaathi app."}
