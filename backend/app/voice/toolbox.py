"""Session-scoped router for voice shopping tools."""

import json
from dataclasses import replace
from typing import Protocol

from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.voice.address_book_tools import (
    ADDRESS_BOOK_TOOL_DEFINITIONS,
    ADDRESS_BOOK_TOOL_NAMES,
    AddressBook,
    AddressBookToolExecutor,
)
from app.voice.address_prefill import RecentUserWords
from app.voice.address_tools import (
    ADDRESS_TOOL_DEFINITIONS,
    ADDRESS_TOOL_NAMES,
    AddressFormToolExecutor,
    ZeptoConnectionChecker,
)
from app.voice.basket_tools import (
    BASKET_TOOL_DEFINITIONS,
    BASKET_TOOL_NAMES,
    AddressDescriber,
    BasketStore,
    BasketToolExecutor,
)
from app.memory.recall import DatabasePreferenceRecaller, PreferenceRecaller
from app.memory.household_writer import DatabaseHouseholdPreferences, HouseholdPreferenceWriter
from app.voice.memory_write_tools import (
    MEMORY_WRITE_TOOL_DEFINITIONS,
    MEMORY_WRITE_TOOL_NAMES,
    MemoryWriteToolExecutor,
)
from app.voice.memory_choices import MemoryChoiceHooks
from app.voice.memory_tools import MEMORY_TOOL_DEFINITIONS, MEMORY_TOOL_NAMES, MemoryToolExecutor
from app.shopping.models import ProviderName
from app.shopping.order_confirmation import OrderConfirmations
from app.voice.order_tools import ORDER_TOOL_DEFINITIONS, ORDER_TOOL_NAMES, OrderToolExecutor
from app.voice.photo_tools import PHOTO_TOOL_DEFINITIONS, PHOTO_TOOL_NAMES, PhotoReading, PhotoToolExecutor
from app.voice.order_status_tools import (
    ORDER_STATUS_TOOL_DEFINITIONS,
    ORDER_STATUS_TOOL_NAMES,
    OrderHistoryReader,
    OrderStatusToolExecutor,
)
from app.tracking.service import OrderTrackingService
from app.voice.tools import TOOL_DEFINITIONS as SEARCH_TOOL_DEFINITIONS
from app.voice.tools import (
    MultiProviderShoppingToolExecutor,
    ProductSearcher,
    ProviderSearchTarget,
    SearchOption,
    ToolOutcome,
)


VOICE_TOOL_DEFINITIONS = [
    *SEARCH_TOOL_DEFINITIONS,
    *BASKET_TOOL_DEFINITIONS,
    *ADDRESS_TOOL_DEFINITIONS,
    *MEMORY_TOOL_DEFINITIONS,
    *MEMORY_WRITE_TOOL_DEFINITIONS,
    *ORDER_TOOL_DEFINITIONS,
    *ADDRESS_BOOK_TOOL_DEFINITIONS,
    *PHOTO_TOOL_DEFINITIONS,
    *ORDER_STATUS_TOOL_DEFINITIONS,
]


def _option_id(raw_arguments: str) -> str | None:
    try:
        value = json.loads(raw_arguments or "{}").get("option_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


class ToolExecutor(Protocol):
    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome: ...


class VoiceToolbox:
    """Shares safe search options between search and basket tools for one session."""

    def __init__(
        self,
        searcher: ProductSearcher,
        baskets: BasketService,
        approvals: ApprovalService,
        address_id: str | None,
        zepto_searcher: ProductSearcher | None = None,
        zepto_address_id: str | None = None,
        household_id: str = "",
        *,
        zepto_connection: ZeptoConnectionChecker | None = None,
        preference_recall: PreferenceRecaller | None = None,
        addresses: AddressDescriber | None = None,
        preference_writer: HouseholdPreferenceWriter | None = None,
        order_confirmations: OrderConfirmations | None = None,
        address_book: AddressBook | None = None,
        user_words: RecentUserWords | None = None,
        photo_reader: PhotoReading | None = None,
        order_tracking: OrderTrackingService | None = None,
        order_history: OrderHistoryReader | None = None,
    ) -> None:
        options: dict[str, SearchOption] = {}
        targets = [
            ProviderSearchTarget(
                provider="swiggy_instamart",
                store="Swiggy Instamart",
                searcher=searcher,
                address_id=address_id,
                add_supported=True,
            )
        ]
        if zepto_searcher is not None:
            targets.append(
                ProviderSearchTarget(
                    provider="zepto",
                    store="Zepto",
                    searcher=zepto_searcher,
                    address_id=zepto_address_id,
                    add_supported=True,
                )
            )
        if not household_id:
            raise ValueError("A verified household identity is required for voice baskets.")
        memory = preference_recall if callable(getattr(preference_recall, "household_memory", None)) else None
        self.search = MultiProviderShoppingToolExecutor(
            targets,
            options,
            memory=memory,  # type: ignore[arg-type]
            household_id=household_id,
        )
        stores = [BasketStore(target.provider, target.store, target.address_id) for target in targets]
        self.basket = BasketToolExecutor(
            baskets, approvals, options, stores, household_id, addresses
        )
        # The user's own recent words; the sideband and typed chat feed it.
        self.user_words = user_words if user_words is not None else RecentUserWords()
        self.address_form = AddressFormToolExecutor(zepto_connection, self.user_words)
        self.options = options
        self._stale_options: set[str] = set()
        self.address_book = AddressBookToolExecutor(address_book, baskets, self, household_id)
        self.memory = MemoryToolExecutor(preference_recall, household_id)
        if preference_writer is None and isinstance(preference_recall, DatabasePreferenceRecaller):
            preference_writer = DatabaseHouseholdPreferences(preference_recall.session_factory)
        self.memory_write = MemoryWriteToolExecutor(preference_writer, household_id)
        self.choices = MemoryChoiceHooks(
            preference_writer, self.memory_write, self.search, options, household_id
        )
        self.order = OrderToolExecutor(self.basket, order_confirmations)
        self.photo = PhotoToolExecutor(photo_reader, household_id)
        self.order_status = OrderStatusToolExecutor(order_tracking, order_history, household_id)

    def has_store(self, provider: ProviderName) -> bool:
        return provider in self.basket.stores

    def store_address(self, provider: ProviderName) -> str | None:
        store = self.basket.stores.get(provider)
        return store.address_id if store is not None else None

    def current_addresses(self) -> tuple[str | None, str | None]:
        return self.store_address("swiggy_instamart"), self.store_address("zepto")

    def use_address(self, provider: ProviderName, address_id: str) -> None:
        """Later searches, baskets and bills in this session use ``address_id``."""

        previous = self.store_address(provider)
        self.search.targets = [
            replace(target, address_id=address_id) if target.provider == provider else target
            for target in self.search.targets
        ]
        store = self.basket.stores.get(provider)
        if store is not None:
            self.basket.stores[provider] = replace(store, address_id=address_id)
        if previous != address_id:
            # Options were priced for the old address; the model must search again.
            # (Ids stay in ``options`` so new option ids never reuse them.)
            self._stale_options.update(
                key for key, option in self.options.items() if option.provider == provider
            )

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name in PHOTO_TOOL_NAMES:
            return await self.photo.execute(name, raw_arguments)
        if name in ADDRESS_BOOK_TOOL_NAMES:
            return await self.address_book.execute(name, raw_arguments)
        if name in ADDRESS_TOOL_NAMES:
            return await self.address_form.execute(name, raw_arguments)
        if name in MEMORY_TOOL_NAMES:
            return await self.memory.execute(name, raw_arguments)
        if name in ORDER_TOOL_NAMES:
            return await self.order.execute(name, raw_arguments)
        if name in ORDER_STATUS_TOOL_NAMES:
            return await self.order_status.execute(name, raw_arguments)
        if name in MEMORY_WRITE_TOOL_NAMES:
            return await self.memory_write.execute(name, raw_arguments)
        if name == "add_to_basket":
            if _option_id(raw_arguments) in self._stale_options:
                return ToolOutcome(
                    model_output={
                        "status": "error",
                        "message": (
                            "That option was found for the previous delivery address. Search "
                            "again and use an option_id from the new results."
                        ),
                    }
                )
            blocked = await self.choices.blocked_add(raw_arguments)
            if blocked is not None:
                return blocked
            outcome = await self.basket.execute(name, raw_arguments)
            return await self.choices.after_add(raw_arguments, outcome)
        if name in BASKET_TOOL_NAMES:
            return await self.basket.execute(name, raw_arguments)
        self.choices.saw_search(raw_arguments)
        outcome = await self.search.execute(name, raw_arguments)
        if name == "search_products":
            # A search for an item read from a product photo is compared with that photo.
            outcome = await self.photo.after_search(raw_arguments, outcome)
        return outcome
