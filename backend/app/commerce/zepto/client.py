import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from jsonschema import ValidationError, validate
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.commerce.zepto.errors import (
    ZeptoError,
    ZeptoProviderError,
    ZeptoRateLimited,
    ZeptoSchemaError,
    ZeptoSessionExpired,
)
from app.commerce.zepto.models import ToolDescription
from app.commerce.zepto.oauth import ZeptoOAuthClient
from app.core.config import Settings


T = TypeVar("T")
RATE_LIMIT_COOLDOWN_SECONDS = 300
RATE_LIMIT_MESSAGE = (
    "Zepto is temporarily rate-limiting this connection. Requests are paused "
    "briefly so GharSaathi does not extend the block."
)


@dataclass
class _SessionState:
    access_token: str
    session: ClientSession
    resources: "_Closable"
    catalogue: dict[str, ToolDescription] | None = None


class _Closable(Protocol):
    async def aclose(self) -> None: ...


@dataclass
class _SessionOwner:
    task: asyncio.Task[None]
    stop: asyncio.Event

    async def aclose(self) -> None:
        self.stop.set()
        await self.task


class ZeptoMcpClient:
    def __init__(self, settings: Settings, oauth: ZeptoOAuthClient) -> None:
        self.settings = settings
        self.oauth = oauth
        self._rate_limited_until = 0.0
        self._session: _SessionState | None = None
        self._operation_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._session_generation = 0

    async def _with_session(
        self,
        operation: Callable[[ClientSession], Awaitable[T]],
        *,
        retry_on_auth: bool = True,
    ) -> T:
        async with self._operation_lock:
            persisted_cooldown = await self._persisted_rate_limit_remaining()
            if time.monotonic() < self._rate_limited_until or persisted_cooldown > 0:
                raise ZeptoRateLimited(RATE_LIMIT_MESSAGE)
            access_token = await self.oauth.access_token()
            try:
                return await self._run_session(access_token, operation)
            except ZeptoRateLimited:
                await self._record_rate_limit()
                raise
            except ZeptoError:
                raise
            except Exception as exc:
                if _is_rate_limited(exc):
                    await self._record_rate_limit()
                    raise ZeptoRateLimited(RATE_LIMIT_MESSAGE) from exc
                if _is_authentication_failure(exc):
                    if not retry_on_auth:
                        raise ZeptoProviderError(
                            "Zepto authentication changed during a consequential request. "
                            "The request was not replayed; reconnect and review before trying again."
                        ) from exc
                    return await self._retry_after_refresh(operation)
                if _is_broken_session(exc):
                    if not retry_on_auth:
                        raise ZeptoProviderError(
                            "The Zepto connection was interrupted during a consequential "
                            "request. The request was not replayed; review before trying again."
                        ) from exc
                    return await self._retry_after_reconnect(access_token, operation)
                raise ZeptoProviderError("Zepto MCP request failed.") from exc

    async def _run_session(
        self, access_token: str, operation: Callable[[ClientSession], Awaitable[T]]
    ) -> T:
        state = await self._session_for(access_token)
        return await operation(state.session)

    async def _retry_after_refresh(
        self, operation: Callable[[ClientSession], Awaitable[T]]
    ) -> T:
        await self._invalidate_session()
        refreshed = await self.oauth.refresh()
        try:
            return await self._run_session(refreshed, operation)
        except ZeptoError:
            raise
        except Exception as retry_exc:
            if _is_rate_limited(retry_exc):
                await self._record_rate_limit()
                raise ZeptoRateLimited(RATE_LIMIT_MESSAGE) from retry_exc
            raise ZeptoSessionExpired(
                "Zepto rejected the refreshed session. Connect again."
            ) from retry_exc

    async def _retry_after_reconnect(
        self,
        access_token: str,
        operation: Callable[[ClientSession], Awaitable[T]],
    ) -> T:
        await self._invalidate_session()
        try:
            return await self._run_session(access_token, operation)
        except ZeptoError:
            raise
        except Exception as retry_exc:
            if _is_rate_limited(retry_exc):
                await self._record_rate_limit()
                raise ZeptoRateLimited(RATE_LIMIT_MESSAGE) from retry_exc
            raise ZeptoProviderError("Zepto MCP session could not be restored.") from retry_exc

    async def _session_for(self, access_token: str) -> _SessionState:
        async with self._session_lock:
            if self._session is not None and self._session.access_token == access_token:
                return self._session
            await self._close_session_locked()
            self._session = await self._open_session(access_token)
            return self._session

    async def _open_session(self, access_token: str) -> _SessionState:
        ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        async def own_session() -> None:
            resources = AsyncExitStack()
            try:
                async with resources:
                    read_stream, write_stream, _ = await resources.enter_async_context(
                        streamablehttp_client(
                            self.settings.zepto_mcp_url,
                            headers={"Authorization": f"Bearer {access_token}"},
                        )
                    )
                    session = await resources.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    await session.initialize()
                    ready.set_result(session)
                    await stop.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    raise

        owner_task = asyncio.create_task(own_session())
        try:
            session = await ready
            self._session_generation += 1
            return _SessionState(
                access_token, session, _SessionOwner(owner_task, stop)
            )
        except BaseException:
            stop.set()
            await asyncio.gather(owner_task, return_exceptions=True)
            raise

    async def _catalogue_for(self, session: ClientSession) -> dict[str, ToolDescription]:
        state = self._session
        if state is None or state.session is not session:
            response = await session.list_tools()
            return {
                tool.name: ToolDescription(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.inputSchema,
                )
                for tool in response.tools
            }
        if state.catalogue is None:
            response = await session.list_tools()
            state.catalogue = {
                tool.name: ToolDescription(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.inputSchema,
                )
                for tool in response.tools
            }
        return state.catalogue

    async def _invalidate_session(self) -> None:
        async with self._session_lock:
            await self._close_session_locked()

    async def _close_session_locked(self) -> None:
        if self._session is None:
            return
        state, self._session = self._session, None
        await state.resources.aclose()

    async def aclose(self) -> None:
        async with self._operation_lock:
            await self._invalidate_session()

    async def reset_session(self) -> None:
        """Drop the persistent MCP session; the next call opens a fresh one."""

        await self.aclose()

    async def _record_rate_limit(self) -> None:
        self._rate_limited_until = max(
            self._rate_limited_until,
            time.monotonic() + RATE_LIMIT_COOLDOWN_SECONDS,
        )
        record = getattr(self.oauth, "record_rate_limit", None)
        if record is not None:
            await record(RATE_LIMIT_COOLDOWN_SECONDS)

    async def _persisted_rate_limit_remaining(self) -> int:
        remaining = getattr(self.oauth, "rate_limit_remaining", None)
        return await remaining() if remaining is not None else 0

    async def prepare_session(self) -> int:
        """Ensure one authenticated transport exists without listing or calling tools."""

        async with self._operation_lock:
            persisted_cooldown = await self._persisted_rate_limit_remaining()
            if time.monotonic() < self._rate_limited_until or persisted_cooldown > 0:
                raise ZeptoRateLimited(RATE_LIMIT_MESSAGE)
            access_token = await self.oauth.access_token()
            await self._session_for(access_token)
            return self._session_generation

    @property
    def session_generation(self) -> int:
        return self._session_generation

    async def list_tools(self) -> list[ToolDescription]:
        async def run(session: ClientSession) -> list[ToolDescription]:
            return list((await self._catalogue_for(session)).values())

        return await self._with_session(run)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        async def run(session: ClientSession) -> dict[str, Any]:
            tool = (await self._catalogue_for(session)).get(name)
            if tool is None:
                raise ZeptoSchemaError(
                    f"The authenticated Zepto catalogue does not expose {name}."
                )
            try:
                validate(instance=arguments, schema=tool.input_schema)
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
                            if _value_is_rate_limited(text):
                                raise ZeptoRateLimited(RATE_LIMIT_MESSAGE)
                            raise ZeptoProviderError(
                                "Zepto could not complete the request."
                            )
                        # Some Zepto read tools currently return successful MCP
                        # text content without structuredContent. Preserve it in
                        # a typed envelope instead of treating success as a
                        # protocol failure. Provider text remains untrusted data.
                        structured = {"content_text": text}
            if is_error:
                if _value_is_rate_limited(structured):
                    raise ZeptoRateLimited(RATE_LIMIT_MESSAGE)
                raise ZeptoProviderError(_safe_error_message(structured))
            if not isinstance(structured, dict):
                raise ZeptoSchemaError(
                    f"Zepto {name} returned no structured result."
                )
            if structured.get("success") is False:
                if _value_is_rate_limited(structured):
                    raise ZeptoRateLimited(RATE_LIMIT_MESSAGE)
                raise ZeptoProviderError(_safe_error_message(structured))
            return structured

        try:
            return await self._with_session(run, retry_on_auth=retry_on_auth)
        except ZeptoError as exc:
            # The tool name only; recovery logs never carry provider text.
            exc.tool = name  # type: ignore[attr-defined]
            raise


def _safe_error_message(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return "Zepto could not complete the request."


def _value_is_rate_limited(value: object) -> bool:
    if isinstance(value, str):
        message = value.casefold()
        return any(
            marker in message
            for marker in ("429", "too many requests", "rate limited", "rate_limit")
        )
    if isinstance(value, dict):
        return any(_value_is_rate_limited(item) for item in value.values())
    if isinstance(value, list):
        return any(_value_is_rate_limited(item) for item in value)
    return False


def _exception_text(error: BaseException) -> str:
    messages = [str(error)]
    for nested in getattr(error, "exceptions", ()):
        messages.append(_exception_text(nested))
    if error.__cause__ is not None:
        messages.append(_exception_text(error.__cause__))
    return "\n".join(messages)


def _is_rate_limited(error: BaseException) -> bool:
    message = _exception_text(error).lower()
    return "429" in message or "too many requests" in message


def _is_authentication_failure(error: BaseException) -> bool:
    message = _exception_text(error).lower()
    return "401" in message or "unauthorized" in message or "-32001" in message


def _is_broken_session(error: BaseException) -> bool:
    message = _exception_text(error).lower()
    name = type(error).__name__.lower()
    markers = (
        "connection reset",
        "connection closed",
        "connection lost",
        "stream closed",
        "endofstream",
        "closedresource",
        "broken resource",
        "peer closed",
    )
    return any(marker in message or marker in name for marker in markers)
