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


def _workers(session_factory, count: int):
    """Independent service stacks, like separate processes: no shared lock."""

    from app.shopping.approval import ApprovalService
    from app.shopping.basket import BasketService
    from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
    from tests.shopping_fakes import FakeProvider

    provider = FakeProvider()
    stacks = []
    for _ in range(count):
        baskets = DatabaseBasketRepository(session_factory)
        approvals = DatabaseApprovalRepository(session_factory)
        stacks.append(
            (BasketService(baskets, approvals), ApprovalService(baskets, approvals, lambda _: provider))
        )
    return stacks, DatabaseBasketRepository(session_factory), DatabaseApprovalRepository(session_factory)


@pytest.mark.parametrize("round_", range(5))
async def test_edits_racing_an_approval_never_leave_a_stale_approval(session_factory, round_: int) -> None:
    from app.shopping.errors import ApprovalRefused
    from app.shopping.models import ApprovalStatus, BasketStatus, basket_fingerprint
    from tests.shopping_fakes import HOUSEHOLD, PROVIDER, line

    stacks, basket_repo, approval_repo = _workers(session_factory, 4)
    (editor, approver), others = stacks[0], stacks[1:]
    await editor.add_line(HOUSEHOLD, PROVIDER, line("spin1"))
    basket = await editor.set_address(HOUSEHOLD, PROVIDER, "addr_home")
    requested = await approver.request(basket.id)
    extra = [f"extra{index}" for index in range(len(others))]

    async def approve() -> str:
        try:
            await approver.approve(requested.approval.id, requested.approval.fingerprint, HOUSEHOLD)
            return "approved"
        except ApprovalRefused:
            return "refused"

    edits = [
        service.add_line(HOUSEHOLD, PROVIDER, line(variant))
        for (service, _), variant in zip(others, extra)
    ]
    outcomes = await asyncio.gather(approve(), *edits)

    stored = await basket_repo.get(basket.id)
    approval = await approval_repo.get(requested.approval.id)
    assert stored is not None and approval is not None
    # No edit was lost.
    assert sorted(item.ref.variant_id for item in stored.lines) == sorted(["spin1", *extra])
    # Whatever won the race, an approval never outlives the basket it priced.
    assert approval.status != ApprovalStatus.APPROVED
    assert stored.status == BasketStatus.DRAFT
    assert basket_fingerprint(stored, approval.payable_paise) != approval.fingerprint
    assert outcomes[0] in {"approved", "refused"}


async def test_parallel_quantity_edits_from_separate_workers_all_apply(session_factory) -> None:
    from tests.shopping_fakes import HOUSEHOLD, PROVIDER, line

    stacks, basket_repo, _ = _workers(session_factory, 4)
    variants = [f"v{index}" for index in range(4)]
    await asyncio.gather(
        *(service.add_line(HOUSEHOLD, PROVIDER, line(variant)) for (service, _), variant in zip(stacks, variants))
    )
    await asyncio.gather(
        *(
            service.set_quantity(HOUSEHOLD, PROVIDER, variant, 3)
            for (service, _), variant in zip(stacks, variants)
        )
    )

    basket = await stacks[0][0].active(HOUSEHOLD, PROVIDER)
    assert {item.ref.variant_id: item.quantity for item in basket.lines} == dict.fromkeys(variants, 3)
    assert basket.revision == 8
