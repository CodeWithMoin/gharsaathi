import asyncio
import json
import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx
from jsonschema import ValidationError, validate
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.commerce.swiggy.errors import (
    SwiggyError,
    SwiggyProviderError,
    SwiggyRateLimitError,
    SwiggySchemaError,
)
from app.commerce.swiggy.models import ToolDescription
from app.commerce.swiggy.oauth import SwiggyOAuthClient
from app.core.config import Settings


T = TypeVar("T")
logger = logging.getLogger(__name__)
RATE_LIMIT_RETRY_SAFE_TOOLS = frozenset({"get_addresses", "search_products"})


@dataclass
class _RateLimitObservation:
    saw_429: bool = False
    retry_after_seconds: int | None = None

    async def capture(self, response: httpx.Response) -> None:
        if response.status_code != 429:
            return
        self.saw_429 = True
        self.retry_after_seconds = _parse_retry_after(response.headers.get("Retry-After"))


class SwiggyMcpClient:
    def __init__(
        self,
        settings: Settings,
        oauth: SwiggyOAuthClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.oauth = oauth
        self._clock = clock
        self._sleep = sleep
        self._request_lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._tool_schema_cache_until = 0.0

    async def _with_session(
        self,
        operation: Callable[[ClientSession], Awaitable[T]],
        *,
        operation_name: str,
        retry_rate_limit: bool,
    ) -> T:
        # This integration currently exposes read-only operations. Serializing them
        # prevents one user's overlapping UI requests from multiplying MCP bursts.
        async with self._request_lock:
            deadline = self._clock() + self.settings.swiggy_retry_budget_seconds
            access_token = await self.oauth.access_token()
            refreshed = False

            while True:
                await self._wait_for_cooldown(deadline)
                try:
                    return await self._run_session(
                        access_token, operation, operation_name=operation_name
                    )
                except SwiggyRateLimitError as exc:
                    retry_after = exc.retry_after_seconds
                    self._cooldown_until = max(
                        self._cooldown_until, self._clock() + retry_after
                    )
                    if not retry_rate_limit:
                        raise
                    remaining = deadline - self._clock()
                    if retry_after >= remaining:
                        raise SwiggyRateLimitError(retry_after) from exc
                    await self._sleep(retry_after)
                except SwiggyError:
                    raise
                except Exception as exc:
                    if not refreshed and _is_authentication_error(exc):
                        access_token = await self.oauth.refresh()
                        refreshed = True
                        continue
                    if refreshed and _is_authentication_error(exc):
                        raise SwiggyProviderError(
                            "Swiggy rejected the refreshed session. Connect again."
                        ) from exc
                    raise SwiggyProviderError("Swiggy MCP request failed.") from exc

    async def _wait_for_cooldown(self, deadline: float) -> None:
        delay = self._cooldown_until - self._clock()
        if delay <= 0:
            return
        if delay >= deadline - self._clock():
            raise SwiggyRateLimitError(math.ceil(delay))
        await self._sleep(delay)

    async def _run_session(
        self,
        access_token: str,
        operation: Callable[[ClientSession], Awaitable[T]],
        *,
        operation_name: str,
    ) -> T:
        observation = _RateLimitObservation()
        correlation_id = str(uuid.uuid4())
        started_at = self._clock()

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
                event_hooks={"response": [observation.capture]},
            )

        try:
            async with streamablehttp_client(
                self.settings.swiggy_mcp_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Correlation-ID": correlation_id,
                },
                httpx_client_factory=http_client_factory,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await operation(session)
                    logger.info(
                        "event=swiggy_mcp_call operation=%s correlation_id=%s "
                        "duration_ms=%d status=ok",
                        operation_name,
                        correlation_id,
                        round((self._clock() - started_at) * 1000),
                    )
                    return result
        except BaseException as exc:
            limited, embedded_retry_after = _rate_limit_from_exception(exc)
            if observation.saw_429 or limited:
                retry_after = (
                    observation.retry_after_seconds
                    or embedded_retry_after
                    or self.settings.swiggy_rate_limit_fallback_seconds
                )
                if observation.retry_after_seconds is not None:
                    source = "http_retry_after"
                elif observation.saw_429:
                    source = "http_header_missing_fallback"
                elif embedded_retry_after is not None:
                    source = "embedded_retry_after"
                else:
                    source = "json_rpc_or_proxy_fallback"
                logger.warning(
                    "event=swiggy_mcp_call operation=%s correlation_id=%s "
                    "duration_ms=%d status=rate_limited retry_after_seconds=%d "
                    "retry_after_source=%s",
                    operation_name,
                    correlation_id,
                    round((self._clock() - started_at) * 1000),
                    retry_after,
                    source,
                )
                raise SwiggyRateLimitError(retry_after) from exc
            logger.warning(
                "event=swiggy_mcp_call operation=%s correlation_id=%s "
                "duration_ms=%d status=error",
                operation_name,
                correlation_id,
                round((self._clock() - started_at) * 1000),
            )
            raise

    async def _load_tools(
        self, session: ClientSession, *, force: bool = False
    ) -> list[ToolDescription]:
        if (
            not force
            and self._tool_schemas
            and self._clock() < self._tool_schema_cache_until
        ):
            return [
                ToolDescription(name=name, input_schema=schema)
                for name, schema in self._tool_schemas.items()
            ]

        response = await session.list_tools()
        descriptions = [
            ToolDescription(
                name=tool.name,
                description=tool.description,
                input_schema=tool.inputSchema,
            )
            for tool in response.tools
        ]
        self._tool_schemas = {
            description.name: description.input_schema for description in descriptions
        }
        self._tool_schema_cache_until = (
            self._clock() + self.settings.swiggy_tool_schema_cache_seconds
        )
        return descriptions

    async def list_tools(self) -> list[ToolDescription]:
        async def run(session: ClientSession) -> list[ToolDescription]:
            return await self._load_tools(session, force=True)

        return await self._with_session(
            run, operation_name="tools/list", retry_rate_limit=True
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def run(session: ClientSession) -> dict[str, Any]:
            await self._load_tools(session)
            schema = self._tool_schemas.get(name)
            if schema is None:
                raise SwiggySchemaError(
                    f"The authenticated Swiggy catalogue does not expose {name}."
                )
            try:
                validate(instance=arguments, schema=schema)
            except ValidationError as exc:
                raise SwiggySchemaError(
                    f"Arguments do not match Swiggy's live {name} schema: {exc.message}"
                ) from exc

            result = await session.call_tool(name, arguments)
            is_error = bool(getattr(result, "isError", False))
            structured = getattr(result, "structuredContent", None)
            content = getattr(result, "content", []) or []
            text = next((item.text for item in content if hasattr(item, "text")), None)
            if structured is None and text:
                try:
                    structured = json.loads(text)
                except json.JSONDecodeError:
                    if is_error:
                        limited, retry_after = _rate_limit_from_value(text)
                        if limited:
                            raise SwiggyRateLimitError(
                                retry_after
                                or self.settings.swiggy_rate_limit_fallback_seconds
                            )
                        raise SwiggyProviderError(text)
            if is_error:
                limited, retry_after = _rate_limit_from_value(structured)
                if limited:
                    raise SwiggyRateLimitError(
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
                    raise SwiggyRateLimitError(
                        retry_after
                        or self.settings.swiggy_rate_limit_fallback_seconds
                    )
                raise SwiggyProviderError(_safe_error_message(structured))
            return structured

        return await self._with_session(
            run,
            operation_name=f"tools/call:{name}",
            retry_rate_limit=name in RATE_LIMIT_RETRY_SAFE_TOOLS,
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


def _exception_tree(exc: BaseException) -> list[BaseException]:
    found: list[BaseException] = []
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return found


def _rate_limit_from_exception(exc: BaseException) -> tuple[bool, int | None]:
    for current in _exception_tree(exc):
        if (
            isinstance(current, httpx.HTTPStatusError)
            and current.response.status_code == 429
        ):
            return True, _parse_retry_after(current.response.headers.get("Retry-After"))
        code = getattr(current, "code", None)
        error = getattr(current, "error", None)
        if code == -32000 or getattr(error, "code", None) == -32000:
            return True, None
        limited, retry_after = _rate_limit_from_value(str(current))
        if limited:
            return True, retry_after
    return False, None


def _is_authentication_error(exc: BaseException) -> bool:
    for current in _exception_tree(exc):
        code = getattr(current, "code", None)
        error = getattr(current, "error", None)
        if code == -32001 or getattr(error, "code", None) == -32001:
            return True
        message = str(current).lower()
        if "401" in message or "unauthorized" in message:
            return True
    return False


def _safe_error_message(payload: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return "Swiggy could not complete the request."
