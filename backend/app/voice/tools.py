import asyncio
import json
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyConfigurationError,
    SwiggyError,
    SwiggyRateLimited,
)
from app.commerce.swiggy.models import Product, ProductSearchResult
from app.commerce.query_guard import clarification_for_query, non_search_reason
from app.commerce.zepto.errors import (
    ZeptoAuthenticationRequired,
    ZeptoConfigurationError,
    ZeptoError,
    ZeptoRateLimited,
)
from app.memory.recall import HouseholdMemory, HouseholdMemoryReader
from app.memory.household_rules import (
    avoided_store_rule,
    product_decision,
    rules_from_memory,
    store_rule_label,
)
from app.memory.remembered import remembered_for
from app.shopping.models import ProviderName, rupees_to_paise
from app.shopping.money import money_fields


logger = logging.getLogger(__name__)


MAX_RESULTS_FOR_MODEL = 6
REMEMBERED_NOTE = (
    " A 'remembered' option matches this household's own past purchase or confirmed "
    "preference (same name and pack size; bought_on names another store). It is a past "
    "choice, not current need or stock."
)

HOUSEHOLD_RULES_NOTE = (
    " This household's own saved rules were applied: skipped_stores were not searched, "
    "hidden_by_household_rules options were left out, 'avoided' marks an option the "
    "user asked for despite a rule, and 'preferred' marks a saved preference. Mention a "
    "skipped store or avoided option once, briefly."
)

SEARCH_PRODUCTS_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "search_products",
    "description": (
        "Search each connected quick-commerce store for any provider-listed product, "
        "including groceries, personal care, household goods, stationery, pet supplies, "
        "toys, small electronics and accessories. Read-only: it never changes a provider cart "
        "or places an order. Use a short product query in English or Hinglish, "
        "including brand and pack size when the user gave them, for example "
        "'Fortune sunflower oil 1 litre', 'Aashirvaad atta 5 kg', or 'USB-C cable'."
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


def item_key(query: str) -> str:
    """Stable key for one searched item: same words, any case/spacing/punctuation.

    The browser groups search results by this key, so searching the same item
    again replaces its group instead of adding another.
    """

    normalised = unicodedata.normalize("NFKC", query).casefold()
    # Keep letters, combining marks (Devanagari matras) and digits.
    kept = "".join(
        char if unicodedata.category(char)[0] in {"L", "M", "N"} else " " for char in normalised
    )
    return " ".join(kept.split())[:120] or "item"


def item_fields(query: str) -> dict[str, str]:
    """``item_key`` and ``item_label`` carried by every search event and result."""

    return {"item_key": item_key(query), "item_label": " ".join(query.split())[:120]}


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


@dataclass(frozen=True)
class ProviderSearchTarget:
    provider: ProviderName
    store: str
    searcher: ProductSearcher
    address_id: str | None
    add_supported: bool = False


@dataclass(frozen=True)
class SearchOption:
    """A search result the model may refer to only by its opaque option_id."""

    provider: ProviderName
    store: str
    product: Product


class ToolOutcome(BaseModel):
    """What the model receives, plus an optional event for the browser UI."""

    model_output: dict[str, Any]
    ui_event: dict[str, Any] | None = None


def _product_for_model(product: Product) -> dict[str, Any]:
    price = rupees_to_paise(product.price.offer_price)
    entry: dict[str, Any] = {
        "name": product.name,
        **money_fields("price", price),
        "available": product.available,
    }
    if product.brand:
        entry["brand"] = product.brand
    if product.pack_size:
        entry["pack_size"] = product.pack_size
    mrp = rupees_to_paise(product.price.mrp)
    if mrp > price:
        entry.update(money_fields("mrp", mrp))
        entry.update(money_fields("saving", mrp - price))
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
                ui_event={"type": "tool.not_connected", "query": query, **item_fields(query)},
            )
        try:
            result = await self.searcher.search(query, self.address_id, 0)
        except (SwiggyAuthenticationRequired, SwiggyConfigurationError):
            return ToolOutcome(
                model_output={
                    "status": "not_connected",
                    "message": "Swiggy is not connected or the session has expired. Ask the user to connect Swiggy on screen.",
                },
                ui_event={"type": "tool.not_connected", "query": query, **item_fields(query)},
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
                    **item_fields(query),
                    "message": "Swiggy is temporarily rate-limiting search.",
                },
            )
        except SwiggyError:
            return ToolOutcome(
                model_output={
                    "status": "error",
                    "message": "Swiggy Instamart search failed. No results are available right now.",
                },
                ui_event={
                    "type": "tool.error",
                    "query": query,
                    **item_fields(query),
                    "message": "Swiggy search failed.",
                },
            )

        products = result.products
        return ToolOutcome(
            model_output={
                "status": "ok",
                "store": "Swiggy Instamart",
                "query": query,
                **item_fields(query),
                "total_variants": len(products),
                "results": [
                    {"option_id": self._remember(product), **_product_for_model(product)}
                    for product in products[:MAX_RESULTS_FOR_MODEL]
                ],
                "note": (
                    "Prices are the store's current prices; quote the price strings exactly. Nothing was added. "
                    "Use option_id with add_to_basket only when the user asks."
                ),
            },
            ui_event={
                "type": "tool.results",
                "query": query,
                **item_fields(query),
                "store": "Swiggy Instamart",
                "products": [p.model_dump() for p in products],
            },
        )


class MultiProviderShoppingToolExecutor:
    """Read-only voice search across ready stores with explicit provenance."""

    def __init__(
        self,
        targets: list[ProviderSearchTarget],
        options: dict[str, SearchOption] | None = None,
        *,
        memory: HouseholdMemoryReader | None = None,
        household_id: str = "",
    ) -> None:
        self.targets = targets
        self.options = options if options is not None else {}
        self.memory = memory
        self.household_id = household_id

    async def _household_memory(self) -> HouseholdMemory | None:
        if self.memory is None or not self.household_id:
            return None
        try:
            return await self.memory.household_memory(self.household_id)
        except Exception as exc:  # noqa: BLE001 - memory must never break a search
            logger.warning("event=voice_search_memory_failed error=%s", type(exc).__name__)
            return None

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
        if non_search_reason(arguments.query) is not None:
            clarification = clarification_for_query(arguments.query)
            return ToolOutcome(
                model_output={
                    "status": "not_a_product_query",
                    "message": clarification
                    or "Answer this conversational question directly. Do not search a store.",
                }
            )

        ready = [target for target in self.targets if target.address_id]
        if not ready:
            return ToolOutcome(
                model_output={
                    "status": "not_connected",
                    "message": "Connect at least one store and choose its saved delivery address on screen.",
                },
                ui_event={"type": "tool.not_connected", "query": arguments.query, **item_fields(arguments.query)},
            )

        # Household memory first: an active avoid_store rule skips that store's call.
        memory = await self._household_memory()
        rules = rules_from_memory(memory)
        skipped: list[dict[str, str]] = []
        allowed: list[ProviderSearchTarget] = []
        for target in ready:
            rule = avoided_store_rule(rules, target.provider, arguments.query)
            if rule is None:
                allowed.append(target)
            else:
                skipped.append({"store": target.store, "household_rule": store_rule_label(rule)})
        if not allowed:
            return ToolOutcome(
                model_output={
                    "status": "skipped_by_household_rule",
                    "query": arguments.query,
                    "skipped_stores": skipped,
                    "message": (
                        "Every ready store is excluded by this household's own saved rule, so "
                        "nothing was searched. Say so in one line and offer to forget or "
                        "change that rule (forget_preference / update_preference)."
                    ),
                },
                ui_event={
                    "type": "tool.error",
                    "query": arguments.query,
                    "message": "Skipped by your saved store rule.",
                },
            )
        ready = allowed
        outcomes = await asyncio.gather(
            *(self._search_target(target, arguments.query) for target in ready)
        )
        successful = [outcome for outcome in outcomes if outcome["status"] == "ok"]
        if not successful:
            statuses = {str(outcome["status"]) for outcome in outcomes}
            status = "rate_limited" if "rate_limited" in statuses else "not_connected" if statuses == {"not_connected"} else "error"
            return ToolOutcome(
                model_output={
                    "status": status,
                    "query": arguments.query,
                    **item_fields(arguments.query),
                    "providers": [
                        {key: value for key, value in outcome.items() if key not in {"target", "products"}}
                        for outcome in outcomes
                    ],
                    "message": "No connected store returned product results. Do not guess price or availability.",
                },
                ui_event={
                    "type": "tool.not_connected" if status == "not_connected" else "tool.error",
                    "query": arguments.query,
                    **item_fields(arguments.query),
                    "message": "Connected stores could not be searched right now.",
                },
            )

        model_results: list[dict[str, Any]] = []
        ui_products: list[dict[str, Any]] = []
        stores: list[str] = []
        per_store_limit = max(1, MAX_RESULTS_FOR_MODEL // len(successful))
        remembered_count = 0
        filtered_count = 0
        for outcome in successful:
            target = outcome["target"]
            assert isinstance(target, ProviderSearchTarget)
            products = outcome["products"]
            assert isinstance(products, list)
            stores.append(target.store)
            decisions: dict[int, dict[str, Any]] = {}
            remembered_sources: dict[int, str] = {}
            if rules:
                kept: list[Product] = []
                for product in products:
                    decision = product_decision(rules, product, target.provider, arguments.query)
                    if decision is not None and decision.get("drop"):
                        filtered_count += 1
                        continue
                    if decision is not None:
                        decisions[id(product)] = decision
                    kept.append(product)
                products = kept
            for product in products[:per_store_limit]:
                assert isinstance(product, Product)
                entry = {
                    "provider": target.store,
                    "store_id": target.provider,
                    "add_supported": target.add_supported,
                    **_product_for_model(product),
                    **decisions.get(id(product), {}),
                }
                if memory is not None:
                    remembered = remembered_for(product, target.provider, memory)
                    if remembered is not None:
                        entry["remembered"] = remembered
                        remembered_sources[id(product)] = str(remembered["source"])
                        remembered_count += 1
                if target.add_supported:
                    option_id = f"opt{len(self.options) + 1}"
                    self.options[option_id] = SearchOption(target.provider, target.store, product)
                    entry["option_id"] = option_id
                model_results.append(entry)
            ui_products.extend(
                {
                    **product.model_dump(),
                    "provider": target.provider,
                    "provider_label": target.store,
                    **{
                        key: value
                        for key, value in decisions.get(id(product), {}).items()
                        if key in {"preferred", "avoided"}
                    },
                    # The browser shows the remembered option first, with its badge.
                    **(
                        {"remembered": remembered_sources[id(product)]}
                        if id(product) in remembered_sources
                        else {}
                    ),
                }
                for product in products[:per_store_limit]
            )

        model_output: dict[str, Any] = {
            "status": "ok",
            "stores_checked": stores,
            "query": arguments.query,
            **item_fields(arguments.query),
            "total_variants": len(ui_products),
            "results": model_results,
            "provider_statuses": [
                {key: value for key, value in outcome.items() if key not in {"target", "products"}}
                for outcome in outcomes
            ],
            "note": (
                "Current item prices only; quote the price strings exactly. Nothing was added. "
                "Only results with an "
                "option_id can be added, to that store's own local basket."
                + (REMEMBERED_NOTE if remembered_count else "")
                + (
                    HOUSEHOLD_RULES_NOTE
                    if skipped
                    or filtered_count
                    or any("preferred" in entry or "avoided" in entry for entry in model_results)
                    else ""
                )
            ),
        }
        if skipped:
            model_output["skipped_stores"] = skipped
        if filtered_count:
            model_output["hidden_by_household_rules"] = filtered_count
        return ToolOutcome(
            model_output=model_output,
            ui_event={
                "type": "tool.results",
                "query": arguments.query,
                **item_fields(arguments.query),
                "store": " + ".join(stores),
                "products": ui_products,
            },
        )

    async def _search_target(
        self, target: ProviderSearchTarget, query: str
    ) -> dict[str, Any]:
        assert target.address_id is not None
        try:
            result = await target.searcher.search(query, target.address_id, 0)
        except (SwiggyAuthenticationRequired, SwiggyConfigurationError, ZeptoAuthenticationRequired, ZeptoConfigurationError):
            return {"status": "not_connected", "store": target.store, "target": target, "products": []}
        except SwiggyRateLimited as exc:
            return {
                "status": "rate_limited",
                "store": target.store,
                "retry_after_seconds": exc.retry_after_seconds,
                "target": target,
                "products": [],
            }
        except ZeptoRateLimited:
            return {"status": "rate_limited", "store": target.store, "target": target, "products": []}
        except (SwiggyError, ZeptoError):
            return {"status": "error", "store": target.store, "target": target, "products": []}
        return {"status": "ok", "store": target.store, "target": target, "products": result.products}
