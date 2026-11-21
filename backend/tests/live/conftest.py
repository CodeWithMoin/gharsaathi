"""Gating and shared live fixtures for the opt-in provider smoke suite.

Live tests are skipped unless BOTH ``--run-live`` and ``GHARSAATHI_LIVE_SMOKE=1``
are present; cart-mutating tests also need ``GHARSAATHI_LIVE_SMOKE_CART=1``.
The offline harness tests in this directory are unmarked and always run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from tests.live.harness import (
    RUN_LIVE_OPTION,
    RateLimitTripwire,
    SmokeRecorder,
    live_skip_reason,
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_live = bool(config.getoption(RUN_LIVE_OPTION, default=False))
    for item in items:
        if item.get_closest_marker("live") is None:
            continue
        reason = live_skip_reason(
            run_live_flag=run_live,
            env=os.environ,
            cart=item.get_closest_marker("live_cart") is not None,
        )
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session")
def live_tripwire() -> RateLimitTripwire:
    return RateLimitTripwire()


@pytest.fixture(scope="session")
def live_recorder(live_tripwire: RateLimitTripwire) -> Any:
    recorder = SmokeRecorder()
    yield recorder
    path = recorder.write(halt=live_tripwire.halt_message() or None)
    print(f"\nLive smoke schema report (content-free): {path}")


@pytest.fixture(autouse=True)
def _halt_live_run_on_rate_limit(
    request: pytest.FixtureRequest,
) -> Any:
    """Stop the whole session after the first 429; never retry in a loop."""

    if request.node.get_closest_marker("live") is None:
        yield
        return
    tripwire: RateLimitTripwire = request.getfixturevalue("live_tripwire")
    tripwire.stop_session_if_tripped()
    yield
    tripwire.stop_session_if_tripped()


async def _prepare(provider: Any, tripwire: RateLimitTripwire) -> Any:
    status = await provider.service.connection_status()
    if not status.connected:
        pytest.skip(f"{provider.name} is not connected; link it in the app first.")
    remaining = await provider.oauth.rate_limit_remaining()
    if remaining > 0:
        tripwire.record_cooldown(provider.name, remaining)
        tripwire.stop_session_if_tripped()
    return provider


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def live_swiggy(
    live_tripwire: RateLimitTripwire, live_recorder: SmokeRecorder
) -> AsyncIterator[Any]:
    from tests.live.live_services import build_live_swiggy

    provider = build_live_swiggy(live_tripwire, live_recorder)
    try:
        yield await _prepare(provider, live_tripwire)
    finally:
        await provider.raw_client.aclose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def live_zepto(
    live_tripwire: RateLimitTripwire, live_recorder: SmokeRecorder
) -> AsyncIterator[Any]:
    from tests.live.live_services import build_live_zepto

    provider = build_live_zepto(live_tripwire, live_recorder)
    try:
        yield await _prepare(provider, live_tripwire)
    finally:
        await provider.raw_client.aclose()
