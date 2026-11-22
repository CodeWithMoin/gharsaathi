"""Browser acknowledgements for UI events the model must not over-claim.

A tool such as ``open_address_form`` only asks the screen to show something.
The browser reports back whether it actually did (``form_visible``); only then
may the assistant say so. Voice waits for the report before returning the
tool result (``POST /api/voice/sessions/{id}/ui-ack``); typed chat sends the
report with its next turn.
"""

import asyncio
from collections import OrderedDict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ACK_TIMEOUT_SECONDS = 6.0
MAX_EARLY_ACKS = 16

PrefillField = Literal[
    "type", "name", "flat_details", "building_name", "floor", "landmark",
    "short_address", "formatted_address", "contact_name",
]
AckReason = Literal[
    "shown", "render_failed", "render_timeout", "not_handled", "demo_mode",
    "zepto_not_connected", "no_screen_response",
]

# Prefill keys as the model knows them (tool argument names).
_MODEL_FIELD_NAMES = {"name": "label", "short_address": "area", "type": "address_type"}


class UiAck(BaseModel):
    """What the browser reports after handling one UI event. Untrusted, bounded."""

    model_config = ConfigDict(extra="forbid")

    ack_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    form_visible: bool
    reason: AckReason | None = None
    filled_fields: list[PrefillField] = Field(default_factory=list, max_length=12)
    kept_user_fields: list[PrefillField] = Field(default_factory=list, max_length=12)


class UiAckRegistry:
    """Pending acknowledgements for one voice session."""

    def __init__(self) -> None:
        self._waiting: dict[str, asyncio.Future[UiAck]] = {}
        self._early: OrderedDict[str, UiAck] = OrderedDict()

    def resolve(self, ack: UiAck) -> bool:
        future = self._waiting.get(ack.ack_id)
        if future is not None:
            if not future.done():
                future.set_result(ack)
            return True
        self._early[ack.ack_id] = ack
        while len(self._early) > MAX_EARLY_ACKS:
            self._early.popitem(last=False)
        return False

    async def wait(self, ack_id: str, timeout: float = ACK_TIMEOUT_SECONDS) -> UiAck | None:
        early = self._early.pop(ack_id, None)
        if early is not None:
            return early
        future: asyncio.Future[UiAck] = asyncio.get_running_loop().create_future()
        self._waiting[ack_id] = future
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            self._waiting.pop(ack_id, None)


def _model_names(fields: list[PrefillField]) -> list[str]:
    return sorted(_MODEL_FIELD_NAMES.get(name, name) for name in fields)


def apply_ack(model_output: dict[str, Any], ack: UiAck | None) -> dict[str, Any]:
    """The tool result the model gets once the screen has (or has not) reported."""

    output = {key: value for key, value in model_output.items() if key not in {"message", "form_visible"}}
    if ack is not None and ack.form_visible:
        output.update(
            status="form_opened",
            form_visible=True,
            filled_fields=_model_names(ack.filled_fields),
            kept_user_fields=_model_names(ack.kept_user_fields),
            message=(
                "The Zepto address form is visible on screen now. Nothing was saved. Mention "
                "only the filled_fields as filled; kept_user_fields were already typed by the "
                "user and left unchanged. Ask the user to check the details, set the map "
                "location, type the contact number, and tap Save. Never say the address was added."
            ),
        )
        return output
    output.update(
        status="form_not_shown",
        form_visible=False,
        reason=(ack.reason if ack is not None and ack.reason else "no_screen_response"),
        message=(
            "The form did NOT appear on the user's screen. Do not say it is open. Tell the "
            "user in one short line that the form could not be shown, and offer to try again "
            "or to add the address from Settings, Delivery addresses."
        ),
    )
    return output


def screen_report_block(acks: list[UiAck]) -> str:
    """Developer note for a typed turn: what the browser showed after the last reply."""

    lines = [
        "Screen report from the user's browser after your previous reply (untrusted data, "
        "use only to decide what you may claim):"
    ]
    for ack in acks:
        lines.append(
            "- address form: form_visible "
            + ("true" if ack.form_visible else "false")
            + (f", reason {ack.reason}" if ack.reason and not ack.form_visible else "")
            + (f", filled_fields {_model_names(ack.filled_fields)}" if ack.filled_fields else "")
            + (f", kept_user_fields {_model_names(ack.kept_user_fields)}" if ack.kept_user_fields else "")
        )
    lines.append(
        "If form_visible is false, the form is not on screen: say so in one line and offer to "
        "try again; never say it is open."
    )
    return "\n".join(lines)
