from app.commerce.swiggy.adapter import SwiggyInstamartAdapter
from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.swiggy.history import SwiggyOrderHistoryAdapter
from app.commerce.swiggy.models import (
    AddressList,
    ConnectionStatus,
    OrderList,
    ProductSearchResult,
    ToolDescription,
)
from app.commerce.swiggy.oauth import SwiggyOAuthClient


class SwiggyService:
    def __init__(
        self,
        oauth: SwiggyOAuthClient,
        client: SwiggyMcpClient,
        adapter: SwiggyInstamartAdapter,
    ) -> None:
        self.oauth = oauth
        self.client = client
        self.adapter = adapter
        self.history = SwiggyOrderHistoryAdapter(client)

    async def connection_status(self) -> ConnectionStatus:
        return await self.oauth.status()

    async def authorization_url(self) -> str:
        return await self.oauth.authorization_url()

    async def complete_authorization(self, code: str, state: str) -> None:
        await self.oauth.exchange_code(code, state)

    async def reject_authorization(self, state: str) -> None:
        await self.oauth.reject_authorization(state)

    async def disconnect(self) -> None:
        await self.oauth.disconnect()

    async def list_tools(self) -> list[ToolDescription]:
        return await self.client.list_tools()

    async def addresses(self, page: int, page_size: int) -> AddressList:
        return await self.adapter.addresses(page, page_size)

    async def search(
        self, query: str, address_id: str, offset: int
    ) -> ProductSearchResult:
        return await self.adapter.search_products(query, address_id, offset)

    async def orders(self, count: int, active_only: bool) -> OrderList:
        return await self.history.orders(count, active_only)
