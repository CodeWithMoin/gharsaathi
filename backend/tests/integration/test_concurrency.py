"""Concurrent requests against real PostgreSQL (SQLite cannot show these races)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.integration.helpers import as_user


@pytest_asyncio.fixture
async def async_api(integration_settings) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: record_consent reads then inserts without handling the "
        "(user_id, terms_version) unique constraint, so a double-submitted "
        "consent form raises IntegrityError (500) instead of returning the "
        "existing record."
    ),
)
async def test_double_submitted_consent_returns_one_record(async_api: AsyncClient) -> None:
    alice = as_user("alice")
    body = {"terms_accepted": True, "personalization_allowed": False}

    responses = await asyncio.gather(
        *(async_api.post("/api/legal-consent", headers=alice, json=body) for _ in range(2)),
        return_exceptions=True,
    )

    assert all(not isinstance(item, BaseException) for item in responses), responses
    assert [item.status_code for item in responses] == [201, 201]
    assert len({item.json()["accepted_at"] for item in responses}) == 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: BasketService.active() creates a basket when none is open, with no "
        "uniqueness guard, so two first add-to-basket requests create two open "
        "baskets and the later GET shows only one of the items."
    ),
)
async def test_two_first_adds_land_in_one_basket(async_api: AsyncClient) -> None:
    alice = as_user("alice")

    def line(variant: str) -> dict:
        return {
            "ref": {"provider": "zepto", "product_id": variant, "variant_id": variant},
            "name": variant,
            "unit_price_paise": 1000,
        }

    responses = await asyncio.gather(
        *(
            async_api.post("/api/basket/lines", headers=alice, params={"provider": "zepto"}, json=line(item))
            for item in ("atta", "dal")
        )
    )
    assert [item.status_code for item in responses] == [200, 200]

    basket = (await async_api.get("/api/basket", headers=alice, params={"provider": "zepto"})).json()
    assert sorted(item["name"] for item in basket["lines"]) == ["atta", "dal"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: MemoryRepository.get_or_create_person selects then inserts, so a new "
        "user's parallel first-load requests (conversation, addresses, memory) race "
        "on uq_memory_subject_identity and one of them fails with IntegrityError (500)."
    ),
)
async def test_a_new_users_parallel_first_requests_all_succeed(async_api: AsyncClient) -> None:
    alice = as_user("first-visit")
    paths = ["/api/conversations/current", "/api/zepto/addresses", "/api/memory/permissions"]

    responses = await asyncio.gather(
        *(async_api.get(path, headers=alice) for path in paths), return_exceptions=True
    )

    assert all(not isinstance(item, BaseException) for item in responses), responses
    assert [item.status_code for item in responses] == [200, 200, 200]


async def test_consent_is_explicit_and_bound_to_the_current_terms_version(async_api: AsyncClient) -> None:
    alice = as_user("alice")
    initial = (await async_api.get("/api/legal-consent", headers=alice)).json()
    assert initial["accepted"] is False

    refused = await async_api.post(
        "/api/legal-consent", headers=alice, json={"terms_accepted": False, "personalization_allowed": True}
    )
    assert refused.status_code == 422
    unknown_field = await async_api.post(
        "/api/legal-consent", headers=alice, json={"terms_accepted": True, "marketing": True}
    )
    assert unknown_field.status_code == 422
    assert (await async_api.get("/api/legal-consent", headers=alice)).json()["accepted"] is False

    accepted = await async_api.post(
        "/api/legal-consent", headers=alice, json={"terms_accepted": True, "personalization_allowed": True}
    )
    assert accepted.status_code == 201
    status = (await async_api.get("/api/legal-consent", headers=alice)).json()
    assert status["accepted"] is True and status["personalization_allowed"] is True
    assert status["terms_version"] == initial["terms_version"]
    assert (await async_api.get("/api/legal-consent", headers=as_user("bob"))).json()["accepted"] is False
