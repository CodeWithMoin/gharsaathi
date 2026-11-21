"""Session-scoped router for voice shopping tools."""

from typing import Protocol

from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.voice.basket_tools import BASKET_TOOL_DEFINITIONS, BASKET_TOOL_NAMES, BasketToolExecutor
from app.voice.tools import TOOL_DEFINITIONS as SEARCH_TOOL_DEFINITIONS
from app.voice.tools import (
    MultiProviderShoppingToolExecutor,
    ProductSearcher,
    ProviderSearchTarget,
    ToolOutcome,
)


VOICE_TOOL_DEFINITIONS = [*SEARCH_TOOL_DEFINITIONS, *BASKET_TOOL_DEFINITIONS]


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
    ) -> None:
        options = {}
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
                )
            )
        self.search = MultiProviderShoppingToolExecutor(targets, options)
        self.basket = BasketToolExecutor(baskets, approvals, options, address_id)

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name in BASKET_TOOL_NAMES:
            return await self.basket.execute(name, raw_arguments)
        return await self.search.execute(name, raw_arguments)
