"""Session-scoped router for voice shopping tools."""

from typing import Protocol

from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
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
from app.memory.recall import PreferenceRecaller
from app.voice.memory_tools import MEMORY_TOOL_DEFINITIONS, MEMORY_TOOL_NAMES, MemoryToolExecutor
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
]


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
        self.address_form = AddressFormToolExecutor(zepto_connection)
        self.memory = MemoryToolExecutor(preference_recall, household_id)

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name in ADDRESS_TOOL_NAMES:
            return await self.address_form.execute(name, raw_arguments)
        if name in MEMORY_TOOL_NAMES:
            return await self.memory.execute(name, raw_arguments)
        if name in BASKET_TOOL_NAMES:
            return await self.basket.execute(name, raw_arguments)
        return await self.search.execute(name, raw_arguments)
