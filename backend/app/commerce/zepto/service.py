from app.commerce.zepto.client import ZeptoMcpClient
from app.commerce.zepto.models import ConnectionStatus, ToolDescription
from app.commerce.zepto.oauth import ZeptoOAuthClient


class ZeptoService:
    def __init__(self, oauth: ZeptoOAuthClient, client: ZeptoMcpClient) -> None:
        self.oauth = oauth
        self.client = client

    async def connection_status(self) -> ConnectionStatus:
        return await self.oauth.status()

    async def authorization_url(self) -> str:
        return await self.oauth.authorization_url()

    async def complete_authorization(
        self, code: str, state: str, issuer: str | None
    ) -> None:
        await self.oauth.exchange_code(code, state, issuer)

    async def reject_authorization(self, state: str) -> None:
        await self.oauth.reject_authorization(state)

    async def disconnect(self) -> None:
        await self.oauth.disconnect()

    async def list_tools(self) -> list[ToolDescription]:
        return await self.client.list_tools()
