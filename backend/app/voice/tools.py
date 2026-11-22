import asyncio
import json
import logging
import time
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
    ZeptoProviderError,
)
from app.memory.recall import HouseholdMemory, HouseholdMemoryReader
from app.memory.household_rules import (
    avoided_store_rule,
    keep_outs,
    product_decision,
    rules_from_memory,
    sort_mode,
    store_rule_label,
    usual_quantity,
    without_store_names,
)
from app.memory.remembered import pack_key, remembered_for
from app.shopping.models import ProviderName, rupees_to_paise
from app.shopping.money import money_fields
from app.voice.timing import ms_since, record, stage


logger = logging.getLogger(__name__)


MAX_RESULTS_FOR_MODEL = 6
# Once one store has answered, a slower store gets this long before the search
# returns without it; with no answer yet, the search waits up to the hard cap.
PROVIDER_SEARCH_TIMEOUT_SECONDS = 4.0
PROVIDER_SEARCH_HARD_TIMEOUT_SECONDS = 20.0
# Options shown for one searched item. The browser numbers cards by `position`.
MAX_OPTIONS_PER_ITEM = 8
POSITION_NOTE = (
    " Results are in the order shown on screen; 'position' is the card number the "
    "user sees for this item (1 = first card)."
)
REMEMBERED_NOTE = (
    " A 'remembered' option matches this household's own past purchase or confirmed "
    "preference (same name and pack size; bought_on names another store). It is a past "
    "choice, not current need or stock."
)

HOUSEHOLD_RULES_NOTE = (
    " This household's own saved rules were applied: skipped_stores were not searched, "
    "hidden_by_household_rules options were left out, and 'preferred' marks a saved "
    "preference. Mention a skipped store or hidden option once, briefly."
)
INGREDIENT_CHECK_NOTE = (
    " ingredient_check lists what this household keeps out of food. Options whose names "
    "show it were hidden, but store names do not list every ingredient: for packaged food, "
    "say once, briefly, to check the pack label before ordering."
)
USUAL_QUANTITY_NOTE = (
    " usual_quantity is the pack count this household told us for this item; when the user "
    "names no quantity, call add_to_basket with quantity null and the code uses it."
)
AVOIDED_NOTE = (
    " An 'avoided' option is one the user named although their own saved rule "
    "(avoided_because) avoids it. Before describing or adding it, ask once: "
    "'Aapne <rule> kaha tha—phir bhi dikhaun/add karun?'"
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
        "'Fortune sunflower oil 1 litre', 'Aashirvaad atta 5 kg', or 'USB-C cable'. "
        "status provider_error (or a store listed in unreachable_stores) means that "
        "store could not be reached: it says nothing about whether the product exists, "
        "so never report it as not found."
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
    # The household's told pack count for this item, the add default.
    usual_quantity: int | None = None


class ShownGroups:
    """What the screen shows per searched item: option ids in card order (position 1 first)."""

    def __init__(self) -> None:
        # None marks a card that cannot be referred to (its store cannot add it).
        self.groups: dict[str, list[str | None]] = {}
        self.labels: dict[str, str] = {}
        self.latest: str | None = None

    def record(self, key: str, label: str, option_ids: list[str | None]) -> None:
        # Searching an item again replaces its group on screen, and here.
        self.groups[key] = option_ids
        self.labels[key] = label
        self.latest = key

    def record_results(self, model_output: dict[str, Any]) -> None:
        """Records an ok search_products output (after any photo reordering) as shown."""

        results = model_output.get("results")
        key, label = model_output.get("item_key"), model_output.get("item_label")
        if model_output.get("status") != "ok" or not isinstance(results, list) or not isinstance(key, str):
            return
        ordered = sorted(
            (entry for entry in results if isinstance(entry, dict) and isinstance(entry.get("position"), int)),
            key=lambda entry: entry["position"],
        )
        ids = [entry.get("option_id") if isinstance(entry.get("option_id"), str) else None for entry in ordered]
        self.record(key, label if isinstance(label, str) else key, ids)

    def group_of(self, option_id: str) -> str | None:
        return next((key for key, ids in self.groups.items() if option_id in ids), None)


class ToolOutcome(BaseModel):
    """What the model receives, plus an optional event for the browser UI."""

    model_output: dict[str, Any]
    ui_event: dict[str, Any] | None = None
    # Events a multi-step tool (express order, clear basket) shows before ``ui_event``.
    extra_ui_events: list[dict[str, Any]] = Field(default_factory=list)


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
        # Display strings only: the prompt matches amounts on price_paise, and
        # mrp/saving are quoted, never computed with.
        entry["mrp"] = money_fields("mrp", mrp)["mrp"]
        entry["saving"] = money_fields("saving", mrp - price)["saving"]
    if product.price.unit_level_price:
        entry["unit_price"] = product.price.unit_level_price
    if product.delivery_value and product.delivery_unit:
        entry["delivery_eta"] = f"{product.delivery_value} {product.delivery_unit}"
    return entry


@dataclass(frozen=True)
class RankedOption:
    """One option for a searched item, with the household annotations that order it."""

    provider: ProviderName
    product: Product
    remembered: str | None = None
    preferred: bool = False
    avoided: bool = False


def _option_rank(option: RankedOption) -> int:
    if not option.product.available:
        return 3
    if option.remembered:
        return 0
    if option.preferred:
        return 1
    return 2


def _delivery_minutes(product: Product) -> float:
    try:
        value = float(product.delivery_value or "")
    except ValueError:
        return float("inf")
    unit = (product.delivery_unit or "").casefold()
    return value * 60 if unit.startswith("h") else value if unit.startswith("m") else float("inf")


def _unit_price(product: Product) -> float:
    # The pack size alone when given: the name's "12 Pack" is already in "840 g".
    size = pack_key(product.pack_size) or pack_key(product.name)
    if size is None or size[0] * size[1] <= 0:
        return float("inf")
    return product.price.offer_price / (size[0] * size[1])


def order_options(
    options: list[RankedOption], limit: int = MAX_OPTIONS_PER_ITEM, sort: str | None = None
) -> list[RankedOption]:
    """The one display order for a searched item, shared by the model and the screen.

    Remembered first, then preferred, then the rest in provider order;
    unavailable options last. Duplicates and unnamed options are dropped.
    Avoided options the user did not name must already be removed; named ones
    keep provider order. ``sort`` ("cheapest" / "fastest", the household's own
    saved choice) orders options of the same rank by price or delivery time.
    Index + 1 is the card's position.
    """

    seen: set[tuple[str, str, str]] = set()
    kept: list[RankedOption] = []
    for option in options:
        key = (option.provider, option.product.product_id, option.product.sku_id)
        if not option.product.name.strip() or key in seen:
            continue
        seen.add(key)
        kept.append(option)
    # sorted() is stable, so equal ranks keep provider order.
    if sort == "cheapest":
        return sorted(kept, key=lambda option: (_option_rank(option), option.product.price.offer_price))[:limit]
    if sort == "unit_value":
        return sorted(kept, key=lambda option: (_option_rank(option), _unit_price(option.product)))[:limit]
    if sort == "fastest":
        return sorted(kept, key=lambda option: (_option_rank(option), _delivery_minutes(option.product)))[:limit]
    return sorted(kept, key=_option_rank)[:limit]


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

        ordered = order_options(
            [RankedOption("swiggy_instamart", product) for product in result.products],
            MAX_RESULTS_FOR_MODEL,
        )
        products = [option.product for option in ordered]
        return ToolOutcome(
            model_output={
                "status": "ok",
                "store": "Swiggy Instamart",
                "query": query,
                **item_fields(query),
                "total_variants": len(result.products),
                "results": [
                    {"option_id": self._remember(product), "position": position, **_product_for_model(product)}
                    for position, product in enumerate(products, 1)
                ],
                "note": (
                    "Prices are the store's current prices; quote the price strings exactly. Nothing was added. "
                    "Use option_id with add_to_basket only when the user asks."
                    + POSITION_NOTE
                ),
            },
            ui_event={
                "type": "tool.results",
                "query": query,
                **item_fields(query),
                "store": "Swiggy Instamart",
                "products": [
                    {**p.model_dump(), "position": position} for position, p in enumerate(products, 1)
                ],
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
        provider_timeout: float = PROVIDER_SEARCH_TIMEOUT_SECONDS,
        provider_hard_timeout: float = PROVIDER_SEARCH_HARD_TIMEOUT_SECONDS,
        shown: ShownGroups | None = None,
    ) -> None:
        self.targets = targets
        self.options = options if options is not None else {}
        self.shown = shown if shown is not None else ShownGroups()
        self.memory = memory
        self.household_id = household_id
        self.provider_timeout = provider_timeout
        self.provider_hard_timeout = provider_hard_timeout

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
        # A store whose saved address vanished is reported, not silently skipped.
        address_problems = [
            {
                "status": "provider_error",
                "reason": problem,
                "store": target.store,
                "store_id": target.provider,
                "target": target,
                "products": [],
            }
            for target in self.targets
            if not target.address_id and (problem := _address_problem(target)) is not None
        ]
        if not ready and not address_problems:
            return ToolOutcome(
                model_output={
                    "status": "not_connected",
                    "message": "Connect at least one store and choose its saved delivery address on screen.",
                },
                ui_event={"type": "tool.not_connected", "query": arguments.query, **item_fields(arguments.query)},
            )

        # Household memory first: an active avoid_store rule skips that store's call.
        with stage("memory_ms"):
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
        if not allowed and not address_problems:
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
        outcomes = (
            await self._search_targets(ready, without_store_names(arguments.query)) + address_problems
        )
        post_started = time.perf_counter()
        try:
            return self._results(arguments.query, outcomes, memory, rules, skipped)
        finally:
            record("post_ms", ms_since(post_started))

    def _results(
        self,
        query: str,
        outcomes: list[dict[str, Any]],
        memory: HouseholdMemory | None,
        rules: Any,
        skipped: list[dict[str, str]],
    ) -> ToolOutcome:
        arguments = SearchProductsArguments(query=query)
        successful = [outcome for outcome in outcomes if outcome["status"] == "ok"]
        unreachable = [
            {"store": outcome["store"], "store_id": outcome["store_id"], "reason": outcome["reason"]}
            for outcome in outcomes
            if outcome["status"] == "provider_error"
        ]
        if not successful:
            statuses = {str(outcome["status"]) for outcome in outcomes}
            status = "not_connected" if statuses == {"not_connected"} else "provider_error"
            model_output: dict[str, Any] = {
                "status": status,
                "query": arguments.query,
                **item_fields(arguments.query),
                "providers": [_provider_status(outcome) for outcome in outcomes],
            }
            if status == "provider_error":
                model_output["unreachable_stores"] = unreachable
                model_output["message"] = provider_error_message(unreachable)
            else:
                model_output["message"] = (
                    "No store is connected. Do not guess price or availability."
                )
            return ToolOutcome(
                model_output=model_output,
                ui_event={
                    "type": "tool.not_connected" if status == "not_connected" else "tool.error",
                    "query": arguments.query,
                    **item_fields(arguments.query),
                    "message": unreachable_ui_message(unreachable)
                    if unreachable
                    else "Connected stores could not be searched right now.",
                    **_address_removed(unreachable),
                },
            )

        model_results: list[dict[str, Any]] = []
        ui_products: list[dict[str, Any]] = []
        stores: list[str] = []
        per_store_limit = max(1, MAX_RESULTS_FOR_MODEL // len(successful))
        remembered_count = 0
        filtered_count = 0
        candidates: list[RankedOption] = []
        targets: dict[ProviderName, ProviderSearchTarget] = {}
        decisions: dict[int, dict[str, Any]] = {}
        remembered_marks: dict[int, dict[str, Any]] = {}
        for outcome in successful:
            target = outcome["target"]
            assert isinstance(target, ProviderSearchTarget)
            products = outcome["products"]
            assert isinstance(products, list)
            stores.append(target.store)
            targets[target.provider] = target
            if rules:
                kept: list[Product] = []
                for product in products:
                    decision = product_decision(rules, product, target.provider, arguments.query)
                    if decision is not None and decision.get("drop"):
                        # Avoided options the user did not name are hidden from the model and the screen alike.
                        filtered_count += 1
                        continue
                    if decision is not None:
                        decisions[id(product)] = decision
                    kept.append(product)
                products = kept
            for product in products[:per_store_limit]:
                assert isinstance(product, Product)
                remembered = remembered_for(product, target.provider, memory) if memory is not None else None
                if remembered is not None:
                    remembered_marks[id(product)] = remembered
                candidates.append(
                    RankedOption(
                        target.provider,
                        product,
                        remembered=str(remembered["source"]) if remembered is not None else None,
                        preferred=bool(decisions.get(id(product), {}).get("preferred")),
                        avoided=bool(decisions.get(id(product), {}).get("avoided")),
                    )
                )

        # One order for the model and the screen: "doosra wala" is card 2 for both.
        usual_count = 0
        for position, option in enumerate(order_options(candidates, sort=sort_mode(rules) if rules else None), 1):
            target = targets[option.provider]
            product = option.product
            entry = {
                "position": position,
                "provider": target.store,
                "store_id": target.provider,
                # Addable results carry an option_id; only the exception is spelled out.
                **({} if target.add_supported else {"add_supported": False}),
                **_product_for_model(product),
                **decisions.get(id(product), {}),
            }
            if id(product) in remembered_marks:
                entry["remembered"] = remembered_marks[id(product)]
                remembered_count += 1
            usual = usual_quantity(rules, query, product) if rules else None
            if usual is not None:
                entry["usual_quantity"] = usual
                usual_count += 1
            if target.add_supported:
                option_id = f"opt{len(self.options) + 1}"
                self.options[option_id] = SearchOption(target.provider, target.store, product, usual)
                entry["option_id"] = option_id
            model_results.append(entry)
            ui_products.append(
                {
                    **product.model_dump(),
                    "provider": target.provider,
                    "provider_label": target.store,
                    "position": position,
                    **({"preferred": True} if option.preferred else {}),
                    **({"avoided": True} if option.avoided else {}),
                    # The browser badges card 1 from these annotations.
                    **({"remembered": option.remembered} if option.remembered else {}),
                }
            )

        model_output: dict[str, Any] = {
            "status": "ok",
            "stores_checked": stores,
            "query": arguments.query,
            **item_fields(arguments.query),
            "results": model_results,
            "note": (
                "Current item prices only; quote the price strings exactly. Nothing was added. "
                "Only results with an "
                "option_id can be added, to that store's own local basket."
                + POSITION_NOTE
                + (REMEMBERED_NOTE if remembered_count else "")
                + (
                    HOUSEHOLD_RULES_NOTE
                    if skipped
                    or filtered_count
                    or any("preferred" in entry for entry in model_results)
                    else ""
                )
                + (AVOIDED_NOTE if any(entry.get("avoided") for entry in model_results) else "")
                + (INGREDIENT_CHECK_NOTE if rules and keep_outs(rules) else "")
                + (USUAL_QUANTITY_NOTE if usual_count else "")
            ),
        }
        if rules and keep_outs(rules):
            model_output["ingredient_check"] = keep_outs(rules)
        if any(outcome["status"] != "ok" for outcome in outcomes):
            # stores_checked already names the stores that answered.
            model_output["provider_statuses"] = [_provider_status(outcome) for outcome in outcomes]
        if skipped:
            model_output["skipped_stores"] = skipped
        if filtered_count:
            model_output["hidden_by_household_rules"] = filtered_count
        self.shown.record_results(model_output)
        ui_event: dict[str, Any] = {
            "type": "tool.results",
            "query": arguments.query,
            **item_fields(arguments.query),
            "store": " + ".join(stores),
            "products": ui_products,
        }
        if unreachable:
            model_output["unreachable_stores"] = unreachable
            model_output["note"] += " " + provider_error_message(unreachable)
            ui_event["unreachable_stores"] = [entry["store"] for entry in unreachable]
            ui_event["notice"] = unreachable_ui_message(unreachable)
            ui_event.update(_address_removed(unreachable))
        return ToolOutcome(model_output=model_output, ui_event=ui_event)

    async def _search_targets(
        self, targets: list[ProviderSearchTarget], query: str
    ) -> list[dict[str, Any]]:
        """Search every store concurrently; a slow store does not hold back a fast one.

        A store still running after the soft timeout (once another store has
        answered) is reported unreachable with reason "timeout". Its search keeps
        running in the background, so its result lands in the provider's cache.
        """

        tasks = [asyncio.create_task(self._search_target(target, query)) for target in targets]
        loop = asyncio.get_running_loop()
        started = loop.time()
        pending = set(tasks)
        while pending:
            answered = any(task.done() and task.result()["status"] == "ok" for task in tasks)
            limit = self.provider_timeout if answered else self.provider_hard_timeout
            remaining = started + limit - loop.time()
            if remaining <= 0:
                break
            _, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
        outcomes: list[dict[str, Any]] = []
        for target, task in zip(targets, tasks):
            if task.done():
                outcomes.append(task.result())
                continue
            task.add_done_callback(_discard_result)
            logger.warning("event=voice_search_store_slow provider=%s", target.provider)
            outcomes.append(
                {
                    "store": target.store,
                    "store_id": target.provider,
                    "target": target,
                    "products": [],
                    "status": "provider_error",
                    "reason": "timeout",
                }
            )
        return outcomes

    async def _search_target(
        self, target: ProviderSearchTarget, query: str
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return await self._search_one(target, query)
        finally:
            record(f"{target.provider}_ms", ms_since(started))

    async def _search_one(
        self, target: ProviderSearchTarget, query: str
    ) -> dict[str, Any]:
        assert target.address_id is not None
        base = {"store": target.store, "store_id": target.provider, "target": target, "products": []}
        try:
            result = await target.searcher.search(query, target.address_id, 0)
        except (SwiggyAuthenticationRequired, SwiggyConfigurationError, ZeptoAuthenticationRequired, ZeptoConfigurationError):
            return {**base, "status": "not_connected"}
        except SwiggyRateLimited as exc:
            return {
                **base,
                "status": "provider_error",
                "reason": "rate_limited",
                "retry_after_seconds": exc.retry_after_seconds,
            }
        except ZeptoProviderError as exc:
            return {**base, "status": "provider_error", "reason": exc.reason}
        except (SwiggyError, ZeptoError):
            return {**base, "status": "provider_error", "reason": "unavailable"}
        return {**base, "status": "ok", "products": result.products}


def _discard_result(task: "asyncio.Task[dict[str, Any]]") -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("event=voice_search_store_failed error=%s", type(task.exception()).__name__)


def _address_problem(target: ProviderSearchTarget) -> str | None:
    problem = getattr(target.searcher, "address_problem", None)
    if not callable(problem):
        return None
    value = problem()
    return value if isinstance(value, str) else None


def _address_removed(unreachable: list[dict[str, Any]]) -> dict[str, str]:
    """Tells the browser to open that store's address picker."""

    store_id = next(
        (entry["store_id"] for entry in unreachable if entry["reason"] == "address_invalid"), None
    )
    return {"address_removed": store_id} if store_id else {}


def _provider_status(outcome: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in outcome.items() if key not in {"target", "products"}}


PROVIDER_ERROR_REASONS = {
    "unavailable": "is not responding right now",
    "rate_limited": "is briefly limiting requests",
    "timeout": "did not answer in time, so its results are not included",
    "session_expired": "needs to be reconnected on screen",
    "address_invalid": (
        "no longer has the selected saved address (it was changed or deleted in the "
        "store app); ask the user to pick a delivery address again "
        "(list_delivery_addresses, then select_delivery_address)"
    ),
}


def provider_error_message(unreachable: list[dict[str, Any]]) -> str:
    """Model-facing explanation of a store that could not be searched."""

    parts = [
        f"{entry['store']} {PROVIDER_ERROR_REASONS.get(entry['reason'], PROVIDER_ERROR_REASONS['unavailable'])}"
        for entry in unreachable
    ]
    return (
        "Could not search: " + "; ".join(parts) + ". This is a connection problem, not a "
        "search result: never say the item was not found or is unavailable there. Say, "
        "for example, 'Zepto se abhi connect nahi ho paya, thodi der mein dobara try karte hain.'"
    )


def unreachable_ui_message(unreachable: list[dict[str, Any]]) -> str:
    if any(entry["reason"] == "address_invalid" for entry in unreachable):
        store = next(entry["store"] for entry in unreachable if entry["reason"] == "address_invalid")
        return f"Your {store} saved address changed—choose a delivery address again."
    stores = " and ".join(dict.fromkeys(entry["store"] for entry in unreachable))
    return f"{stores} isn't responding—try again."
