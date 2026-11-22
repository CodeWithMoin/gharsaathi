"""Voice tools to list and switch between a household's SAVED delivery addresses.

Read-only on the provider: addresses come from the server-side snapshot
(``provider_state``) in live mode, or the static fixture in demo mode. A
selection is validated against that list, stored like
``PUT /api/{swiggy,zepto}/addresses/selected``, moved onto the store's basket
(which supersedes any open bill), and used by this session's later tools.
"""

import json
import logging
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.commerce.provider_state import summarise_address
from app.commerce.swiggy.models import AddressList
from app.shopping.basket import BasketService
from app.shopping.errors import ShoppingError
from app.shopping.models import BasketStatus, ProviderName
from app.voice.tools import ToolOutcome

logger = logging.getLogger(__name__)

LIST_DELIVERY_ADDRESSES = "list_delivery_addresses"
SELECT_DELIVERY_ADDRESS = "select_delivery_address"
ADDRESS_BOOK_TOOL_NAMES = frozenset({LIST_DELIVERY_ADDRESSES, SELECT_DELIVERY_ADDRESS})

STORE_LABELS: dict[str, str] = {"swiggy_instamart": "Swiggy Instamart", "zepto": "Zepto"}
MAX_LISTED = 10
_OPEN_BILL = {BasketStatus.AWAITING_APPROVAL, BasketStatus.APPROVED}

ADDRESS_BOOK_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": LIST_DELIVERY_ADDRESSES,
        "description": (
            "List the user's saved delivery addresses per connected store (label, short "
            "address, address_id, and which one is selected). Use this first whenever the "
            "user wants to change or check the delivery address. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "store": {
                    "type": ["string", "null"],
                    "enum": ["swiggy_instamart", "zepto", None],
                    "description": "Store the user named, or null for every connected store.",
                }
            },
            "required": ["store"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": SELECT_DELIVERY_ADDRESS,
        "description": (
            "Switch a store's delivery address to one of the user's saved addresses, using an "
            "address_id from list_delivery_addresses. Later searches, baskets and bills use "
            "it. An open bill for that store's basket is cancelled and must be requested "
            "again. Does not add or edit addresses and places no order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "store": {
                    "type": "string",
                    "enum": ["swiggy_instamart", "zepto"],
                    "description": "store_id from list_delivery_addresses.",
                },
                "address_id": {
                    "type": "string",
                    "description": "address_id from list_delivery_addresses.",
                },
            },
            "required": ["store", "address_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class AddressBook(Protocol):
    """Saved addresses per store for one household; never calls a provider."""

    async def saved(self, household_id: str, provider: str) -> AddressList | None: ...

    async def select(
        self, household_id: str, provider: str, address_id: str
    ) -> AddressList | None: ...


class AddressSelectionTarget(Protocol):
    """The session that must use the new address for its later tools."""

    def store_address(self, provider: ProviderName) -> str | None: ...

    def has_store(self, provider: ProviderName) -> bool: ...

    def use_address(self, provider: ProviderName, address_id: str) -> None: ...


class ListArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: Literal["swiggy_instamart", "zepto"] | None = None


class SelectArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: Literal["swiggy_instamart", "zepto"]
    address_id: str = Field(min_length=1, max_length=256)


def _error(message: str, status: str = "error") -> ToolOutcome:
    return ToolOutcome(model_output={"status": status, "message": message})


class AddressBookToolExecutor:
    def __init__(
        self,
        book: AddressBook | None,
        baskets: BasketService,
        session: AddressSelectionTarget,
        household_id: str,
    ) -> None:
        self.book = book
        self.baskets = baskets
        self.session = session
        self.household_id = household_id

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        try:
            raw = json.loads(raw_arguments or "{}")
            if name == LIST_DELIVERY_ADDRESSES:
                return await self._list(ListArguments.model_validate(raw))
            if name == SELECT_DELIVERY_ADDRESS:
                return await self._select(SelectArguments.model_validate(raw))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return _error("Invalid arguments for this tool.")
        return _error(f"Unknown tool {name}.")

    async def _saved(self, provider: str) -> AddressList | None:
        if self.book is None:
            return None
        try:
            return await self.book.saved(self.household_id, provider)
        except Exception:  # noqa: BLE001 - a lookup failure reads as "none saved"
            logger.warning("event=voice_address_book_failed provider=%s", provider)
            return None

    def _store_view(self, provider: ProviderName, addresses: AddressList | None) -> dict[str, Any]:
        view: dict[str, Any] = {"store": STORE_LABELS[provider], "store_id": provider}
        if addresses is None or not addresses.addresses:
            view["status"] = "no_saved_addresses"
            view["message"] = (
                f"No saved {STORE_LABELS[provider]} addresses here yet. The user can tap "
                "Refresh in Settings, Delivery addresses to load them once."
            )
            return view
        current = self.session.store_address(provider) or addresses.resolution.default_address_id
        entries = []
        for address in addresses.addresses[:MAX_LISTED]:
            summary = summarise_address(address)
            entry: dict[str, Any] = {
                "address_id": address.id,
                "address": summary.short_address,
                "selected": address.id == current,
            }
            if summary.label:
                entry["label"] = summary.label
            entries.append(entry)
        view["status"] = "ok"
        view["addresses"] = entries
        if addresses.selection_removed and not self.session.store_address(provider):
            view["selection_removed"] = True
            view["message"] = (
                f"The previously selected {STORE_LABELS[provider]} address was removed in the "
                "store app. Say so in one line and ask which saved address to use; never pick one "
                "yourself, even if only one is listed."
            )
        return view

    async def _list(self, arguments: ListArguments) -> ToolOutcome:
        providers: list[ProviderName] = (
            [arguments.store] if arguments.store else ["swiggy_instamart", "zepto"]
        )
        connected = [provider for provider in providers if self.session.has_store(provider)]
        if not connected:
            return _error(
                f"{STORE_LABELS[providers[0]]} is not connected. Ask the user to connect it on screen.",
                "not_connected",
            )
        stores = [self._store_view(provider, await self._saved(provider)) for provider in connected]
        return ToolOutcome(
            model_output={
                "status": "ok",
                "stores": stores,
                "message": (
                    "Name the saved addresses by label in one short line and ask which one to "
                    "use. Offer adding a new address only if the user wants one not listed."
                ),
            }
        )

    async def _select(self, arguments: SelectArguments) -> ToolOutcome:
        provider: ProviderName = arguments.store
        label = STORE_LABELS[provider]
        if not self.session.has_store(provider):
            return _error(f"{label} is not connected. Ask the user to connect it on screen.", "not_connected")
        if self.book is None:
            return _error("Saved addresses are not available here.", "not_connected")
        address_id = arguments.address_id.strip()
        basket = await self.baskets.active(self.household_id, provider)
        if basket.status in {BasketStatus.CHECKOUT_PENDING, BasketStatus.ORDERED}:
            return _error(
                f"Checkout already started for the {label} basket, so its address cannot change now."
            )
        try:
            selected = await self.book.select(self.household_id, provider, address_id)
        except Exception:  # noqa: BLE001
            logger.warning("event=voice_address_select_failed provider=%s", provider)
            selected = None
        match = (
            next((item for item in selected.addresses if item.id == address_id), None)
            if selected is not None
            else None
        )
        if match is None:
            return _error(
                "That address_id is not one of the user's saved addresses. Call "
                "list_delivery_addresses and use an address_id from it.",
                "unknown_address",
            )
        changed = self.session.store_address(provider) != address_id
        self.session.use_address(provider, address_id)
        had_bill = basket.status in _OPEN_BILL and basket.address_id != address_id
        try:
            await self.baskets.set_address(self.household_id, provider, address_id)
        except ShoppingError as exc:
            return _error(str(exc))
        summary = summarise_address(match)
        delivery: dict[str, str] = {"address": summary.short_address}
        if summary.label:
            delivery["label"] = summary.label
        output: dict[str, Any] = {
            "status": "selected",
            "store": label,
            "store_id": provider,
            "delivery_address": delivery,
            "changed": changed,
            "bill_cancelled": had_bill,
            "message": (
                "Confirm the new delivery address in one short line."
                + (" Earlier search results were for the old address; search again before adding." if changed else "")
                + (" The open bill was cancelled; offer to prepare a new one." if had_bill else "")
            ),
        }
        return ToolOutcome(
            model_output=output,
            ui_event={
                "type": "address.selected",
                "provider": provider,
                "address_id": address_id,
                **({"label": summary.label} if summary.label else {}),
                "bill_cancelled": had_bill,
            },
        )
