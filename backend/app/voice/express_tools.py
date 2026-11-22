"""One-call shopping: express order ("2 Amul lassi order kar do") and quick add.

``express_order`` runs the existing safe steps in code: search each item (with
household rules and memory annotations), pick only a confident match, put it
in the store's open basket, then ``prepare_order`` for the order card. It never
approves or places anything; only the user's tap on the card does.

A confident pick is, among in-stock options that match every product word of
the query (and its pack size when given), either
- the one remembered product (this household's past purchase or confirmed
  preference), or
- the one product whose brand the user named.
Anything else is ``needs_choice``: the options go on screen and the model asks
one question. ``quick_add`` uses the same pick for plain add requests and
stops after the add.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.memory.recall import item_terms, term_matcher
from app.memory.remembered import _UNIT_WORDS, name_tokens, pack_key
from app.shopping.errors import ShoppingError
from app.shopping.models import Basket, PaymentMethod, ProviderName
from app.shopping.money import money_fields
from app.voice.basket_tools import (
    BasketStore,
    _basket_ui,
    _line_from_product,
    basket_for_model,
)
from app.voice.order_tools import _PAYMENT_PARAMETER
from app.voice.tools import MultiProviderShoppingToolExecutor, ToolOutcome

if TYPE_CHECKING:
    from app.voice.toolbox import VoiceToolbox


MAX_ITEMS = 5
MAX_CHOICE_OPTIONS = 4
ORDER_STORE: ProviderName = "zepto"

_ITEMS_PARAMETER: dict[str, Any] = {
    "type": "array",
    "description": "Each item the user named, 1 to 5.",
    "items": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Short English product query with the brand, variant and pack size the user "
                    "gave, without order words or the count, e.g. 'Amul lassi', 'Amul Taaza milk "
                    "1 litre', 'milk' for 'usual doodh'."
                ),
            },
            "quantity": {"type": "integer", "description": "Packs, 1 to 50; 1 unless the user said a number."},
        },
        "required": ["query", "quantity"],
        "additionalProperties": False,
    },
}

EXPRESS_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "express_order",
        "description": (
            "Use when the user explicitly asks to ORDER specific items ('2 Amul lassi order kar "
            "do', 'usual milk mangwa do', 'doodh aur chai patti order karo'). In one call it "
            "searches each item, picks only a confident match (the household's remembered/usual "
            "product, or the single in-stock product of the brand and size the user named), adds "
            "it to the Zepto basket (merging with what is already there), gets the bill and "
            "shows the order card. Returns ready (card on screen), needs_choice (one item needs "
            "the user's pick; its options are on screen) or failed. Never approves, pays or "
            "places an order: only the user's tap on the card does. Zepto only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": _ITEMS_PARAMETER,
                "store": {
                    "type": ["string", "null"],
                    "enum": ["swiggy_instamart", "zepto", None],
                    "description": "Store the user named, else null (Zepto).",
                },
                "payment_method": _PAYMENT_PARAMETER,
            },
            "required": ["items", "store", "payment_method"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "quick_add",
        "description": (
            "Use for a plain add request that names items ('doodh add karo', 'usual atta daal "
            "do', 'Tata Tea Gold 500 g bhi add karo'). Adds only confident matches (the "
            "remembered/usual product in stock, or the single product of the brand and size "
            "named) to that store's basket; otherwise returns needs_choice with the options on "
            "screen. The result includes the basket; do not call view_basket after it. Never "
            "orders."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": _ITEMS_PARAMETER,
                "store": {
                    "type": ["string", "null"],
                    "enum": ["swiggy_instamart", "zepto", None],
                    "description": "Store the user named, else null.",
                },
            },
            "required": ["items", "store"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

EXPRESS_TOOL_NAMES = frozenset(tool["name"] for tool in EXPRESS_TOOL_DEFINITIONS)

READY_NOTE = (
    "The order card is on screen. Nothing is approved, paid or ordered yet; only the user's "
    "tap does that. Reply in ONE line: the items with quantities (item_summary), the bill (bill_breakdown "
    "when present, else payable) exactly as given, the payment method label and the "
    "delivery address label, then \"Place order dabaiye\". If also_in_basket is not empty, "
    "add that those items are in the same order. No question."
)
CHOICE_NOTE = (
    "Confident items (added) are already in the basket. The item in 'choice' needs the user's "
    "pick; its options are on screen. Ask ONE short question naming at most two or three "
    "choices by variant and size (no prices unless asked). When the user picks, call {tool} "
    "again with that exact product name and size (and the same other items); never add a "
    "guess."
)
FAILED_NOTE = (
    "Say the reason in one line from message. Nothing was ordered. Do not retry in this turn."
)
ADDED_NOTE = (
    "Added without asking because the match was confident. Reply in one line: product, size, "
    "price and store from added, then 'change karna ho to bolo'. No question. Do not call "
    "view_basket."
)

_EXTRA_STOPWORDS = {
    "usual", "regular", "hamesha", "wala", "wali", "wale", "order", "mangwa", "mangwado",
    "mangwao", "daal", "dalo", "add", "karo", "bhi", "ek", "do", "teen", "char", "chaar",
    "paanch", "packet", "packets", "pack", "packs", "piece", "pieces", "bottle", "bottles",
}


class ExpressItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=120)
    quantity: int = Field(ge=1, le=50)


class ExpressArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExpressItem] = Field(min_length=1, max_length=MAX_ITEMS)
    store: ProviderName | None = None
    payment_method: PaymentMethod | None = None


def query_words(query: str) -> list[str]:
    """The product words of a query that every picked option must contain."""

    words = []
    for word in re.split(r"[^\w]+", query.casefold()):
        if not word or word in _UNIT_WORDS or word in _EXTRA_STOPWORDS or any(c.isdigit() for c in word):
            continue
        if item_terms(word):
            words.append(word)
    return words


def matches_query(query: str, result: dict[str, Any]) -> bool:
    words = query_words(query)
    haystack = f"{result.get('brand') or ''} {result.get('name') or ''}"
    if not words or not all(term_matcher(item_terms(word))(haystack) for word in words):
        return False
    wanted = pack_key(query)
    return wanted is None or wanted == _size(result)


def _size(result: dict[str, Any]) -> tuple[int, float, str] | None:
    return pack_key(f"{result.get('pack_size') or ''} {result.get('name') or ''}")


def _product_key(result: dict[str, Any]) -> tuple[frozenset[str], Any]:
    return frozenset(name_tokens(f"{result.get('brand') or ''} {result.get('name') or ''}")), _size(result)


def _remembered_rank(result: dict[str, Any]) -> tuple[int, int]:
    remembered = result.get("remembered") or {}
    return (1 if remembered.get("source") == "confirmed_preference" else 0, int(remembered.get("times") or 0))


@dataclass
class Pick:
    """How one requested item was resolved."""

    item: ExpressItem
    status: Literal["picked", "choice", "failed"]
    option_id: str | None = None
    reason: str = ""
    message: str = ""
    search: dict[str, Any] = field(default_factory=dict)
    ui_event: dict[str, Any] | None = None
    picked_because: str = ""


def pick_option(
    query: str, results: list[dict[str, Any]], filled_stores: set[str] | None = None
) -> tuple[str | None, str, str]:
    """(option_id, reason, because). option_id None means the user must choose; reason says why."""

    usable = [r for r in results if r.get("option_id") and r.get("available") and not r.get("avoided")]
    relevant = [r for r in usable if matches_query(query, r)]
    if not relevant:
        if any(matches_query(query, r) for r in results if r.get("avoided")):
            return None, "avoided", ""
        if any(matches_query(query, r) for r in results if not r.get("available")):
            return None, "not_available", ""
        return None, "no_exact_match", ""
    remembered = [r for r in relevant if r.get("remembered")]
    if remembered:
        best = max(_remembered_rank(r) for r in remembered)
        top = {_product_key(r) for r in remembered if _remembered_rank(r) == best}
        if len(top) == 1:
            return _cheapest([r for r in relevant if _product_key(r) in top], filled_stores), "", "remembered"
        return None, "several_matches", ""
    named = [
        r
        for r in relevant
        if r.get("brand") and name_tokens(str(r["brand"])) <= name_tokens(query)
    ]
    products = {_product_key(r) for r in named}
    if len(products) == 1:
        return _cheapest(named, filled_stores), "", "named_brand_and_size"
    if len({_product_key(r) for r in relevant}) > 1:
        return None, "several_matches", ""
    return None, "confirm_single", ""


def _cheapest(options: list[dict[str, Any]], filled_stores: set[str] | None) -> str:
    filled = filled_stores or set()
    best = min(
        options,
        key=lambda r: (int(r.get("price_paise") or 0), 0 if r.get("store_id") in filled else 1, r.get("position", 0)),
    )
    return str(best["option_id"])


REASON_MESSAGES = {
    "several_matches": "More than one product matches; the user must pick.",
    "no_exact_match": "No option matches every word the user said; the user must pick or say more.",
    "confirm_single": (
        "One product matches, but the user did not name its brand (and size); confirm it in "
        "one short question."
    ),
    "not_available": "The matching product is out of stock right now.",
    "avoided": "The matching product is one the household's saved rule avoids; ask once before adding it.",
}


class ExpressToolExecutor:
    def __init__(self, toolbox: VoiceToolbox) -> None:
        self.toolbox = toolbox

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
            if name == "quick_add":
                arguments.setdefault("payment_method", None)
            parsed = ExpressArguments.model_validate(arguments)
        except (json.JSONDecodeError, ValueError, ValidationError):
            return _failed("invalid_arguments", "Invalid arguments: 1 to 5 items, each a query and a quantity from 1 to 50.")
        if name == "express_order":
            return await self._express(parsed)
        if name == "quick_add":
            return await self._quick_add(parsed)
        return _failed("unknown_tool", f"Unknown tool {name}.")

    # ------------------------------------------------------------ steps

    def _stores(self, provider: ProviderName | None) -> list[BasketStore]:
        stores = self.toolbox.basket.stores
        if provider is not None:
            return [stores[provider]] if provider in stores else []
        return list(stores.values())

    def _searcher(self, stores: list[BasketStore]) -> MultiProviderShoppingToolExecutor:
        wanted = {store.provider for store in stores}
        search = self.toolbox.search
        return MultiProviderShoppingToolExecutor(
            [target for target in search.targets if target.provider in wanted],
            search.options,
            memory=search.memory,
            household_id=search.household_id,
            provider_timeout=search.provider_timeout,
            provider_hard_timeout=search.provider_hard_timeout,
        )

    async def _resolve(self, stores: list[BasketStore], items: list[ExpressItem]) -> list[Pick]:
        searcher = self._searcher(stores)
        filled = {
            store.provider for store in stores if (await self.toolbox.basket._active(store)).lines
        }
        outcomes = await asyncio.gather(
            *(searcher.execute("search_products", json.dumps({"query": item.query})) for item in items)
        )
        picks: list[Pick] = []
        for item, outcome in zip(items, outcomes):
            output = outcome.model_output
            status = output.get("status")
            if status == "not_a_product_query":
                # A brand alone ("Surf Excel"): the guard's one clarifying question.
                picks.append(
                    Pick(item, "choice", reason="needs_product", message=str(output.get("message") or ""), search=output)
                )
                continue
            if status != "ok":
                picks.append(
                    Pick(
                        item,
                        "failed",
                        reason=str(output.get("status") or "error"),
                        message=str(output.get("message") or "The store could not be searched."),
                        search=output,
                        ui_event=outcome.ui_event,
                    )
                )
                continue
            option_id, reason, because = pick_option(item.query, output.get("results", []), filled)
            if option_id is None:
                picks.append(
                    Pick(item, "choice", reason=reason, message=REASON_MESSAGES[reason], search=output, ui_event=outcome.ui_event)
                )
            else:
                picks.append(Pick(item, "picked", option_id=option_id, search=output, picked_because=because))
        return picks

    async def _add(self, pick: Pick, *, set_quantity: bool) -> tuple[BasketStore, Basket, dict[str, Any]]:
        toolbox = self.toolbox
        option = toolbox.search.options[pick.option_id or ""]
        store = toolbox.basket.stores[option.provider]
        household = toolbox.basket.household_id
        if store.address_id:
            await toolbox.basket.baskets.set_address(household, store.provider, store.address_id)
        line = _line_from_product(store.provider, option.product, pick.item.quantity)
        current = await toolbox.basket._active(store)
        existing = next((l for l in current.lines if l.ref.variant_id == line.ref.variant_id), None)
        if set_quantity and existing is not None:
            basket = await toolbox.basket.baskets.set_quantity(
                household, store.provider, line.ref.variant_id, pick.item.quantity
            )
        else:
            basket = await toolbox.basket.baskets.add_line(household, store.provider, line)
        # The same memory hooks as add_to_basket (repeated-choice signals).
        toolbox.choices.saw_search(json.dumps({"query": pick.item.query}))
        await toolbox.choices.after_add(
            json.dumps({"option_id": pick.option_id}), ToolOutcome(model_output={"status": "added"})
        )
        quantity = next(l.quantity for l in basket.lines if l.ref.variant_id == line.ref.variant_id)
        added = {
            "name": option.product.name,
            "pack_size": option.product.pack_size,
            "quantity": quantity,
            "store": store.label,
            "store_id": store.provider,
            **money_fields("line_total", line.unit_price_paise * quantity),
            "picked_because": pick.picked_because,
        }
        return store, basket, added

    async def _add_all(
        self, picks: list[Pick], *, set_quantity: bool
    ) -> tuple[list[dict[str, Any]], dict[ProviderName, tuple[BasketStore, Basket]]]:
        added: list[dict[str, Any]] = []
        baskets: dict[ProviderName, tuple[BasketStore, Basket]] = {}
        for pick in picks:
            if pick.status != "picked":
                continue
            store, basket, entry = await self._add(pick, set_quantity=set_quantity)
            added.append(entry)
            baskets[store.provider] = (store, basket)
        return added, baskets

    # ------------------------------------------------------------ tools

    async def _express(self, arguments: ExpressArguments) -> ToolOutcome:
        provider = arguments.store or ORDER_STORE
        if provider != ORDER_STORE:
            return _failed(
                "unsupported_store",
                "Swiggy Instamart orders cannot be placed from GharSaathi yet; only Zepto. Offer "
                "to add the items to the Instamart basket (quick_add) for a bill review. Nothing "
                "was ordered.",
            )
        stores = self._stores(provider)
        if not stores:
            return _failed("not_connected", "Zepto is not connected. Ask the user to connect it on screen.")
        store = stores[0]
        if not store.address_id:
            return _failed(
                "no_address",
                "No Zepto delivery address is selected. Ask the user to choose one "
                "(list_delivery_addresses). Nothing was added or ordered.",
            )
        before = await self.toolbox.basket._active(store)
        picks = await self._resolve(stores, arguments.items)
        failure = _store_failure(picks)
        if failure is not None:
            return failure
        try:
            added, baskets = await self._add_all(picks, set_quantity=True)
        except ShoppingError as exc:
            return _failed("basket_refused", f"{exc} Nothing was ordered.")
        requested = {
            self.toolbox.search.options[p.option_id].product.spin_id
            for p in picks
            if p.status == "picked" and p.option_id
        }
        also = [
            {"name": line.name, "pack_size": line.pack_size, "quantity": line.quantity}
            for line in before.lines
            if line.ref.variant_id not in requested
        ]
        events = [
            {"type": "basket.updated", "provider": s.provider, "basket": _basket_ui(b, s)}
            for s, b in baskets.values()
        ]
        unresolved = [pick for pick in picks if pick.status != "picked"]
        if unresolved:
            return _choice("express_order", added, unresolved, events, also)
        prepared = await self.toolbox.order.execute(
            "prepare_order",
            json.dumps({"store": provider, "payment_method": arguments.payment_method}),
        )
        order = prepared.model_output
        if order.get("status") != "awaiting_tap":
            return ToolOutcome(
                model_output={
                    "status": "failed",
                    "reason": str(order.get("status") or "error"),
                    "added": added,
                    "order": order,
                    "message": (
                        "The items were put in the Zepto basket, but the order card could not be "
                        f"prepared: {order.get('reason') or order.get('message') or order.get('status')}"
                    ),
                    "note": FAILED_NOTE,
                },
                ui_event=prepared.ui_event or (events[-1] if events else None),
                extra_ui_events=events if prepared.ui_event else events[:-1],
            )
        return ToolOutcome(
            model_output={
                **{key: value for key, value in order.items() if key != "note"},
                "status": "ready",
                "added": added,
                "also_in_basket": also,
                "note": READY_NOTE,
            },
            ui_event=prepared.ui_event,
            extra_ui_events=events,
        )

    async def _quick_add(self, arguments: ExpressArguments) -> ToolOutcome:
        stores = self._stores(arguments.store)
        if not stores:
            return _failed("not_connected", "That store is not connected. Ask the user to connect it on screen.")
        if not any(store.address_id for store in stores):
            return _failed("no_address", "No delivery address is selected. Ask the user to choose one on screen.")
        picks = await self._resolve(stores, arguments.items)
        failure = _store_failure(picks)
        if failure is not None:
            return failure
        try:
            added, baskets = await self._add_all(picks, set_quantity=False)
        except ShoppingError as exc:
            return _failed("basket_refused", str(exc))
        events = [
            {"type": "basket.updated", "provider": s.provider, "basket": _basket_ui(b, s)}
            for s, b in baskets.values()
        ]
        unresolved = [pick for pick in picks if pick.status != "picked"]
        if unresolved:
            return _choice("quick_add", added, unresolved, events, [])
        return ToolOutcome(
            model_output={
                "status": "added",
                "added": added,
                "baskets": [
                    basket_for_model(basket, store) for store, basket in baskets.values()
                ],
                "note": ADDED_NOTE,
            },
            ui_event=events[-1] if events else None,
            extra_ui_events=events[:-1],
        )


def _store_failure(picks: list[Pick]) -> ToolOutcome | None:
    """A store-level problem (not connected, unreachable, address gone) stops everything."""

    store_level = [p for p in picks if p.status == "failed" and p.reason in {"not_connected", "provider_error"}]
    every_failed = all(p.status == "failed" for p in picks)
    if not store_level and not every_failed:
        return None
    first = (store_level or picks)[0]
    reason = first.reason
    unreachable = first.search.get("unreachable_stores") or []
    if any(entry.get("reason") == "address_invalid" for entry in unreachable):
        reason = "address_invalid"
    return ToolOutcome(
        model_output={
            "status": "failed",
            "reason": reason,
            "item": first.item.query,
            **({"unreachable_stores": unreachable} if unreachable else {}),
            "message": first.message + " Nothing was added or ordered.",
            "note": FAILED_NOTE,
        },
        ui_event=first.ui_event,
    )


def _choice(
    tool: str,
    added: list[dict[str, Any]],
    unresolved: list[Pick],
    events: list[dict[str, Any]],
    also: list[dict[str, Any]],
) -> ToolOutcome:
    first = next((p for p in unresolved if p.status == "choice"), unresolved[0])
    results = first.search.get("results", [])
    relevant = [r for r in results if matches_query(first.item.query, r)] or results
    options = [
        {
            key: r[key]
            for key in ("option_id", "position", "provider", "store_id", "name", "pack_size", "price", "available", "remembered", "avoided", "avoided_because")
            if key in r
        }
        for r in relevant[:MAX_CHOICE_OPTIONS]
    ]
    return ToolOutcome(
        model_output={
            "status": "needs_choice",
            "added": added,
            **({"also_in_basket": also} if also else {}),
            "choice": {
                "item": first.item.query,
                "quantity": first.item.quantity,
                "reason": first.reason,
                "message": first.message,
                "options": options,
            },
            "still_open": [
                {"item": p.item.query, "reason": p.reason} for p in unresolved if p is not first
            ],
            # Typed chat keeps this search so "pehla wala" can add from it next turn.
            "search": first.search,
            "note": CHOICE_NOTE.format(tool=tool),
        },
        ui_event=first.ui_event,
        extra_ui_events=events,
    )


def _failed(reason: str, message: str) -> ToolOutcome:
    return ToolOutcome(
        model_output={"status": "failed", "reason": reason, "message": message, "note": FAILED_NOTE}
    )
