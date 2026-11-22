import asyncio
import json
import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, TypeVar

import httpx
from jsonschema import ValidationError, validate
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.commerce.swiggy.errors import (
    SwiggyError,
    SwiggyProviderError,
    SwiggyRateLimited,
    SwiggySchemaError,
)
from app.commerce.swiggy.models import ToolDescription
from app.commerce.swiggy.oauth import SwiggyOAuthClient
from app.core.config import Settings


T = TypeVar("T")
logger = logging.getLogger(__name__)
RATE_LIMIT_MESSAGE = (
    "Swiggy is rate-limiting this account. Requests are paused to avoid "
    "extending the block. "
    "Your Zepto connection is unaffected."
)


@dataclass
class _RateLimitObservation:
    saw_429: bool = False
    retry_after_seconds: int | None = None

    def reset(self) -> None:
        self.saw_429 = False
        self.retry_after_seconds = None

    async def capture(self, response: httpx.Response) -> None:
        if response.status_code != 429:
            return
        self.saw_429 = True
        self.retry_after_seconds = _parse_retry_after(response.headers.get("Retry-After"))


@dataclass
class _SessionState:
    """One authenticated MCP transport and its discovered tool catalogue.

    The local-development connection store currently represents one linked
    Swiggy account. Keeping the state on this client therefore gives that
    account one persistent MCP session. When the account token changes, the
    old transport is closed before a new one is opened.
    """

    access_token: str
    session: ClientSession
    resources: "_Closable"
    catalogue: dict[str, ToolDescription] | None = None


class _Closable(Protocol):
    async def aclose(self) -> None: ...


@dataclass
class _SessionOwner:
    """Own the MCP contexts on the same task that entered them."""

    task: asyncio.Task[None]
    stop: asyncio.Event

    async def aclose(self) -> None:
        self.stop.set()
        await self.task


class SwiggyMcpClient:
    def __init__(self, settings: Settings, oauth: SwiggyOAuthClient) -> None:
        self.settings = settings
        self.oauth = oauth
        self._rate_limited_until = 0.0
        self._rate_limit_observation = _RateLimitObservation()
        self._session: _SessionState | None = None
        # MCP ClientSession request handling and session teardown must not race.
        # Serialising provider operations is also intentional: it prevents a
        # page-load burst from creating several sessions for the same account.
        self._operation_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()

    async def _with_session(
        self,
        operation: Callable[[ClientSession], Awaitable[T]],
        *,
        retry_on_auth: bool = True,
        operation_name: str = "mcp_request",
    ) -> T:
        async with self._operation_lock:
            correlation_id = str(uuid.uuid4())
            self._rate_limit_observation.reset()
            remaining_cooldown = max(
                self._rate_limited_until - time.monotonic(),
                await self._persisted_rate_limit_remaining(),
            )
            if remaining_cooldown > 0:
                raise _rate_limit_error(math.ceil(remaining_cooldown))
            access_token = await self.oauth.access_token()
            try:
                return await self._run_session(access_token, operation)
            except SwiggyRateLimited as exc:
                await self._register_rate_limit(
                    exc.retry_after_seconds,
                    operation_name=operation_name,
                    correlation_id=correlation_id,
                    source="tool_result",
                )
                raise
            except SwiggyError:
                raise
            except Exception as exc:
                limited, retry_after, source = _rate_limit_details(
                    exc, self._rate_limit_observation
                )
                if limited:
                    retry_after = (
                        retry_after
                        or self.settings.swiggy_rate_limit_fallback_seconds
                    )
                    await self._register_rate_limit(
                        retry_after,
                        operation_name=operation_name,
                        correlation_id=correlation_id,
                        source=source,
                    )
                    raise _rate_limit_error(retry_after) from exc
                if _is_authentication_failure(exc):
                    if not retry_on_auth:
                        raise SwiggyProviderError(
                            "Swiggy authentication changed during a consequential request. "
                            "The request was not replayed; review the cart and try again."
                        ) from exc
                    return await self._retry_after_refresh(operation)
                if _is_broken_session(exc):
                    if not retry_on_auth:
                        raise SwiggyProviderError(
                            "The Swiggy connection was interrupted during a consequential "
                            "request. The request was not replayed; review the cart and try again."
                        ) from exc
                    return await self._retry_after_reconnect(access_token, operation)
                raise SwiggyProviderError("Swiggy MCP request failed.") from exc

    async def _register_rate_limit(
        self,
        retry_after_seconds: int,
        *,
        operation_name: str,
        correlation_id: str,
        source: str,
    ) -> None:
        self._rate_limited_until = max(
            self._rate_limited_until,
            time.monotonic() + retry_after_seconds,
        )
        record = getattr(self.oauth, "record_rate_limit", None)
        if record is not None:
            await record(retry_after_seconds)
        logger.warning(
            "event=swiggy_mcp_call operation=%s correlation_id=%s "
            "status=rate_limited retry_after_seconds=%d retry_after_source=%s",
            operation_name,
            correlation_id,
            retry_after_seconds,
            source,
        )

    async def cooldown_remaining(self) -> int:
        """Seconds left in a known rate-limit cooldown; no network call."""

        return math.ceil(
            max(
                self._rate_limited_until - time.monotonic(),
                await self._persisted_rate_limit_remaining(),
                0,
            )
        )

    async def _persisted_rate_limit_remaining(self) -> int:
        remaining = getattr(self.oauth, "rate_limit_remaining", None)
        return await remaining() if remaining is not None else 0

    async def _run_session(
        self, access_token: str, operation: Callable[[ClientSession], Awaitable[T]]
    ) -> T:
        """Run against the persistent session for this linked account."""

        state = await self._session_for(access_token)
        return await operation(state.session)

    async def _retry_after_refresh(
        self, operation: Callable[[ClientSession], Awaitable[T]]
    ) -> T:
        await self._invalidate_session()
        refreshed = await self.oauth.refresh()
        try:
            return await self._run_session(refreshed, operation)
        except SwiggyRateLimited as exc:
            await self._register_rate_limit(
                exc.retry_after_seconds,
                operation_name="oauth_refresh_retry",
                correlation_id=str(uuid.uuid4()),
                source="tool_result",
            )
            raise
        except SwiggyError:
            raise
        except Exception as retry_exc:
            limited, retry_after, source = _rate_limit_details(
                retry_exc, self._rate_limit_observation
            )
            if limited:
                retry_after = (
                    retry_after or self.settings.swiggy_rate_limit_fallback_seconds
                )
                await self._register_rate_limit(
                    retry_after,
                    operation_name="oauth_refresh_retry",
                    correlation_id=str(uuid.uuid4()),
                    source=source,
                )
                raise _rate_limit_error(retry_after) from retry_exc
            raise SwiggyProviderError(
                "Swiggy rejected the refreshed session. Connect again."
            ) from retry_exc

    async def _retry_after_reconnect(
        self,
        access_token: str,
        operation: Callable[[ClientSession], Awaitable[T]],
    ) -> T:
        """Reconnect once for idempotent reads after a transport/session failure."""

        await self._invalidate_session()
        try:
            return await self._run_session(access_token, operation)
        except SwiggyRateLimited as exc:
            await self._register_rate_limit(
                exc.retry_after_seconds,
                operation_name="session_reconnect_retry",
                correlation_id=str(uuid.uuid4()),
                source="tool_result",
            )
            raise
        except SwiggyError:
            raise
        except Exception as retry_exc:
            limited, retry_after, source = _rate_limit_details(
                retry_exc, self._rate_limit_observation
            )
            if limited:
                retry_after = (
                    retry_after or self.settings.swiggy_rate_limit_fallback_seconds
                )
                await self._register_rate_limit(
                    retry_after,
                    operation_name="session_reconnect_retry",
                    correlation_id=str(uuid.uuid4()),
                    source=source,
                )
                raise _rate_limit_error(retry_after) from retry_exc
            raise SwiggyProviderError("Swiggy MCP session could not be restored.") from retry_exc

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

            def http_client_factory(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
            ) -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                    follow_redirects=True,
                    event_hooks={"response": [self._rate_limit_observation.capture]},
                )

            try:
                async with resources:
                    read_stream, write_stream, _ = await resources.enter_async_context(
                        streamablehttp_client(
                            self.settings.swiggy_mcp_url,
                            headers={"Authorization": f"Bearer {access_token}"},
                            httpx_client_factory=http_client_factory,
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
            # This branch is only useful for narrow unit-test doubles overriding
            # _run_session; production always has a persistent _SessionState.
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
        """Close the active transport. Safe to call repeatedly at app shutdown."""

        async with self._operation_lock:
            await self._invalidate_session()

    async def list_tools(self) -> list[ToolDescription]:
        async def run(session: ClientSession) -> list[ToolDescription]:
            return list((await self._catalogue_for(session)).values())

        return await self._with_session(run, operation_name="tools/list")

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
                raise SwiggySchemaError(
                    f"The authenticated Swiggy catalogue does not expose {name}."
                )
            try:
                validate(instance=arguments, schema=tool.input_schema)
            except ValidationError as exc:
                raise SwiggySchemaError(
                    f"Arguments do not match Swiggy's live {name} schema: {exc.message}"
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
                            limited, retry_after = _rate_limit_from_value(text)
                            if limited:
                                raise _rate_limit_error(
                                    retry_after
                                    or self.settings.swiggy_rate_limit_fallback_seconds
                                )
                            raise SwiggyProviderError(text)
            if is_error:
                limited, retry_after = _rate_limit_from_value(structured)
                if limited:
                    raise _rate_limit_error(
                        retry_after
                        or self.settings.swiggy_rate_limit_fallback_seconds
                    )
                raise SwiggyProviderError(_safe_error_message(structured))
            if not isinstance(structured, dict):
                raise SwiggySchemaError(
                    f"Swiggy {name} returned no structured result."
                )
            if structured.get("success") is False:
                limited, retry_after = _rate_limit_from_value(structured)
                if limited:
                    raise _rate_limit_error(
                        retry_after
                        or self.settings.swiggy_rate_limit_fallback_seconds
                    )
                raise SwiggyProviderError(_safe_error_message(structured))
            return structured

        return await self._with_session(
            run,
            retry_on_auth=retry_on_auth,
            operation_name=f"tools/call:{name}",
        )


def _rate_limit_error(retry_after_seconds: int) -> SwiggyRateLimited:
    return SwiggyRateLimited(
        f"{RATE_LIMIT_MESSAGE} Try again in {retry_after_seconds} seconds.",
        retry_after_seconds,
    )


def _parse_retry_after(value: str | None, *, now: datetime | None = None) -> int | None:
    if value is None:
        return None
    try:
        return max(1, math.ceil(float(value.strip())))
    except (ValueError, OverflowError):
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        return max(1, math.ceil((retry_at - current).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def _rate_limit_from_value(value: object) -> tuple[bool, int | None]:
    if isinstance(value, dict):
        error = value.get("error")
        candidates = [value]
        if isinstance(error, dict):
            candidates.append(error)
            if isinstance(error.get("data"), dict):
                candidates.append(error["data"])
        limited = any(candidate.get("code") == -32000 for candidate in candidates)
        retry_after: int | None = None
        messages: list[str] = []
        for candidate in candidates:
            if isinstance(candidate.get("message"), str):
                messages.append(candidate["message"])
            for key in ("retryAfter", "retry_after", "retryAfterSeconds"):
                raw = candidate.get(key)
                if isinstance(raw, int | float | str):
                    retry_after = _parse_retry_after(str(raw))
                    if retry_after is not None:
                        break
        if limited:
            return True, retry_after
        value = " ".join(messages)
    if isinstance(value, str):
        lowered = value.lower()
        return (
            "429" in lowered
            or "too many requests" in lowered
            or "rate limit" in lowered
            or "rate-limit" in lowered,
            None,
        )
    return False, None


def _safe_error_message(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return "Swiggy could not complete the request."


def _exception_text(error: BaseException) -> str:
    """Flatten nested task-group failures without exposing request headers."""

    messages = [str(error)]
    for nested in getattr(error, "exceptions", ()):
        messages.append(_exception_text(nested))
    if error.__cause__ is not None:
        messages.append(_exception_text(error.__cause__))
    return "\n".join(messages)


def _exception_tree(error: BaseException) -> list[BaseException]:
    found: list[BaseException] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        pending.extend(getattr(current, "exceptions", ()))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return found


def _rate_limit_details(
    error: BaseException,
    observation: _RateLimitObservation,
) -> tuple[bool, int | None, str]:
    if observation.retry_after_seconds is not None:
        return True, observation.retry_after_seconds, "http_retry_after"
    if observation.saw_429:
        return True, None, "http_header_missing_fallback"

    for current in _exception_tree(error):
        if (
            isinstance(current, httpx.HTTPStatusError)
            and current.response.status_code == 429
        ):
            retry_after = _parse_retry_after(
                current.response.headers.get("Retry-After")
            )
            source = (
                "http_retry_after"
                if retry_after is not None
                else "http_header_missing_fallback"
            )
            return True, retry_after, source
        code = getattr(current, "code", None)
        rpc_error = getattr(current, "error", None)
        if code == -32000 or getattr(rpc_error, "code", None) == -32000:
            return True, None, "json_rpc_fallback"
        limited, retry_after = _rate_limit_from_value(str(current))
        if limited:
            return True, retry_after, "proxy_text_fallback"
    return False, None, "not_rate_limited"


def _is_authentication_failure(error: BaseException) -> bool:
    message = _exception_text(error).lower()
    return "401" in message or "unauthorized" in message or "-32001" in message


def _is_broken_session(error: BaseException) -> bool:
    """Identify local transport teardown, not ordinary provider tool failures."""

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
        "signalingstate is 'closed'",
    )
    return any(marker in message or marker in name for marker in markers)
