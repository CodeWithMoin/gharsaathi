from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.api.swiggy import _raise_http
from app.commerce.swiggy.client import (
    SwiggyMcpClient,
    _RateLimitObservation,
    _parse_retry_after,
    _rate_limit_from_exception,
    _rate_limit_from_value,
)
from app.commerce.swiggy.errors import SwiggyRateLimitError
from app.core.config import Settings


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeOAuth:
    async def access_token(self) -> str:
        return "test-token"

    async def refresh(self) -> str:
        return "refreshed-test-token"


def make_client(clock: FakeClock, *, budget: int = 30) -> SwiggyMcpClient:
    settings = Settings(
        _env_file=None,
        swiggy_retry_budget_seconds=budget,
        swiggy_rate_limit_fallback_seconds=30,
    )
    return SwiggyMcpClient(  # type: ignore[arg-type]
        settings,
        FakeOAuth(),
        clock=clock,
        sleep=clock.sleep,
    )


def test_retry_after_accepts_seconds_and_http_date() -> None:
    assert _parse_retry_after("4.2") == 5
    now = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    assert _parse_retry_after("Sat, 21 Nov 2026 00:00:12 GMT", now=now) == 12
    assert _parse_retry_after("not-a-delay") is None
    assert _parse_retry_after("inf") is None


def test_json_rpc_rate_limit_code_and_proxy_text_are_detected() -> None:
    assert _rate_limit_from_value(
        {"error": {"code": -32000, "message": "gateway rejected request"}}
    ) == (True, None)
    assert _rate_limit_from_value("Error: 429 Too Many Requests") == (True, None)


def test_exception_group_preserves_http_retry_after() -> None:
    request = httpx.Request("POST", "https://mcp.swiggy.com/im")
    response = httpx.Response(429, headers={"Retry-After": "17"}, request=request)
    status_error = httpx.HTTPStatusError(
        "too many requests", request=request, response=response
    )

    assert _rate_limit_from_exception(ExceptionGroup("transport", [status_error])) == (
        True,
        17,
    )


@pytest.mark.asyncio
async def test_http_observer_captures_header_before_mcp_wrapping() -> None:
    observation = _RateLimitObservation()
    response = httpx.Response(429, headers={"Retry-After": "19"})

    await observation.capture(response)

    assert observation.saw_429 is True
    assert observation.retry_after_seconds == 19


@pytest.mark.asyncio
async def test_short_retry_after_is_retried_within_budget() -> None:
    clock = FakeClock()
    client = make_client(clock)
    calls = 0

    async def run_session(
        access_token: str, operation: Any, *, operation_name: str
    ) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SwiggyRateLimitError(5)
        return "ok"

    client._run_session = run_session  # type: ignore[method-assign]

    async def operation(session: Any) -> str:
        return "unused"

    assert (
        await client._with_session(
            operation, operation_name="test", retry_rate_limit=True
        )
        == "ok"
    )
    assert calls == 2
    assert clock.sleeps == [5]


@pytest.mark.asyncio
async def test_fallback_cooldown_is_not_polled_past_retry_budget() -> None:
    clock = FakeClock()
    client = make_client(clock)
    calls = 0

    async def run_session(
        access_token: str, operation: Any, *, operation_name: str
    ) -> str:
        nonlocal calls
        calls += 1
        raise SwiggyRateLimitError(30)

    client._run_session = run_session  # type: ignore[method-assign]

    async def operation(session: Any) -> str:
        return "unused"

    with pytest.raises(SwiggyRateLimitError) as raised:
        await client._with_session(
            operation, operation_name="test", retry_rate_limit=True
        )

    assert raised.value.retry_after_seconds == 30
    assert calls == 1
    assert clock.sleeps == []


def test_api_preserves_retry_after_for_the_browser() -> None:
    with pytest.raises(HTTPException) as raised:
        _raise_http(SwiggyRateLimitError(23))

    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "23"}
    assert raised.value.detail == (
        "Swiggy is receiving too many requests. Try again in 23 seconds."
    )


@pytest.mark.asyncio
async def test_non_read_operation_is_never_blindly_retried() -> None:
    clock = FakeClock()
    client = make_client(clock)
    calls = 0

    async def run_session(
        access_token: str, operation: Any, *, operation_name: str
    ) -> str:
        nonlocal calls
        calls += 1
        raise SwiggyRateLimitError(5)

    client._run_session = run_session  # type: ignore[method-assign]

    async def operation(session: Any) -> str:
        return "unused"

    with pytest.raises(SwiggyRateLimitError):
        await client._with_session(
            operation, operation_name="tools/call:checkout", retry_rate_limit=False
        )

    assert calls == 1
    assert clock.sleeps == []
