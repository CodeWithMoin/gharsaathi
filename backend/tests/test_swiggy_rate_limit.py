from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api.swiggy import _raise_http
from app.commerce.swiggy.client import (
    RATE_LIMIT_MESSAGE,
    SwiggyMcpClient,
    _RateLimitObservation,
    _parse_retry_after,
    _rate_limit_details,
    _rate_limit_from_value,
)
from app.commerce.swiggy.errors import SwiggyRateLimited
from app.core.config import Settings


class FakeOAuth:
    async def access_token(self) -> str:
        return "test-token"


@pytest.mark.asyncio
async def test_nested_429_is_reported_clearly_and_starts_local_cooldown() -> None:
    client = SwiggyMcpClient(Settings(_env_file=None), FakeOAuth())  # type: ignore[arg-type]
    attempts = 0

    async def fail_with_nested_429(*_args: object) -> object:
        nonlocal attempts
        attempts += 1
        try:
            raise RuntimeError("429 Too Many Requests")
        except RuntimeError as nested:
            raise ExceptionGroup("transport failed", [nested])

    client._run_session = fail_with_nested_429  # type: ignore[method-assign]

    with pytest.raises(SwiggyRateLimited, match="rate-limiting") as first:
        await client._with_session(lambda _: None)  # type: ignore[arg-type,return-value]
    assert str(first.value).startswith(RATE_LIMIT_MESSAGE)
    assert first.value.retry_after_seconds == 30

    with pytest.raises(SwiggyRateLimited, match="rate-limiting"):
        await client._with_session(lambda _: None)  # type: ignore[arg-type,return-value]
    assert attempts == 1


def test_rate_limit_maps_to_http_429() -> None:
    with pytest.raises(HTTPException) as raised:
        _raise_http(SwiggyRateLimited(RATE_LIMIT_MESSAGE, 41))
    assert raised.value.status_code == 429
    assert raised.value.detail == RATE_LIMIT_MESSAGE
    assert raised.value.headers == {"Retry-After": "41"}


def test_retry_after_accepts_seconds_and_http_date() -> None:
    assert _parse_retry_after("4.2") == 5
    now = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    assert _parse_retry_after("Sat, 21 Nov 2026 00:00:12 GMT", now=now) == 12
    assert _parse_retry_after("not-a-delay") is None
    assert _parse_retry_after("inf") is None


@pytest.mark.asyncio
async def test_raw_http_observer_preserves_retry_after_before_mcp_wraps_it() -> None:
    observation = _RateLimitObservation()
    await observation.capture(httpx.Response(429, headers={"Retry-After": "19"}))

    assert observation.saw_429 is True
    assert observation.retry_after_seconds == 19
    assert _rate_limit_details(RuntimeError("stream closed"), observation) == (
        True,
        19,
        "http_retry_after",
    )


def test_json_rpc_and_proxy_wrapped_limits_are_detected() -> None:
    assert _rate_limit_from_value(
        {
            "error": {
                "code": -32000,
                "message": "gateway rejected request",
                "data": {"retryAfter": 47},
            }
        }
    ) == (True, 47)
    assert _rate_limit_from_value("Error: 429 Too Many Requests") == (True, None)


@pytest.mark.asyncio
async def test_tool_level_json_rpc_limit_starts_exact_cooldown() -> None:
    client = SwiggyMcpClient(Settings(_env_file=None), FakeOAuth())  # type: ignore[arg-type]

    class Session:
        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="search_products",
                        description="Search",
                        inputSchema={"type": "object"},
                    )
                ]
            )

        async def call_tool(self, name: str, arguments: dict[str, object]) -> SimpleNamespace:
            del name, arguments
            return SimpleNamespace(
                isError=True,
                structuredContent={
                    "error": {
                        "code": -32000,
                        "message": "Too Many Requests",
                        "data": {"retryAfter": 47},
                    }
                },
                content=[],
            )

    session = Session()

    async def run_session(access_token: str, operation):  # type: ignore[no-untyped-def]
        del access_token
        return await operation(session)

    client._run_session = run_session  # type: ignore[method-assign]

    with pytest.raises(SwiggyRateLimited) as raised:
        await client.call_tool("search_products", {})

    assert raised.value.retry_after_seconds == 47
