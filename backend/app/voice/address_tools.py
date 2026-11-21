"""Voice tool that opens the on-screen Zepto address form for user review.

It never saves an address and never calls a provider. The only server-side
check is the local Zepto connection status, which reads the stored token.
Everything the model heard is untrusted prefill that the user edits and saves
on screen; coordinates and phone numbers are deliberately not accepted.
"""

import json
import logging
import unicodedata
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.commerce.zepto.errors import ZeptoError
from app.commerce.zepto.models import ConnectionStatus
from app.voice.tools import ToolOutcome

logger = logging.getLogger(__name__)

OPEN_ADDRESS_FORM = "open_address_form"
ADDRESS_TOOL_NAMES = {OPEN_ADDRESS_FORM}

# Keep in sync with the input limits in frontend/app/zepto-address-form.tsx.
TEXT_LIMITS: dict[str, int] = {
    "label": 80,
    "flat_details": 160,
    "building_name": 160,
    "floor": 40,
    "landmark": 160,
    "area": 200,
    "formatted_address": 500,
    "contact_name": 100,
}

_FIELD_DESCRIPTIONS = {
    "label": "Short label such as 'Home' or 'Mummy ka ghar', if the user said one.",
    "flat_details": "Flat or house number, if the user said it.",
    "building_name": "Building, society or street name, if the user said it.",
    "floor": "Floor, if the user said it.",
    "landmark": "Nearby landmark, if the user said it.",
    "area": "Area, city and state, if the user said them.",
    "formatted_address": "Full address text including PIN code, only if the user dictated it.",
    "contact_name": "Delivery contact name, only if the user said it.",
}

ADDRESS_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": OPEN_ADDRESS_FORM,
        "description": (
            "Open the on-screen form to add a new delivery address, pre-filled with only "
            "the text details the user actually said. This never saves the address: the "
            "user reviews the fields, sets the map location and taps Save. Use null for "
            "anything not said. Do not include phone numbers or coordinates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "store": {
                    "type": "string",
                    "enum": ["zepto", "swiggy_instamart", "unspecified"],
                    "description": "Store the user named for this address, or unspecified.",
                },
                "address_type": {
                    "type": ["string", "null"],
                    "enum": ["HOME", "WORK", "OTHER", None],
                    "description": "HOME, WORK or OTHER if the user said it, otherwise null.",
                },
                **{
                    name: {"type": ["string", "null"], "description": description}
                    for name, description in _FIELD_DESCRIPTIONS.items()
                },
            },
            "required": ["store", "address_type", *_FIELD_DESCRIPTIONS],
            "additionalProperties": False,
        },
        "strict": True,
    }
]


def _clean_text(value: str) -> str:
    """Drop control/format characters and collapse whitespace."""

    characters: list[str] = []
    for char in value:
        category = unicodedata.category(char)
        if category == "Cc" or category.startswith("Z"):
            characters.append(" ")
        elif not category.startswith("C"):
            characters.append(char)
    return " ".join("".join(characters).split())


class OpenAddressFormArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: Literal["zepto", "swiggy_instamart", "unspecified"] = "unspecified"
    address_type: Literal["HOME", "WORK", "OTHER"] | None = None
    label: str | None = Field(default=None, max_length=TEXT_LIMITS["label"])
    flat_details: str | None = Field(default=None, max_length=TEXT_LIMITS["flat_details"])
    building_name: str | None = Field(default=None, max_length=TEXT_LIMITS["building_name"])
    floor: str | None = Field(default=None, max_length=TEXT_LIMITS["floor"])
    landmark: str | None = Field(default=None, max_length=TEXT_LIMITS["landmark"])
    area: str | None = Field(default=None, max_length=TEXT_LIMITS["area"])
    formatted_address: str | None = Field(
        default=None, max_length=TEXT_LIMITS["formatted_address"]
    )
    contact_name: str | None = Field(default=None, max_length=TEXT_LIMITS["contact_name"])

    @field_validator(*TEXT_LIMITS, mode="before")
    @classmethod
    def clean_text(cls, value: object) -> object:
        if isinstance(value, str):
            return _clean_text(value) or None
        return value

    def prefill(self) -> dict[str, str]:
        values = {
            "type": self.address_type,
            "name": self.label,
            "flat_details": self.flat_details,
            "building_name": self.building_name,
            "floor": self.floor,
            "landmark": self.landmark,
            "short_address": self.area,
            "formatted_address": self.formatted_address,
            "contact_name": self.contact_name,
        }
        return {key: value for key, value in values.items() if value}


class ZeptoConnectionChecker(Protocol):
    async def connection_status(self) -> ConnectionStatus: ...


_UNAVAILABLE = "address_form.unavailable"


class AddressFormToolExecutor:
    def __init__(self, zepto: ZeptoConnectionChecker | None) -> None:
        self.zepto = zepto

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name != OPEN_ADDRESS_FORM:
            return ToolOutcome(model_output={"status": "error", "message": f"Unknown tool {name}."})
        try:
            arguments = OpenAddressFormArguments.model_validate(json.loads(raw_arguments))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return ToolOutcome(
                model_output={
                    "status": "error",
                    "message": (
                        "Invalid arguments. Send only short address text fields, or null. "
                        "Phone numbers and coordinates are not accepted."
                    ),
                }
            )

        if arguments.store == "swiggy_instamart":
            return ToolOutcome(
                model_output={
                    "status": "swiggy_unsupported",
                    "message": (
                        "Swiggy addresses can only be added in the Swiggy app. Say so in one "
                        "short line. A Zepto address can be added here if the user wants."
                    ),
                },
                ui_event={"type": _UNAVAILABLE, "reason": "swiggy_unsupported"},
            )

        status = await self._zepto_status()
        if status == "demo":
            return ToolOutcome(
                model_output={
                    "status": "demo_mode",
                    "message": "Demo mode cannot add real addresses. Say so in one short line.",
                },
                ui_event={"type": _UNAVAILABLE, "reason": "demo_mode"},
            )
        if status != "connected":
            return ToolOutcome(
                model_output={
                    "status": "not_connected",
                    "message": (
                        "Zepto is not connected, so no form was opened. Tell the user in one "
                        "short line to connect Zepto first. Swiggy addresses are added in the "
                        "Swiggy app."
                    ),
                },
                ui_event={"type": _UNAVAILABLE, "reason": "zepto_not_connected"},
            )

        prefill = arguments.prefill()
        return ToolOutcome(
            model_output={
                "status": "form_opened",
                "store": "Zepto",
                "prefilled_fields": sorted(prefill),
                "message": (
                    "The Zepto address form is open on screen for review. Nothing was saved. "
                    "Ask the user to check the details, set the map location, add the contact "
                    "number, and tap Save. Never say the address was added."
                ),
            },
            ui_event={"type": "address_form.open", "provider": "zepto", "prefill": prefill},
        )

    async def _zepto_status(self) -> Literal["connected", "not_connected", "demo"]:
        if self.zepto is None:
            return "not_connected"
        try:
            status = await self.zepto.connection_status()
        except (ZeptoError, OSError, ValueError):
            logger.warning("Could not read Zepto connection status for the address form.")
            return "not_connected"
        if status.mode == "demo":
            return "demo"
        return "connected" if status.connected else "not_connected"
