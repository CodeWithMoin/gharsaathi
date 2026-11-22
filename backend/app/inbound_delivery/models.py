"""Request/response shapes for the inbound delivery line, and their validation."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.rider.safety import mentions_secret

Provider = Literal["swiggy", "zepto"]
# Key of each store in the saved address snapshot (app/commerce/provider_state.py).
ADDRESS_BOOK_KEYS: dict[str, str] = {"swiggy": "swiggy_instamart", "zepto": "zepto"}
STORE_NAMES: dict[str, str] = {"swiggy": "Swiggy", "zepto": "Zepto"}

LANDMARK_MAX = 120
STEP_MAX = 80
MAX_STEPS = 8
GATE_NOTE_MAX = 120
HANDOVER_MAX = 100

LinePhase = Literal[
    "none",
    # The last change shared delivery details.
    "processing",
    "ready",
    "rejected",
    "failed",
    "unknown",
    # The last change cleared them.
    "clearing",
    "cleared",
    "clear_failed",
]
RevokeReason = Literal["address_changed", "delivered", "cancelled", "user_cleared"]
DisabledReason = Literal["not_configured", "owner_not_configured", "not_owner"]

_URL = re.compile(r"(?i)\bhttps?://|\bwww\.")


def clean_line(value: str, *, field: str) -> str:
    """One plain line: no control characters, links, OTPs, PINs or payment details."""

    if any(unicodedata.category(char) == "Cc" and char not in "\t" for char in value):
        raise ValueError(f"Keep the {field} on one line, as plain text.")
    cleaned = " ".join(unicodedata.normalize("NFC", value).split())
    if mentions_secret(cleaned):
        raise ValueError("Delivery directions can't contain OTPs, PINs, passwords or payment details.")
    if _URL.search(cleaned):
        raise ValueError("Delivery directions can't contain links.")
    return cleaned


class DeliveryDirections(BaseModel):
    """What the delivery line may tell an approved caller about one saved address."""

    model_config = ConfigDict(extra="forbid")

    landmark: str = Field(max_length=LANDMARK_MAX)
    route_directions: list[str] = Field(default_factory=list, max_length=MAX_STEPS)
    gate_note: str | None = Field(default=None, max_length=GATE_NOTE_MAX)
    handover_instructions: str | None = Field(default=None, max_length=HANDOVER_MAX)

    @field_validator("landmark", mode="before")
    @classmethod
    def clean_landmark(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = clean_line(value, field="landmark")
        if len(cleaned) < 3:
            raise ValueError("Add a landmark of at least 3 characters.")
        return cleaned

    @field_validator("gate_note", "handover_instructions", mode="before")
    @classmethod
    def clean_optional(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        return clean_line(value, field="note") or None

    @field_validator("route_directions", mode="before")
    @classmethod
    def clean_steps(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        steps: list[object] = []
        for step in value:
            if not isinstance(step, str):
                steps.append(step)
                continue
            cleaned = clean_line(step, field="step")
            if len(cleaned) > STEP_MAX:
                raise ValueError(f"Keep each step under {STEP_MAX} characters.")
            if cleaned:
                steps.append(cleaned)
        return steps


class DirectionsUpdate(DeliveryDirections):
    provider: Provider
    address_id: str = Field(min_length=1, max_length=128)


class SavedDirections(DeliveryDirections):
    provider: Provider
    address_id: str
    updated_at: datetime | None = None


def _require_true(value: bool) -> bool:
    if value is not True:
        raise ValueError("Tick the approval box to continue.")
    return value


class PrepareRequest(BaseModel):
    """The user's tap on "Share with delivery line". Only ids: the server reads the
    address text, name and directions from its own records."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    address_id: str = Field(min_length=1, max_length=128)
    # StrictBool: 1, "true" and "yes" are refused; only JSON true approves.
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def must_be_true(cls, value: bool) -> bool:
        return _require_true(value)


class LineSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_share: StrictBool
    confirmed: StrictBool | None = None

    @model_validator(mode="after")
    def turning_on_needs_approval(self) -> "LineSettingsUpdate":
        if self.auto_share and self.confirmed is not True:
            raise ValueError("Tick the approval box to share automatically.")
        return self


class InboundDeliveryConfig(BaseModel):
    enabled: bool
    reason: DisabledReason | None = None
    inbound_phone_number: str | None
    approved_caller_hint: str | None
    route_directions_enabled: bool = False


class DeliveryLine(BaseModel):
    """What the delivery line was last told for this user. ``ready`` is decided here,
    never by the browser: a completed cohort with exactly one valid record and none rejected."""

    phase: LinePhase = "none"
    provider: Provider | None = None
    address_id: str | None = None
    cohort_id: str | None = None
    # Set when sharing started automatically for one tracked order.
    monitor_id: str | None = None
    reason: RevokeReason | None = None
    error: str | None = None
    auto_share: bool = False
    inbound_phone_number: str | None = None
    updated_at: datetime | None = None
