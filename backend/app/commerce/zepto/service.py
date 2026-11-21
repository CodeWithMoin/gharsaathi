import asyncio
import re
import time
from typing import Any

from app.commerce.query_guard import non_search_reason
from app.commerce.relevance import refine_search_result
from app.commerce.zepto.client import ZeptoMcpClient
from app.commerce.zepto.adapter import parse_addresses, parse_products
from app.commerce.zepto.cart import ZeptoCart, parse_view_cart
from app.commerce.zepto.history import (
    ZeptoOrderDetail,
    ZeptoOrderHistory,
    parse_order_detail,
    parse_order_history,
)
from app.commerce.swiggy.models import AddressList, ProductSearchResult
from app.commerce.swiggy.service import ProductReranker
from app.commerce.zepto.models import (
    CapabilitySummary,
    ConnectionStatus,
    ToolDescription,
)
from app.commerce.zepto.oauth import ZeptoOAuthClient


class ZeptoService:
    def __init__(
        self,
        oauth: ZeptoOAuthClient,
        client: ZeptoMcpClient,
        reranker: ProductReranker | None = None,
    ) -> None:
        self.oauth = oauth
        self.client = client
        self.reranker = reranker
        self._selected_address: tuple[int, str] | None = None
        self._search_cache: dict[tuple[str, str, int], tuple[float, ProductSearchResult]] = {}
        self._search_lock = asyncio.Lock()

    async def connection_status(self) -> ConnectionStatus:
        return await self.oauth.status()

    async def authorization_url(self) -> str:
        return await self.oauth.authorization_url()

    async def complete_authorization(
        self, code: str, state: str, issuer: str | None
    ) -> None:
        await self.client.aclose()
        await self.oauth.exchange_code(code, state, issuer)

    async def reject_authorization(self, state: str) -> None:
        await self.oauth.reject_authorization(state)

    async def disconnect(self) -> None:
        await self.client.aclose()
        await self.oauth.disconnect()

    async def list_tools(self) -> list[ToolDescription]:
        return await self.client.list_tools()

    async def capabilities(self) -> CapabilitySummary:
        """Return discovery information without exposing a provider action."""

        status = await self.connection_status()
        if not status.connected:
            return CapabilitySummary(connected=False, catalogue_discovered=False)

        tools = await self.list_tools()
        return CapabilitySummary(
            connected=True,
            catalogue_discovered=True,
            remote_tool_names=sorted({tool.name for tool in tools}),
            exposed_actions=[
                "connect",
                "disconnect",
                "inspect_catalogue",
                "view_cart",
                "add_saved_address",
                "list_order_history",
                "quote_cod_order",
                "confirm_approved_cod_order",
            ],
            cart_actions_exposed=True,
            cart_read_exposed=True,
            order_history_exposed=True,
            checkout_exposed=True,
        )

    async def addresses(self) -> AddressList:
        return parse_addresses(await self.client.call_tool("list_saved_addresses", {}))

    async def add_address(self, arguments: dict[str, Any]) -> AddressList:
        """Add one user-confirmed address, then fetch provider truth.

        The mutation is never replayed after an authentication or transport
        failure. If the outcome is unclear, callers should refresh the address
        list instead of submitting the same address a second time.
        """

        await self.client.call_tool(
            "add_saved_address", arguments, retry_on_auth=False
        )
        self._selected_address = None
        return await self.addresses()

    async def cart(self) -> ZeptoCart:
        """Read the provider cart without replaying a failed MCP request."""

        raw = await self.client.call_tool("view_cart", {}, retry_on_auth=False)
        return parse_view_cart(raw)

    async def order_history(
        self, *, limit: int = 10, page_number: int = 1
    ) -> ZeptoOrderHistory:
        raw = await self.client.call_tool(
            "list_order_history", {"limit": limit, "pageNumber": page_number}
        )
        return parse_order_history(raw, limit=limit, page_number=page_number)

    async def order_detail(self, order_id: str) -> ZeptoOrderDetail:
        raw = await self.client.call_tool("get_order_detail", {"orderId": order_id})
        return parse_order_detail(raw)

    async def search(
        self, query: str, address_id: str, page_number: int = 0
    ) -> ProductSearchResult:
        normalized = " ".join(query.split())
        if non_search_reason(normalized) is not None:
            raise ValueError("A conversational message cannot be sent to product search.")
        cache_key = (address_id, normalized.casefold(), page_number)
        async with self._search_lock:
            cached = self._search_cache.get(cache_key)
            if cached is not None and time.monotonic() - cached[0] < 60:
                result = cached[1].model_copy(deep=True)
            else:
                generation = await self.client.prepare_session()
                if self._selected_address != (generation, address_id):
                    await self.client.call_tool(
                        "select_saved_address", {"addressId": address_id}
                    )
                    self._selected_address = (self.client.session_generation, address_id)
                    generation = self.client.session_generation

                queries = _split_queries(normalized)
                if len(queries) > 1:
                    raw = await self.client.call_tool(
                        "search_multiple_products",
                        {"queries": queries, "pageNumber": page_number},
                    )
                else:
                    raw = await self.client.call_tool(
                        "search_products",
                        {"query": queries[0], "pageNumber": page_number},
                    )
                result = refine_search_result(normalized, parse_products(raw))
                self._search_cache[cache_key] = (
                    time.monotonic(),
                    result.model_copy(deep=True),
                )
        if self.reranker is not None:
            return await self.reranker.rerank(normalized, result)
        return result


def _split_queries(query: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\s*(?:,|\band\b|\baur\b)\s*", query, flags=re.IGNORECASE)]
    return [part for part in parts if part] or [query]
