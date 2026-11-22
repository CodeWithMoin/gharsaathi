import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.commerce.query_guard import non_search_reason
from app.commerce.relevance import refine_search_result
from app.commerce.zepto.client import ZeptoMcpClient
from app.commerce.zepto.adapter import parse_addresses, parse_products
from app.commerce.zepto.cart import ZeptoCart, parse_view_cart
from app.commerce.zepto.diagnostics import log_search_unrecognised
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
from app.commerce.zepto.errors import (
    ZeptoAddressInvalid,
    ZeptoProviderError,
    ZeptoRateLimited,
    ZeptoSessionExpired,
    ZeptoUnrecognisedResponse,
)
from app.commerce.zepto.oauth import ZeptoOAuthClient


logger = logging.getLogger(__name__)
T = TypeVar("T")

# Read-only Zepto tools: after a provider error the persistent session is reset
# and the read is retried once. Cart-mutating, address-adding, payment and
# order tools are never in this set and are never replayed.
RECOVERABLE_READ_TOOLS = frozenset(
    {
        "select_saved_address",
        "search_products",
        "search_multiple_products",
        "list_saved_addresses",
        "view_cart",
        "get_order_detail",
        "list_order_history",
        "get_past_order_items",
        "get_product_details",
    }
)


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
        # Saved-address ids Zepto no longer has (from a refresh or a failed selection).
        self._removed_addresses: set[str] = set()
        self._selection_needed = False
        # Called with the verified live list when a selected address is found missing.
        self.on_address_removed: Callable[[AddressList, str], Awaitable[None]] | None = None
        self._background: set[asyncio.Task[None]] = set()

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

    async def _recovering(
        self, tools: tuple[str, ...], operation: Callable[[], Awaitable[T]]
    ) -> T:
        """Run a read-only operation; on a provider error reset the session and retry once."""

        if not set(tools) <= RECOVERABLE_READ_TOOLS:
            raise ValueError("Only read-only Zepto tools may be retried.")
        try:
            return await operation()
        except (ZeptoRateLimited, ZeptoSessionExpired, ZeptoAddressInvalid):
            # Retrying a rate limit extends the block; the client already refreshed an
            # expired session; a missing address needs the user.
            raise
        except ZeptoProviderError as exc:
            logger.warning(
                "event=zepto_session_reset reason=%s tool=%s correlation_id=%s",
                exc.reason,
                getattr(exc, "tool", None) or tools[-1],
                uuid.uuid4().hex,
            )
            self._selected_address = None
            await self.client.reset_session()
            return await operation()

    async def addresses(self) -> AddressList:
        async def read() -> AddressList:
            return parse_addresses(
                await self.client.call_tool("list_saved_addresses", {})
            )

        live = await self._recovering(("list_saved_addresses",), read)
        self._removed_addresses -= {address.id for address in live.addresses}
        return live

    def mark_address_removed(self, address_id: str) -> None:
        """A refreshed snapshot no longer has ``address_id``; searches stop using it."""

        self._removed_addresses.add(address_id)
        self._selection_needed = True
        if self._selected_address is not None and self._selected_address[1] == address_id:
            self._selected_address = None

    def address_selected(self) -> None:
        """The user chose a delivery address again."""

        self._selection_needed = False

    def address_problem(self) -> str | None:
        """Why Zepto has no usable address, when that is known (not just unselected)."""

        return "address_invalid" if self._selection_needed else None

    async def _select_address(self, address_id: str) -> None:
        try:
            await self.client.call_tool("select_saved_address", {"addressId": address_id})
        except ZeptoRateLimited:
            raise
        except ZeptoProviderError as exc:
            # Is the address still in the account? If listing also fails, the
            # original error stands (and the session is reset and retried once).
            live = parse_addresses(await self.client.call_tool("list_saved_addresses", {}))
            if address_id in {address.id for address in live.addresses}:
                raise
            self.mark_address_removed(address_id)
            if self.on_address_removed is not None:
                task = asyncio.create_task(self._notify_removed(live, address_id))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
            raise ZeptoAddressInvalid(
                "The selected Zepto address is no longer saved in the account."
            ) from exc

    async def _notify_removed(self, live: AddressList, address_id: str) -> None:
        assert self.on_address_removed is not None
        try:
            await self.on_address_removed(live, address_id)
        except Exception as exc:  # noqa: BLE001 - the search already reported the problem
            logger.warning("event=zepto_address_removed_apply_failed error=%s", type(exc).__name__)

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

        async def read() -> ZeptoCart:
            raw = await self.client.call_tool("view_cart", {}, retry_on_auth=False)
            return parse_view_cart(raw)

        return await self._recovering(("view_cart",), read)

    async def order_history(
        self, *, limit: int = 10, page_number: int = 1
    ) -> ZeptoOrderHistory:
        async def read() -> ZeptoOrderHistory:
            raw = await self.client.call_tool(
                "list_order_history", {"limit": limit, "pageNumber": page_number}
            )
            return parse_order_history(raw, limit=limit, page_number=page_number)

        return await self._recovering(("list_order_history",), read)

    async def order_detail(self, order_id: str) -> ZeptoOrderDetail:
        async def read() -> ZeptoOrderDetail:
            raw = await self.client.call_tool("get_order_detail", {"orderId": order_id})
            return parse_order_detail(raw)

        return await self._recovering(("get_order_detail",), read)

    async def search(
        self, query: str, address_id: str, page_number: int = 0
    ) -> ProductSearchResult:
        normalized = " ".join(query.split())
        if non_search_reason(normalized) is not None:
            raise ValueError("A conversational message cannot be sent to product search.")
        cache_key = (address_id, normalized.casefold(), page_number)
        queries = _split_queries(normalized)
        tool = "search_multiple_products" if len(queries) > 1 else "search_products"

        if address_id in self._removed_addresses:
            raise ZeptoAddressInvalid(
                "The selected Zepto address is no longer saved in the account."
            )
        self._selection_needed = False

        async def run() -> ProductSearchResult:
            generation = await self.client.prepare_session()
            if self._selected_address != (generation, address_id):
                await self._select_address(address_id)
                self._selected_address = (self.client.session_generation, address_id)
            if len(queries) > 1:
                raw = await self.client.call_tool(
                    tool, {"queries": queries, "pageNumber": page_number}
                )
            else:
                raw = await self.client.call_tool(
                    tool, {"query": queries[0], "pageNumber": page_number}
                )
            try:
                parsed = parse_products(raw)
            except ZeptoUnrecognisedResponse:
                log_search_unrecognised(correlation_id=uuid.uuid4().hex, tool=tool, payload=raw)
                raise
            return refine_search_result(normalized, parsed)

        async with self._search_lock:
            cached = self._search_cache.get(cache_key)
            if cached is not None and time.monotonic() - cached[0] < 60:
                result = cached[1].model_copy(deep=True)
            else:
                # Failures raise before this point, so they are never cached.
                result = await self._recovering(("select_saved_address", tool), run)
                self._search_cache[cache_key] = (
                    time.monotonic(),
                    result.model_copy(deep=True),
                )
        if self.reranker is not None:
            return await self.reranker.rerank(normalized, result)
        return result

    async def prewarm(self, address_id: str) -> None:
        """Open the MCP session and select the address before the first search needs them.

        Read-only setup that voice session start runs in the background. No retry:
        a failure here leaves recovery to the first search.
        """

        if address_id in self._removed_addresses:
            return
        async with self._search_lock:
            generation = await self.client.prepare_session()
            if self._selected_address != (generation, address_id):
                await self._select_address(address_id)
                self._selected_address = (self.client.session_generation, address_id)


def _split_queries(query: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\s*(?:,|\band\b|\baur\b)\s*", query, flags=re.IGNORECASE)]
    return [part for part in parts if part] or [query]
