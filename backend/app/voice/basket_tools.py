"""Voice tools that change the local basket, never the provider cart.

The model is deliberately constrained to opaque ``option_id`` values issued by
the search tool. Provider product identifiers and prices are derived only from
the corresponding stored search result.
"""

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.commerce.swiggy.models import Product
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService, format_rupees, item_total_paise
from app.shopping.errors import ShoppingError
from app.shopping.models import Basket, BasketLine, ProductRef, rupees_to_paise
from app.voice.tools import ToolOutcome


HOUSEHOLD_ID = "local-household"
PROVIDER = "swiggy_instamart"

BASKET_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "add_to_basket",
        "description": (
            "Add an option from this conversation's latest product results to the local "
            "household basket. Use only option_id returned by search_products. This does "
            "not change a provider cart or place an order."
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
            "Change a local basket line's quantity. Use item_id from view_basket. "
            "Quantity zero removes the item. This never places an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "item_id from view_basket."},
                "quantity": {"type": "integer", "description": "New quantity, from 0 to 50."},
            },
            "required": ["item_id", "quantity"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "view_basket",
        "description": "Show the current local basket and its exact item total. Fees are not included.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "request_approval",
        "description": (
            "Request the provider's final bill for the current basket and show it for on-screen "
            "review. Use only when the user says the basket is ready. This does not approve, "
            "checkout, or place an order."
        ),
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
]

BASKET_TOOL_NAMES = frozenset(tool["name"] for tool in BASKET_TOOL_DEFINITIONS)


class AddArguments(BaseModel):
    option_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=50)


class QuantityArguments(BaseModel):
    item_id: str = Field(min_length=1, max_length=256)
    quantity: int = Field(ge=0, le=50)


def basket_for_model(basket: Basket) -> dict[str, Any]:
    return {
        "items": [
            {
                "item_id": line.ref.variant_id,
                "name": line.name,
                "pack_size": line.pack_size,
                "quantity": line.quantity,
                "line_total": format_rupees(line.unit_price_paise * line.quantity),
                "available": line.available,
            }
            for line in basket.lines
        ],
        "item_total": format_rupees(item_total_paise(basket)),
        "note": "Item total only. Fees and the final payable amount require an approval quote. Nothing was ordered.",
    }


def _line_from_product(product: Product, quantity: int) -> BasketLine:
    unit_price = rupees_to_paise(product.price.offer_price)
    mrp = rupees_to_paise(product.price.mrp)
    return BasketLine(
        ref=ProductRef(
            provider=PROVIDER,
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
        options: dict[str, Product],
        address_id: str | None,
    ) -> None:
        self.baskets = baskets
        self.approvals = approvals
        self.options = options
        self.address_id = address_id

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
                return _basket_outcome(await self.baskets.active(HOUSEHOLD_ID, PROVIDER), "ok")
            if name == "request_approval":
                return await self._request_approval()
        except ValidationError:
            return _error("Invalid arguments for this tool.")
        except ShoppingError as exc:
            return _error(str(exc))
        return _error(f"Unknown tool {name}.")

    async def _add(self, arguments: AddArguments) -> ToolOutcome:
        product = self.options.get(arguments.option_id)
        if product is None:
            return _error("Unknown option_id. Search again and use an option_id from the results.")
        if not product.available:
            return _error(f"{product.name} is unavailable right now, so it was not added.")
        if self.address_id:
            await self.baskets.set_address(HOUSEHOLD_ID, PROVIDER, self.address_id)
        basket = await self.baskets.add_line(
            HOUSEHOLD_ID, PROVIDER, _line_from_product(product, arguments.quantity)
        )
        return _basket_outcome(basket, "added")

    async def _set_quantity(self, arguments: QuantityArguments) -> ToolOutcome:
        basket = await self.baskets.set_quantity(
            HOUSEHOLD_ID, PROVIDER, arguments.item_id, arguments.quantity
        )
        return _basket_outcome(basket, "updated")

    async def _request_approval(self) -> ToolOutcome:
        if not self.address_id:
            return _error("No delivery address is selected. Ask the user to choose one on screen.")
        basket = await self.baskets.set_address(HOUSEHOLD_ID, PROVIDER, self.address_id)
        if not basket.lines:
            return _error("The basket is empty.")
        result = await self.approvals.request(basket.id)
        quote = result.quote
        return ToolOutcome(
            model_output={
                "status": "awaiting_user_approval",
                "payable": format_rupees(quote.payable_paise),
                "charges": [
                    {"label": charge.label, "amount": format_rupees(charge.amount_paise)}
                    for charge in quote.charges
                ],
                "note": (
                    "The exact bill is on screen. Ask the user to review it and choose an action there. "
                    "No order has been approved or placed."
                ),
            },
            ui_event={
                "type": "basket.approval_requested",
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
                "basket": _basket_ui(basket),
            },
        )


def _basket_ui(basket: Basket) -> dict[str, Any]:
    return {
        "id": basket.id,
        "revision": basket.revision,
        "status": basket.status.value,
        "item_count": sum(line.quantity for line in basket.lines),
        "item_total": format_rupees(item_total_paise(basket)),
        "lines": [
            {"item_id": line.ref.variant_id, "name": line.name, "quantity": line.quantity}
            for line in basket.lines
        ],
    }


def _basket_outcome(basket: Basket, status: str) -> ToolOutcome:
    return ToolOutcome(
        model_output={"status": status, **basket_for_model(basket)},
        ui_event={"type": "basket.updated", "basket": _basket_ui(basket)},
    )


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(model_output={"status": "error", "message": message})
