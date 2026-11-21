"""Session-scoped router for voice shopping tools."""

from typing import Protocol

from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.voice.basket_tools import BASKET_TOOL_DEFINITIONS, BASKET_TOOL_NAMES, BasketToolExecutor
from app.voice.tools import TOOL_DEFINITIONS as SEARCH_TOOL_DEFINITIONS
from app.voice.tools import ProductSearcher, ShoppingToolExecutor, ToolOutcome


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
    ) -> None:
        options = {}
        self.search = ShoppingToolExecutor(searcher, address_id, options)
        self.basket = BasketToolExecutor(baskets, approvals, options, address_id)

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        if name in BASKET_TOOL_NAMES:
            return await self.basket.execute(name, raw_arguments)
        return await self.search.execute(name, raw_arguments)
