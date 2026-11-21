import json

import httpx
import pytest
from pydantic import SecretStr

from app.commerce.jev import JevProductReranker
from app.commerce.swiggy.models import Price, Product, ProductSearchResult


def product(identifier: str, name: str, *, available: bool = True) -> Product:
    return Product(
        product_id=identifier,
        sku_id=f"sku-{identifier}",
        spin_id=f"spin-{identifier}",
        name=name,
        brand=name.split()[0],
        pack_size="1 L",
        price=Price(mrp=72, offer_price=69),
        available=available,
    )


def reranker(handler: httpx.AsyncBaseTransport) -> JevProductReranker:
    return JevProductReranker(
        api_key=SecretStr("test-key"),
        base_url="https://api.typesafe.ai/v1",
        model="jev-1.14.0",
        timeout_seconds=2.0,
        http_client=httpx.AsyncClient(transport=handler),
    )


@pytest.mark.asyncio
async def test_valid_scores_reorder_only_original_products() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload["state"] == {
            "shopping_query": "Amul milk 1 litre",
            "candidates": [
                {
                    "candidate_id": "product_0",
                    "name": "Nandini Milk",
                    "brand": "Nandini",
                    "pack_size": "1 L",
                },
                {
                    "candidate_id": "product_1",
                    "name": "Amul Taaza Milk",
                    "brand": "Amul",
                    "pack_size": "1 L",
                },
            ],
        }
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {
                    "product_0": {"type": "noul", "noul": 0.2},
                    "product_1": {"type": "noul", "noul": 0.98},
                },
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    original = ProductSearchResult(
        products=[product("nandini", "Nandini Milk"), product("amul", "Amul Taaza Milk")]
    )
    result = await reranker(httpx.MockTransport(respond)).rerank(
        "Amul milk 1 litre", original
    )

    assert [item.product_id for item in result.products] == ["amul", "nandini"]
    assert result.products[0].price == original.products[1].price


@pytest.mark.asyncio
async def test_diagnostics_report_resolved_model() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0-20261014",
                "answers": {
                    "product_0": {"type": "noul", "noul": 0.4},
                    "product_1": {"type": "noul", "noul": 0.8},
                },
            },
        )

    original = ProductSearchResult(
        products=[product("one", "Amul Butter"), product("two", "Amul Milk")]
    )
    outcome = await reranker(httpx.MockTransport(respond)).rerank_with_diagnostics(
        "Amul milk", original
    )

    assert outcome.applied is True
    assert outcome.resolved_model == "jev-1.14.0-20261014"


@pytest.mark.asyncio
async def test_unavailable_product_cannot_be_promoted() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {
                    "product_0": {"type": "noul", "noul": 0.1},
                    "product_1": {"type": "noul", "noul": 0.99},
                },
            },
        )

    original = ProductSearchResult(
        products=[
            product("available", "Amul Milk"),
            product("unavailable", "Amul Gold Milk", available=False),
        ]
    )
    result = await reranker(httpx.MockTransport(respond)).rerank("Amul milk", original)

    assert [item.product_id for item in result.products] == ["available", "unavailable"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"detail": "unavailable"}),
        httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {"product_0": {"type": "noul", "noul": 0.8}},
            },
        ),
    ],
)
async def test_failure_or_incomplete_answer_fails_open(response: httpx.Response) -> None:
    transport = httpx.MockTransport(lambda _: response)
    original = ProductSearchResult(
        products=[product("one", "Amul Milk"), product("two", "Amul Butter")]
    )

    result = await reranker(transport).rerank("Amul milk", original)

    assert [item.product_id for item in result.products] == ["one", "two"]


@pytest.mark.asyncio
async def test_deterministically_removed_brand_cannot_return() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert [
            candidate["brand"] for candidate in payload["state"]["candidates"]
        ] == ["Axe", "Axe"]
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {
                    "product_0": {"type": "noul", "noul": 0.7},
                    "product_1": {"type": "noul", "noul": 0.9},
                },
            },
        )

    already_filtered = ProductSearchResult(
        products=[product("axe-one", "Axe Pulse"), product("axe-two", "Axe Dark")]
    )
    result = await reranker(httpx.MockTransport(respond)).rerank(
        "Axe", already_filtered
    )

    assert {item.brand for item in result.products} == {"Axe"}
