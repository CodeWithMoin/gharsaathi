from app.commerce.swiggy.adapter import SwiggyInstamartAdapter
from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.swiggy.models import (
    AddressList,
    ConnectionStatus,
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
        return await self.adapter.search_products(query, address_id, offset)
