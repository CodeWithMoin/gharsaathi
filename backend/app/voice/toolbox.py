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
from app.voice.memory_tools import (
    MEMORY_TOOL_DEFINITIONS,
    MEMORY_TOOL_NAMES,
    NO_MEMORY_NOTE,
    MemoryToolExecutor,
)
from app.shopping.models import ProviderName
from app.shopping.order_confirmation import OrderConfirmations
from app.voice.order_tools import ORDER_TOOL_DEFINITIONS, ORDER_TOOL_NAMES, OrderToolExecutor
from app.voice.compare_tools import COMPARE_TOOL_DEFINITIONS, COMPARE_TOOL_NAMES, CompareToolExecutor
from app.voice.express_tools import EXPRESS_TOOL_DEFINITIONS, EXPRESS_TOOL_NAMES, ExpressToolExecutor
from app.voice.photo_tools import PHOTO_TOOL_DEFINITIONS, PHOTO_TOOL_NAMES, PhotoReading, PhotoToolExecutor
from app.voice.order_status_tools import (
    ORDER_STATUS_TOOL_DEFINITIONS,
    ORDER_STATUS_TOOL_NAMES,
    OrderHistoryReader,
    OrderStatusToolExecutor,
)
from app.tracking.service import OrderTrackingService
from app.shopping.saved_lists import SavedListStore, StoreTarget
from app.voice.saved_list_tools import (
    SAVED_LIST_TOOL_DEFINITIONS,
    SAVED_LIST_TOOL_NAMES,
    SavedListToolExecutor,
)
from app.voice.tools import TOOL_DEFINITIONS as SEARCH_TOOL_DEFINITIONS
from app.voice.tools import (
    MultiProviderShoppingToolExecutor,
    ProductSearcher,
    ProviderSearchTarget,
    SearchOption,
    ToolOutcome,
)


SEARCH_TOOL_NAME = "search_products"
NO_MEMORY_SEARCHED_NOTE = (
    "Nothing remembered for this item, so it was already searched: 'search' holds the "
    "search_products result and its options are on the user's screen. Do not search this "
    "item again; reply from these results, and for several items ask one combined question, "
    "for example 'Atta aur dahi ke options screen par hain; kaunsa lena hai?'. Do not ask "
    "brand and size per item."
)
NO_MEMORY_SEARCH_FAILED_NOTE = (
    "Nothing remembered for this item. The automatic search in 'search' found no usable "
    "results; follow its status and message."
)

VOICE_TOOL_DEFINITIONS = [
    *SEARCH_TOOL_DEFINITIONS,
    *BASKET_TOOL_DEFINITIONS,
    *ADDRESS_TOOL_DEFINITIONS,
    *MEMORY_TOOL_DEFINITIONS,
    *MEMORY_WRITE_TOOL_DEFINITIONS,
    *ORDER_TOOL_DEFINITIONS,
    *EXPRESS_TOOL_DEFINITIONS,
    *ADDRESS_BOOK_TOOL_DEFINITIONS,
    *PHOTO_TOOL_DEFINITIONS,
    *ORDER_STATUS_TOOL_DEFINITIONS,
    *SAVED_LIST_TOOL_DEFINITIONS,
    *COMPARE_TOOL_DEFINITIONS,
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
        saved_lists: SavedListStore | None = None,
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
        self.express = ExpressToolExecutor(self)
        self.order_status = OrderStatusToolExecutor(
            order_tracking,
            order_history,
            household_id,
            checkouts=getattr(order_confirmations, "reconciler", None),
            on_reconciled=(
                (lambda outcome: order_confirmations.notify_reconciled(outcome, announce=False))
                if order_confirmations is not None
                else None
            ),
        )

        self.saved_lists = SavedListToolExecutor(
            saved_lists, baskets, self._list_targets, household_id
        )
        self.compare = CompareToolExecutor(
            options, self.search.shown, lambda option_id: option_id in self._stale_options
        )

    def _list_targets(self) -> dict[ProviderName, StoreTarget]:
        # The session's current stores and addresses (use_address updates them).
        return {
            target.provider: StoreTarget(target.provider, target.searcher, target.address_id)
            for target in self.search.targets
        }

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
            return await self._recall_then_search(name, raw_arguments)
        if name in ORDER_TOOL_NAMES:
            return await self.order.execute(name, raw_arguments)
        if name in EXPRESS_TOOL_NAMES:
            return await self.express.execute(name, raw_arguments)
        if name in SAVED_LIST_TOOL_NAMES:
            return await self.saved_lists.execute(name, raw_arguments)
        if name in COMPARE_TOOL_NAMES:
            return await self.compare.execute(name, raw_arguments)
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
            # Photo matching may reorder the cards; compare_options follows the screen.
            self.search.shown.record_results(outcome.model_output)
        return outcome

    async def _recall_then_search(self, name: str, raw_arguments: str) -> ToolOutcome:
        """Recall; when nothing is remembered, search the item in the same tool call.

        The prompt searches right after a no_memory recall anyway, so doing it
        here saves one backend model round. Remembered items still stop at the
        recall, so the user is asked about their past choice before any search.
        """

        recalled = await self.memory.execute(name, raw_arguments)
        item = recalled.model_output.get("item")
        if recalled.model_output.get("status") != "no_memory" or not isinstance(item, str):
            return recalled
        query = json.dumps({"query": item})
        self.choices.saw_search(query)
        searched = await self.photo.after_search(
            query, await self.search.execute(SEARCH_TOOL_NAME, query)
        )
        self.search.shown.record_results(searched.model_output)
        searched_ok = searched.model_output.get("status") == "ok"
        note = recalled.model_output.get("note", "")
        return ToolOutcome(
            model_output={
                **recalled.model_output,
                "note": (NO_MEMORY_SEARCHED_NOTE if searched_ok else NO_MEMORY_SEARCH_FAILED_NOTE)
                + note.removeprefix(NO_MEMORY_NOTE),
                "search": searched.model_output,
            },
            ui_event=searched.ui_event,
        )
