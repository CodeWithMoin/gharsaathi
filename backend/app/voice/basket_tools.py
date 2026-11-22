"""Voice tools that change local per-store baskets, never a provider cart.

The model is deliberately constrained to opaque ``option_id`` values issued by
the search tool. The store, provider product identifiers and prices are
derived only from the corresponding stored search result.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Protocol, get_args

from pydantic import BaseModel, Field, ValidationError

from app.commerce.provider_state import SavedAddressSummary
from app.commerce.swiggy.models import Product
from app.shopping.approval import ApprovalService, latest_open_approval
from app.shopping.basket import BasketService, item_total_paise
from app.shopping.money import format_rupees, money_fields
from app.shopping.errors import ProviderUnavailable, ShoppingError
from app.shopping.models import (
    Basket,
    BasketLine,
    BasketStatus,
    OfferOpportunity,
    ProductRef,
    ProviderName,
    Quote,
    rupees_to_paise,
)
from app.voice.tools import SearchOption, ToolOutcome


logger = logging.getLogger(__name__)

STORE_IDS: tuple[ProviderName, ...] = get_args(ProviderName)

_STORE_PARAMETER: dict[str, Any] = {
    "type": ["string", "null"],
    "enum": [*STORE_IDS, None],
    "description": (
        "store_id of the basket (swiggy_instamart or zepto). Use null only when "
        "the user did not say which store."
    ),
}

BASKET_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "add_to_basket",
        "description": (
            "Add an option from this conversation's product results to its store's local "
            "basket. Use only option_id returned by search_products; the store comes from "
            "that option. This does not change a provider cart or place an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "option_id": {"type": "string", "description": "An option_id returned by search_products."},
                "quantity": {"type": "integer", "description": "Packs to add, from 1 to 50."},
            },
            "required": ["option_id", "quantity"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "set_basket_quantity",
        "description": (
            "Change a local basket line's quantity. Use item_id and store_id from view_basket. "
            "Quantity zero removes the item. This never places an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "item_id from view_basket."},
                "quantity": {"type": "integer", "description": "New quantity, from 0 to 50."},
                "store": _STORE_PARAMETER,
            },
            "required": ["item_id", "quantity", "store"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "view_basket",
        "description": (
            "Show local baskets and their exact item totals, one per store, with the "
            "selected delivery_address when known. Use store null to see every store's "
            "basket. Fees are not included."
        ),
        "parameters": {
            "type": "object",
            "properties": {"store": _STORE_PARAMETER},
            "required": ["store"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "request_approval",
        "description": (
            "Request one store's final bill for its current basket and show it for on-screen "
            "review. Use only when the user says the basket is ready. If baskets at more "
            "than one store have items, pass the store the user chose. This does not "
            "approve, checkout, or place an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {"store": _STORE_PARAMETER},
            "required": ["store"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

BASKET_TOOL_NAMES = frozenset(tool["name"] for tool in BASKET_TOOL_DEFINITIONS)


@dataclass(frozen=True)
class BasketStore:
    """A store whose local basket voice may change in this session."""

    provider: ProviderName
    label: str
    address_id: str | None


class AddressDescriber(Protocol):
    async def describe(
        self, external_user_id: str, provider: str, address_id: str
    ) -> SavedAddressSummary | None: ...


class AddArguments(BaseModel):
    option_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=50)


class QuantityArguments(BaseModel):
    item_id: str = Field(min_length=1, max_length=256)
    quantity: int = Field(ge=0, le=50)
    store: ProviderName | None = None


class StoreArguments(BaseModel):
    store: ProviderName | None = None


def address_for_model(summary: SavedAddressSummary | None) -> dict[str, Any]:
    """The selected saved address, only when the server-side snapshot has it."""

    if summary is None:
        return {}
    address: dict[str, str] = {"address": summary.short_address}
    if summary.label:
        address["label"] = summary.label
    return {"delivery_address": address}


def basket_for_model(
    basket: Basket, store: BasketStore, address: SavedAddressSummary | None = None
) -> dict[str, Any]:
    return {
        "store": store.label,
        "store_id": store.provider,
        **address_for_model(address),
        "items": [
            {
                "item_id": line.ref.variant_id,
                "name": line.name,
                "pack_size": line.pack_size,
                "quantity": line.quantity,
                **money_fields("line_total", line.unit_price_paise * line.quantity),
                "available": line.available,
            }
            for line in basket.lines
        ],
        **money_fields("item_total", item_total_paise(basket)),
    }


BASKET_NOTE = "Item totals only; quote amount strings exactly. Fees and the final payable amount require an approval quote. Nothing was ordered."
OFFERS_NOTE = (
    "bill_offers are the only offers the store confirmed for this exact bill; discounts are "
    "already in charges. There is no tool to enter a promo or coupon code. Never invent "
    "offers, codes or savings."
)


def _offer_status(offer: OfferOpportunity) -> str:
    if offer.applied:
        return "applied"
    return "unlocked" if offer.unlocked else "not_unlocked"


def offers_for_model(quote: Quote) -> list[dict[str, Any]]:
    """Provider-confirmed offers on a quote, with amounts formatted from integer paise."""

    offers: list[dict[str, Any]] = []
    for offer in quote.offers:
        if not offer.provider_confirmed:
            continue
        entry: dict[str, Any] = {
            "title": offer.title,
            "kind": offer.kind,
            "status": _offer_status(offer),
        }
        if offer.potential_saving_paise:
            entry.update(money_fields("possible_saving", offer.potential_saving_paise))
        if not offer.unlocked and offer.remaining_paise:
            entry.update(money_fields("add_more_to_unlock", offer.remaining_paise))
        offers.append(entry)
    return offers


def bill_for_model(quote: Quote) -> dict[str, Any]:
    return {
        **money_fields("payable", quote.payable_paise),
        "charges": [
            {"label": charge.label, **money_fields("amount", charge.amount_paise)}
            for charge in quote.charges
        ],
        "bill_offers": offers_for_model(quote),
        "promo_code_entry": "not_available",
        **({"delivery_eta": quote.eta_text} if quote.eta_text else {}),
    }


def _line_from_product(provider: ProviderName, product: Product, quantity: int) -> BasketLine:
    """Build the same line the on-screen Add button sends for this product."""

    unit_price = rupees_to_paise(product.price.offer_price)
    mrp = rupees_to_paise(product.price.mrp)
    return BasketLine(
        ref=ProductRef(
            provider=provider,
            product_id=product.product_id,
            variant_id=product.spin_id,
            sku_id=product.sku_id,
        ),
        name=product.name,
        brand=product.brand,
        pack_size=product.pack_size,
        unit_price_paise=unit_price,
        mrp_paise=mrp if mrp > unit_price else None,
        quantity=quantity,
        available=product.available,
    )


class BasketToolExecutor:
    def __init__(
        self,
        baskets: BasketService,
        approvals: ApprovalService,
        options: dict[str, SearchOption],
        stores: list[BasketStore],
        household_id: str,
        addresses: AddressDescriber | None = None,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.options = options
        self.stores = {store.provider: store for store in stores}
        self.household_id = household_id
        self.addresses = addresses
        self._address_cache: dict[tuple[str, str], SavedAddressSummary | None] = {}
        # The latest bill this session requested per store, so view_basket can
        # answer offer questions without re-quoting (which would supersede it).
        self._quotes: dict[ProviderName, Quote] = {}

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return _error("Invalid arguments.")
        try:
            if name == "add_to_basket":
                return await self._add(AddArguments.model_validate(arguments))
            if name == "set_basket_quantity":
                return await self._set_quantity(QuantityArguments.model_validate(arguments))
            if name == "view_basket":
                return await self._view(StoreArguments.model_validate(arguments))
            if name == "request_approval":
                return await self._request_approval(StoreArguments.model_validate(arguments))
        except ProviderUnavailable as exc:
            if name == "request_approval":
                return bill_failed(exc)
            return _error(str(exc))
        except ValidationError:
            return _error("Invalid arguments for this tool.")
        except ShoppingError as exc:
            return _error(str(exc))
        return _error(f"Unknown tool {name}.")

    def _store(self, provider: ProviderName) -> BasketStore | None:
        return self.stores.get(provider)

    async def _active(self, store: BasketStore) -> Basket:
        return await self.baskets.active(self.household_id, store.provider)

    async def _address(self, store: BasketStore) -> SavedAddressSummary | None:
        if self.addresses is None or not store.address_id:
            return None
        key = (store.provider, store.address_id)
        if key not in self._address_cache:
            try:
                self._address_cache[key] = await self.addresses.describe(
                    self.household_id, store.provider, store.address_id
                )
            except Exception:
                logger.warning(
                    "event=voice_address_lookup_failed provider=%s", store.provider
                )
                return None
        return self._address_cache[key]

    async def _current_bill(self, basket: Basket) -> dict[str, Any]:
        """The bill on screen for this basket, without re-quoting.

        Prefers the quote this session requested; otherwise reads the persisted
        approval, e.g. a bill opened with the on-screen "Get final bill" button.
        """

        quote = self._quotes.get(basket.provider)
        if (
            quote is not None
            and quote.basket_id == basket.id
            and quote.basket_revision == basket.revision
            and basket.status == BasketStatus.AWAITING_APPROVAL
        ):
            return {"current_bill": bill_for_model(quote)}
        if basket.status not in {BasketStatus.AWAITING_APPROVAL, BasketStatus.APPROVED}:
            return {}
        try:
            approval = latest_open_approval(
                await self.approvals.approvals.for_basket(basket.id), basket
            )
        except Exception:  # noqa: BLE001 - a missing bill must not break view_basket
            logger.warning("event=voice_bill_lookup_failed provider=%s", basket.provider)
            return {}
        if approval is None or approval.quote is None:
            return {}
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:  # SQLite returns naive UTC timestamps
            expires_at = expires_at.replace(tzinfo=UTC)
        expired = self.approvals.clock() >= expires_at
        return {
            "current_bill": {
                **bill_for_model(approval.quote),
                **money_fields("item_total", approval.quote.item_total_paise),
                "approval_status": "expired" if expired else approval.status.value,
                "expires_at": expires_at.isoformat(),
            }
        }

    async def _outcome(self, basket: Basket, store: BasketStore, status: str) -> ToolOutcome:
        return _basket_outcome(
            basket, store, status, await self._address(store), await self._current_bill(basket)
        )

    async def _add(self, arguments: AddArguments) -> ToolOutcome:
        option = self.options.get(arguments.option_id)
        if option is None:
            return _error("Unknown option_id. Search again and use an option_id from the results.")
        store = self._store(option.provider)
        if store is None:
            return _error(f"{option.store} is not connected for voice, so nothing was added.")
        product = option.product
        if not product.available:
            return _error(f"{product.name} is unavailable right now, so it was not added.")
        if store.address_id:
            await self.baskets.set_address(self.household_id, store.provider, store.address_id)
        basket = await self.baskets.add_line(
            self.household_id,
            store.provider,
            _line_from_product(store.provider, product, arguments.quantity),
        )
        return await self._outcome(basket, store, "added")

    async def _set_quantity(self, arguments: QuantityArguments) -> ToolOutcome:
        if arguments.store is not None:
            store = self._store(arguments.store)
            if store is None:
                return _not_connected(arguments.store)
        else:
            matches = [
                store
                for store in self.stores.values()
                if any(line.ref.variant_id == arguments.item_id for line in (await self._active(store)).lines)
            ]
            if not matches:
                return _error("Unknown item_id. Use view_basket and pass an item_id from it.")
            if len(matches) > 1:
                return _ambiguous("Which store's basket should change", matches)
            store = matches[0]
        basket = await self.baskets.set_quantity(
            self.household_id, store.provider, arguments.item_id, arguments.quantity
        )
        return await self._outcome(basket, store, "updated")

    async def _view(self, arguments: StoreArguments) -> ToolOutcome:
        if arguments.store is not None:
            store = self._store(arguments.store)
            if store is None:
                return _not_connected(arguments.store)
            return await self._view_one(store)
        if len(self.stores) == 1:
            return await self._view_one(next(iter(self.stores.values())))
        views = [(store, await self._active(store)) for store in self.stores.values()]
        filled = [(store, basket) for store, basket in views if basket.lines]
        bills = [await self._current_bill(basket) for _, basket in views]
        return ToolOutcome(
            model_output={
                "status": "ok",
                "baskets": [
                    {
                        **basket_for_model(basket, store, await self._address(store)),
                        **bill,
                    }
                    for (store, basket), bill in zip(views, bills)
                ],
                "note": BASKET_NOTE
                + " Each store has its own basket and bill."
                + (f" {OFFERS_NOTE}" if any(bills) else ""),
            },
            ui_event=(
                {
                    "type": "basket.updated",
                    "provider": filled[0][0].provider,
                    "basket": _basket_ui(filled[0][1], filled[0][0]),
                }
                if len(filled) == 1
                else None
            ),
        )

    async def _view_one(self, store: BasketStore) -> ToolOutcome:
        basket = await self._active(store)
        outcome = await self._outcome(basket, store, "ok")
        if "current_bill" in outcome.model_output:
            outcome.model_output["note"] = f"{BASKET_NOTE} {OFFERS_NOTE}"
        return outcome

    async def _request_approval(self, arguments: StoreArguments) -> ToolOutcome:
        if arguments.store is not None:
            store = self._store(arguments.store)
            if store is None:
                return _not_connected(arguments.store)
        else:
            filled = [
                store for store in self.stores.values() if (await self._active(store)).lines
            ]
            if not filled:
                return _error("The basket is empty.")
            if len(filled) > 1:
                return _ambiguous("Which store's bill should be prepared", filled)
            store = filled[0]
        if not store.address_id:
            return _error(
                f"No {store.label} delivery address is selected. Ask the user to choose one on screen."
            )
        basket = await self.baskets.set_address(self.household_id, store.provider, store.address_id)
        if not basket.lines:
            return _error(f"The {store.label} basket is empty.")
        result = await self.approvals.request(basket.id)
        quote = result.quote
        self._quotes[store.provider] = quote
        # Imported here: app.api.basket -> app.dependencies -> app.voice is a cycle.
        from app.api.basket import approval_request_out

        return ToolOutcome(
            model_output={
                "status": "awaiting_user_approval",
                "store": store.label,
                "store_id": store.provider,
                **address_for_model(await self._address(store)),
                **bill_for_model(quote),
                "note": (
                    "The exact bill is on screen. Ask the user to review it and choose an action there. "
                    f"No order has been approved or placed. {OFFERS_NOTE}"
                ),
            },
            ui_event={
                "type": "basket.approval_requested",
                "provider": store.provider,
                "store": store.label,
                "approval": {
                    "id": result.approval.id,
                    "basket_id": result.approval.basket_id,
                    "basket_revision": result.approval.basket_revision,
                    "fingerprint": result.approval.fingerprint,
                    "payable": format_rupees(result.approval.payable_paise),
                    "expires_at": result.approval.expires_at.isoformat(),
                },
                "quote": {
                    "item_total": format_rupees(quote.item_total_paise),
                    "payable": format_rupees(quote.payable_paise),
                    "charges": [
                        {"label": charge.label, "amount": format_rupees(charge.amount_paise)}
                        for charge in quote.charges
                    ],
                    "eta_text": quote.eta_text,
                },
                "basket": _basket_ui(result.basket, store),
                # Same shape as POST /api/basket/approval so the basket panel can
                # show this exact bill without requesting (and superseding) a new one.
                "request": approval_request_out(result).model_dump(mode="json"),
            },
        )


def _basket_ui(basket: Basket, store: BasketStore) -> dict[str, Any]:
    return {
        "id": basket.id,
        "provider": store.provider,
        "store": store.label,
        "revision": basket.revision,
        "status": basket.status.value,
        "item_count": sum(line.quantity for line in basket.lines),
        "item_total": format_rupees(item_total_paise(basket)),
        "lines": [
            {"item_id": line.ref.variant_id, "name": line.name, "quantity": line.quantity}
            for line in basket.lines
        ],
    }


def _basket_outcome(
    basket: Basket,
    store: BasketStore,
    status: str,
    address: SavedAddressSummary | None = None,
    current_bill: dict[str, Any] | None = None,
) -> ToolOutcome:
    return ToolOutcome(
        model_output={
            "status": status,
            **basket_for_model(basket, store, address),
            **(current_bill or {}),
            "note": BASKET_NOTE,
        },
        ui_event={"type": "basket.updated", "provider": store.provider, "basket": _basket_ui(basket, store)},
    )


def _ambiguous(question: str, stores: list[BasketStore]) -> ToolOutcome:
    labels = " or ".join(store.label for store in stores)
    return ToolOutcome(
        model_output={
            "status": "needs_store",
            "stores": [{"store": store.label, "store_id": store.provider} for store in stores],
            "message": f"{question}: {labels}? Ask the user, then call again with that store_id.",
        }
    )


def _not_connected(provider: ProviderName) -> ToolOutcome:
    label = "Zepto" if provider == "zepto" else "Swiggy Instamart"
    return _error(f"{label} is not connected for voice. Ask the user to connect it on screen.")


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(model_output={"status": "error", "message": message})


BILL_FAILED_NOTE = (
    "The store could not prepare or verify this bill; the basket is unchanged and nothing "
    "was ordered. Say the reason in one short line. Do not change the basket yourself and "
    "do not suggest basket edits as a workaround (no removing, re-adding or ordering fewer "
    "packs to test the bill) unless the reason names an item problem such as out of stock "
    "or a quantity limit. Otherwise offer only: try again in a little while, or use the "
    "store's app."
)


def bill_failed(exc: ProviderUnavailable) -> ToolOutcome:
    """A provider or checking failure on a bill: report it, never work around it."""

    return ToolOutcome(
        model_output={"status": "bill_failed", "reason": str(exc), "message": BILL_FAILED_NOTE}
    )
