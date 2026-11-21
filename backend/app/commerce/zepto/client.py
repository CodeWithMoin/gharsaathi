import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from jsonschema import ValidationError, validate
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.commerce.zepto.errors import (
    ZeptoError,
    ZeptoProviderError,
    ZeptoSchemaError,
)
from app.commerce.zepto.models import ToolDescription
from app.commerce.zepto.oauth import ZeptoOAuthClient
from app.core.config import Settings


T = TypeVar("T")


class ZeptoMcpClient:
    def __init__(self, settings: Settings, oauth: ZeptoOAuthClient) -> None:
        self.settings = settings
        self.oauth = oauth

    async def _with_session(
        self, operation: Callable[[ClientSession], Awaitable[T]]
    ) -> T:
        access_token = await self.oauth.access_token()
        try:
            return await self._run_session(access_token, operation)
        except ZeptoError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "401" in message or "unauthorized" in message or "-32001" in message:
                refreshed = await self.oauth.refresh()
                try:
                    return await self._run_session(refreshed, operation)
                except Exception as retry_exc:
                    raise ZeptoProviderError(
                        "Zepto rejected the refreshed session. Connect again."
                    ) from retry_exc
            raise ZeptoProviderError("Zepto MCP request failed.") from exc

    async def _run_session(
        self, access_token: str, operation: Callable[[ClientSession], Awaitable[T]]
    ) -> T:
        async with streamablehttp_client(
            self.settings.zepto_mcp_url,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await operation(session)

    async def list_tools(self) -> list[ToolDescription]:
        async def run(session: ClientSession) -> list[ToolDescription]:
            response = await session.list_tools()
            return [
                ToolDescription(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.inputSchema,
                )
                for tool in response.tools
            ]

        return await self._with_session(run)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def run(session: ClientSession) -> dict[str, Any]:
            catalogue = await session.list_tools()
            tool = next((item for item in catalogue.tools if item.name == name), None)
            if tool is None:
                raise ZeptoSchemaError(
                    f"The authenticated Zepto catalogue does not expose {name}."
                )
            try:
                validate(instance=arguments, schema=tool.inputSchema)
            except ValidationError as exc:
                raise ZeptoSchemaError(
                    f"Arguments do not match Zepto's live {name} schema: {exc.message}"
                ) from exc

            result = await session.call_tool(name, arguments)
            is_error = bool(getattr(result, "isError", False))
            structured = getattr(result, "structuredContent", None)
            content = getattr(result, "content", []) or []
            if structured is None:
                text = next(
                    (item.text for item in content if hasattr(item, "text")), None
                )
                if text:
                    try:
                        structured = json.loads(text)
                    except json.JSONDecodeError:
                        if is_error:
                            raise ZeptoProviderError(text)
            if is_error:
                raise ZeptoProviderError(_safe_error_message(structured))
            if not isinstance(structured, dict):
                raise ZeptoSchemaError(
                    f"Zepto {name} returned no structured result."
                )
            return structured

        return await self._with_session(run)


def _safe_error_message(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return "Zepto could not complete the request."
