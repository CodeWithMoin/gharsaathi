import json
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyConfigurationError,
    SwiggyError,
    SwiggyRateLimited,
)
from app.commerce.swiggy.models import Product, ProductSearchResult
from app.shopping.models import rupees_to_paise


MAX_RESULTS_FOR_MODEL = 6

SEARCH_PRODUCTS_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "search_products",
    "description": (
        "Search Swiggy Instamart for groceries or household products at the "
        "user's selected delivery address. Read-only: it never changes a cart "
        "or places an order. Use a short product query in English or Hinglish, "
        "including brand and pack size when the user gave them, for example "
        "'Fortune sunflower oil 1 litre' or 'Aashirvaad atta 5 kg'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Product search text, 2 to 120 characters.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "strict": True,
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [SEARCH_PRODUCTS_TOOL]


class SearchProductsArguments(BaseModel):
    query: str = Field(min_length=2, max_length=120)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        stripped = " ".join(value.split())
        if len(stripped) < 2:
            raise ValueError("query is too short")
        return stripped


class ProductSearcher(Protocol):
    async def search(
        self, query: str, address_id: str, offset: int
    ) -> ProductSearchResult: ...


class ToolOutcome(BaseModel):
    """What the model receives, plus an optional event for the browser UI."""

    model_output: dict[str, Any]
    ui_event: dict[str, Any] | None = None


def _product_for_model(product: Product) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": product.name,
        "price_rupees": product.price.offer_price,
        "available": product.available,
    }
    if product.brand:
        entry["brand"] = product.brand
    if product.pack_size:
        entry["pack_size"] = product.pack_size
    if product.price.mrp > product.price.offer_price:
        entry["mrp_rupees"] = product.price.mrp
        saving = rupees_to_paise(product.price.mrp) - rupees_to_paise(product.price.offer_price)
        entry["saving_rupees"] = saving / 100
    if product.price.unit_level_price:
        entry["unit_price"] = product.price.unit_level_price
    if product.delivery_value and product.delivery_unit:
        entry["delivery_eta"] = f"{product.delivery_value} {product.delivery_unit}"
    return entry


class ShoppingToolExecutor:
    def __init__(
        self,
        searcher: ProductSearcher,
        address_id: str | None,
        options: dict[str, Product] | None = None,
    ) -> None:
        self.searcher = searcher
        self.address_id = address_id
        # The model may only add a product by one of these opaque ids. This
        # keeps provider ids and prices out of untrusted model arguments.
        self.options = options if options is not None else {}

    def _remember(self, product: Product) -> str:
        option_id = f"opt{len(self.options) + 1}"
        self.options[option_id] = product
        return option_id

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name != "search_products":
            return ToolOutcome(
                model_output={"status": "error", "message": f"Unknown tool {name}."}
            )
        try:
            arguments = SearchProductsArguments.model_validate(json.loads(raw_arguments))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return ToolOutcome(
                model_output={
                    "status": "error",
                    "message": "Invalid arguments. Send a product query of 2 to 120 characters.",
                }
            )
        return await self._search(arguments.query)

    async def _search(self, query: str) -> ToolOutcome:
        if not self.address_id:
            return ToolOutcome(
                model_output={
                    "status": "not_connected",
                    "message": (
                        "No Swiggy delivery address is selected. Ask the user to connect "
                        "Swiggy and choose a saved address on screen, then start voice again."
                    ),
                },
                ui_event={"type": "tool.not_connected", "query": query},
            )
        try:
            result = await self.searcher.search(query, self.address_id, 0)
        except (SwiggyAuthenticationRequired, SwiggyConfigurationError):
            return ToolOutcome(
                model_output={
                    "status": "not_connected",
                    "message": "Swiggy is not connected or the session has expired. Ask the user to connect Swiggy on screen.",
                },
                ui_event={"type": "tool.not_connected", "query": query},
            )
        except SwiggyRateLimited as exc:
            return ToolOutcome(
                model_output={
                    "status": "rate_limited",
                    "provider": "Swiggy Instamart",
                    "retry_after_seconds": exc.retry_after_seconds,
                    "message": (
                        "Swiggy is temporarily rate-limiting search. No stock result "
                        "was returned, so do not describe this as unclear availability."
                    ),
                },
                ui_event={
                    "type": "tool.error",
                    "query": query,
                    "message": "Swiggy is temporarily rate-limiting search.",
                },
            )
        except SwiggyError:
            return ToolOutcome(
                model_output={
                    "status": "error",
                    "message": "Swiggy Instamart search failed. No results are available right now.",
                },
                ui_event={"type": "tool.error", "query": query, "message": "Swiggy search failed."},
            )

        products = result.products
        return ToolOutcome(
            model_output={
                "status": "ok",
                "store": "Swiggy Instamart",
                "query": query,
                "total_variants": len(products),
                "results": [
                    {"option_id": self._remember(product), **_product_for_model(product)}
                    for product in products[:MAX_RESULTS_FOR_MODEL]
                ],
                "note": (
                    "Prices are the store's current prices in rupees. Nothing was added. "
                    "Use option_id with add_to_basket only when the user asks."
                ),
            },
            ui_event={
                "type": "tool.results",
                "query": query,
                "store": "Swiggy Instamart",
                "products": [p.model_dump() for p in products],
            },
        )
