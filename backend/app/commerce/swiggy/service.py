import asyncio
import time
from typing import Protocol

from app.commerce.query_guard import non_search_reason
from app.commerce.relevance import refine_search_result
from app.commerce.swiggy.adapter import SwiggyInstamartAdapter
from app.commerce.swiggy.client import SwiggyMcpClient, _rate_limit_error
from app.commerce.swiggy.models import (
    AddressList,
    ConnectionStatus,
    ProductSearchResult,
    ToolDescription,
)
from app.commerce.swiggy.oauth import SwiggyOAuthClient


class ProductReranker(Protocol):
    async def rerank(
        self, query: str, result: ProductSearchResult
    ) -> ProductSearchResult: ...


class SwiggyService:
    def __init__(
        self,
        oauth: SwiggyOAuthClient,
        client: SwiggyMcpClient,
        adapter: SwiggyInstamartAdapter,
        reranker: ProductReranker | None = None,
    ) -> None:
        self.oauth = oauth
        self.client = client
        self.adapter = adapter
        self.reranker = reranker
        self._search_cache: dict[
            tuple[str, str, int], tuple[float, ProductSearchResult]
        ] = {}
        self._search_lock = asyncio.Lock()

    async def connection_status(self) -> ConnectionStatus:
        return await self.oauth.status()

    async def authorization_url(self) -> str:
        return await self.oauth.authorization_url()

    async def complete_authorization(self, code: str, state: str) -> None:
        await self.oauth.exchange_code(code, state)
        # A freshly linked account must not inherit an older account's MCP
        # transport or tool catalogue.
        await self.client.aclose()

    async def reject_authorization(self, state: str) -> None:
        await self.oauth.reject_authorization(state)

    async def disconnect(self) -> None:
        # Close the authenticated transport before revoking/removing its token.
        await self.client.aclose()
        await self.oauth.disconnect()

    async def list_tools(self) -> list[ToolDescription]:
        return await self.client.list_tools()

    async def addresses(self, page: int, page_size: int) -> AddressList:
        return await self.adapter.addresses(page, page_size)

    async def search(
        self, query: str, address_id: str, offset: int
    ) -> ProductSearchResult:
        normalized = " ".join(query.split())
        if non_search_reason(normalized) is not None:
            raise ValueError("A conversational message cannot be sent to product search.")
        cache_key = (address_id, normalized.casefold(), offset)
        cached = self._search_cache.get(cache_key)
        if cached is None or time.monotonic() - cached[0] >= 60:
            # A known cooldown answers at once instead of queueing behind
            # another Swiggy request for the provider lock.
            cooldown = getattr(self.client, "cooldown_remaining", None)
            remaining = await cooldown() if cooldown is not None else 0
            if remaining > 0:
                raise _rate_limit_error(remaining)
        async with self._search_lock:
            cached = self._search_cache.get(cache_key)
            if cached is not None and time.monotonic() - cached[0] < 60:
                result = cached[1].model_copy(deep=True)
            else:
                result = refine_search_result(
                    normalized,
                    await self.adapter.search_products(normalized, address_id, offset),
                )
                self._search_cache[cache_key] = (
                    time.monotonic(),
                    result.model_copy(deep=True),
                )
        if self.reranker is not None:
            return await self.reranker.rerank(normalized, result)
        return result
