"""Resilience contracts for the optional Jev search reranker.

All HTTP here is an ``httpx.MockTransport``.  These tests intentionally use a
manual monotonic clock so circuit-breaker behaviour has no wall-clock races.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app import dependencies
from app.commerce.jev import JevProductReranker
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.commerce.swiggy.service import SwiggyService
from app.core.config import Settings


class ManualClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def product(identifier: str, *, available: bool = True, brand: str = "Amul") -> Product:
    return Product(
        product_id=identifier,
        sku_id=f"sku-{identifier}",
        spin_id=f"spin-{identifier}",
        name=f"{brand} {identifier}",
        brand=brand,
        pack_size="1 L",
        price=Price(mrp=72, offer_price=69),
        available=available,
    )


def candidates(*items: Product) -> ProductSearchResult:
    return ProductSearchResult(products=list(items))


def success_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "jev-test-model",
            "answers": {
                "product_0": {"type": "noul", "noul": 0.1},
                "product_1": {"type": "noul", "noul": 0.9},
            },
        },
    )


def build_reranker(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    clock: ManualClock | None = None,
) -> tuple[JevProductReranker, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        JevProductReranker(
            api_key=SecretStr("test-key"),
            base_url="https://jev.invalid/v1",
            model="jev-test-model",
            timeout_seconds=0.1,
            http_client=client,
            cache_ttl_seconds=60,
            circuit_failures=3,
            circuit_cooldown_seconds=30,
            clock=clock or ManualClock(),
        ),
        client,
    )


@pytest.mark.parametrize(
    ("enabled", "api_key"),
    [(False, "test-key"), (True, "")],
)
def test_disabled_or_unconfigured_reranker_is_not_constructed(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, api_key: str
) -> None:
    """The dependency boundary prevents an optional client from ever being made."""

    settings = Settings(jev_reranking_enabled=enabled, jev_api_key=api_key)
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    def should_not_construct(**_: object) -> JevProductReranker:
        pytest.fail("disabled/unconfigured Jev must not create an HTTP-capable reranker")

    monkeypatch.setattr(dependencies, "JevProductReranker", should_not_construct)
    dependencies.get_product_reranker.cache_clear()
    try:
        assert dependencies.get_product_reranker() is None
    finally:
        dependencies.get_product_reranker.cache_clear()


@pytest.mark.asyncio
async def test_identical_query_and_candidate_fingerprint_uses_one_jev_request() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return success_response()

    reranker, client = build_reranker(respond)
    original = candidates(product("milk"), product("butter"))
    try:
        first = await reranker.rerank("  Amul   dairy ", original)
        second = await reranker.rerank("amul dairy", original.model_copy(deep=True))
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert [item.product_id for item in first.products] == ["butter", "milk"]
    assert [item.product_id for item in second.products] == ["butter", "milk"]
    assert second is not first


@pytest.mark.asyncio
async def test_three_failures_open_circuit_then_half_open_probe_recovers() -> None:
    clock = ManualClock()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) <= 3:
            return httpx.Response(503, json={"detail": "temporary failure"})
        return success_response()

    reranker, client = build_reranker(respond, clock=clock)
    original = candidates(product("milk"), product("butter"))
    try:
        for _ in range(3):
            outcome = await reranker.rerank_with_diagnostics("amul dairy", original)
            assert outcome.applied is False

        # The fourth attempt is suppressed immediately: no request and no timeout.
        assert (await reranker.rerank_with_diagnostics("amul dairy", original)).applied is False
        assert len(requests) == 3

        clock.advance(30)
        probe = await reranker.rerank_with_diagnostics("amul dairy", original)
        assert probe.applied is True
        assert len(requests) == 4

        # A successful half-open probe closes the circuit for new cache keys.
        after_recovery = await reranker.rerank_with_diagnostics("amul milk", original)
        assert after_recovery.applied is True
    finally:
        await client.aclose()

    assert len(requests) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "malformed"])
async def test_timeout_or_malformed_response_fails_open(failure: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Jev timed out", request=request)
        return httpx.Response(200, json={"model": "jev-test-model", "answers": []})

    reranker, client = build_reranker(respond)
    original = candidates(product("milk"), product("butter"))
    try:
        outcome = await reranker.rerank_with_diagnostics("amul dairy", original)
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert outcome.applied is False
    assert [item.product_id for item in outcome.result.products] == ["milk", "butter"]


@pytest.mark.asyncio
async def test_reranking_preserves_filtered_membership_and_availability_authority() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return success_response()

    reranker, client = build_reranker(respond)
    # This is the already-filtered deterministic shortlist, not raw provider data.
    original = candidates(
        product("axe-available", brand="Axe"),
        product("axe-unavailable", available=False, brand="Axe"),
    )
    try:
        result = await reranker.rerank("axe", original)
    finally:
        await client.aclose()

    assert {item.product_id for item in result.products} == {
        "axe-available",
        "axe-unavailable",
    }
    assert {item.brand for item in result.products} == {"Axe"}
    assert [item.available for item in result.products] == [True, False]


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def search_products(
        self, query: str, address_id: str, offset: int
    ) -> ProductSearchResult:
        self.calls.append((query, address_id, offset))
        return candidates(product("milk"), product("butter"))


@pytest.mark.asyncio
async def test_service_cache_and_jev_cache_do_not_repeat_commerce_or_jev_requests() -> None:
    provider = RecordingAdapter()
    jev_requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        jev_requests.append(request)
        return success_response()

    reranker, client = build_reranker(respond)
    service = SwiggyService(object(), object(), provider, reranker)  # type: ignore[arg-type]
    try:
        first = await service.search("  Amul   dairy ", "home", 0)
        second = await service.search("amul dairy", "home", 0)
    finally:
        await client.aclose()

    assert provider.calls == [("Amul dairy", "home", 0)]
    assert len(jev_requests) == 1
    assert [item.product_id for item in first.products] == ["butter", "milk"]
    assert [item.product_id for item in second.products] == ["butter", "milk"]
