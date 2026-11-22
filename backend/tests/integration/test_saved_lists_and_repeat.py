"""Saved lists, repeat-last-order, recent items and clear on PostgreSQL (demo stores).

A previous order is placed through the real tap path with the recording Zepto
gateway from test_shopping_checkout (a fake: no provider is called). Repeating it
must never reach the gateway again. Synthetic sample orders (`synth-` ids) and
baskets marked ordered without a provider-confirmed attempt are never repeated.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.database.models import ShoppingBasketRecord, ZeptoCheckoutAttemptRecord
from app.dependencies import get_zepto_checkout_service
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.models import Approval, ApprovalStatus, Basket, BasketLine, BasketStatus, ProductRef
from tests.integration.helpers import as_user
from tests.integration.test_shopping_checkout import RecordingZeptoGateway, _checkout_service

ZEPTO = {"provider": "zepto"}
ADDRESSES = {"zepto_address_id": "demo-home", "swiggy_address_id": "demo-home"}


def _add_oil(api: TestClient, user: dict[str, str], quantity: int = 2) -> dict:
    product = api.post("/api/zepto/search", headers=user, json={"query": "Fortune tel", "address_id": "demo-home"}).json()["products"][0]
    response = api.post(
        "/api/basket/lines",
        headers=user,
        params=ZEPTO,
        json={
            "ref": {"provider": "zepto", "product_id": product["product_id"], "variant_id": product["spin_id"], "sku_id": product["sku_id"]},
            "name": product["name"],
            "pack_size": product.get("pack_size"),
            # An old price: a repeat must use today's, not this one.
            "unit_price_paise": 9900,
            "quantity": quantity,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _run(session_factory, statement) -> object:
    async def go():
        async with session_factory() as session, session.begin():
            return await session.execute(statement)

    return asyncio.run(go())


def _attempts(session_factory) -> int:
    async def go():
        async with session_factory() as session:
            return await session.scalar(select(func.count()).select_from(ZeptoCheckoutAttemptRecord))

    return asyncio.run(go())


@pytest.fixture
def gateway(override) -> RecordingZeptoGateway:
    recording = RecordingZeptoGateway()
    service = _checkout_service(recording)
    override(get_zepto_checkout_service, lambda: service)
    return recording


def _place_order(api: TestClient, user: dict[str, str]) -> dict:
    """Bill → approve → COD tap through the recording gateway (no provider call)."""

    basket = _add_oil(api, user)
    api.post("/api/basket/address", headers=user, params=ZEPTO, json={"address_id": "demo-home"})
    approval = api.post("/api/basket/approval", headers=user, params=ZEPTO).json()["approval"]
    assert api.post(f"/api/basket/approval/{approval['id']}/approve", headers=user, json={"fingerprint": approval["fingerprint"]}).status_code == 200
    placed = api.post(
        "/api/zepto/checkout/cod",
        headers={**user, "Idempotency-Key": f"key-{basket['id']}"},
        json={"approval_id": approval["id"], "fingerprint": approval["fingerprint"]},
    )
    assert placed.json()["status"] == "confirmed", placed.text
    return basket


def test_repeat_last_order_adds_todays_price_and_never_orders(api: TestClient, gateway: RecordingZeptoGateway) -> None:
    alice = as_user("alice")
    assert api.get("/api/quick-actions", headers=alice).json()["last_order"] is None
    basket = _place_order(api, alice)
    assert len(gateway.calls) == 1

    actions = api.get("/api/quick-actions", headers=alice).json()
    assert actions["last_order"]["store"] == "Zepto"
    assert [item["name"] for item in actions["recent_items"]] == ["Fortune Sunflower Oil"]
    # Another household sees none of it.
    assert api.get("/api/quick-actions", headers=as_user("bob")).json()["last_order"] is None
    assert api.post("/api/quick-actions/repeat-last", headers=as_user("bob"), json=ADDRESSES).status_code == 404

    repeated = api.post("/api/quick-actions/repeat-last", headers=alice, json=ADDRESSES)
    assert repeated.status_code == 200, repeated.text
    body = repeated.json()
    assert body["added_count"] == 1
    fresh = body["baskets"][0]
    assert fresh["id"] != basket["id"] and fresh["status"] == "draft"
    assert fresh["lines"][0]["quantity"] == 2 and fresh["lines"][0]["unit_price"]["paise"] == 15100
    # No bill, no approval, no second order.
    assert len(gateway.calls) == 1


def test_synthetic_or_unconfirmed_orders_are_never_repeated(api: TestClient, session_factory) -> None:
    alice = as_user("alice")
    # Marked ordered but no provider-confirmed attempt: not a real order.
    unconfirmed = _add_oil(api, alice)
    _run(session_factory, update(ShoppingBasketRecord).where(ShoppingBasketRecord.id == unconfirmed["id"]).values(status="ordered"))

    # A synthetic sample order, complete with a "confirmed" attempt.
    now = datetime.now(UTC)
    line = BasketLine(ref=ProductRef(provider="zepto", product_id="p", variant_id="v"), name="Sample Ghee", unit_price_paise=50000, quantity=1)
    synthetic = Basket(id="synth-basket-1", household_id="alice", provider="zepto", lines=[line], status=BasketStatus.ORDERED, revision=1, created_at=now, updated_at=now + timedelta(minutes=1))

    async def seed() -> None:
        await DatabaseBasketRepository(session_factory).save(synthetic)
        await DatabaseApprovalRepository(session_factory).save(
            Approval(id="synth-approval-1", basket_id=synthetic.id, basket_revision=1, fingerprint="f" * 64, payable_paise=50000, status=ApprovalStatus.CONSUMED, created_at=now, expires_at=now)
        )
        async with session_factory() as session, session.begin():
            session.add(ZeptoCheckoutAttemptRecord(id="synth-attempt-1", approval_id="synth-approval-1", basket_id=synthetic.id, household_id="alice", idempotency_key_hash="a" * 64, payable_paise=50000, status="confirmed", provider_order_id="synth-order-1", created_at=now))

    asyncio.run(seed())
    actions = api.get("/api/quick-actions", headers=alice).json()
    assert actions["last_order"] is None and actions["recent_items"] == []
    assert api.post("/api/quick-actions/repeat-last", headers=alice, json=ADDRESSES).status_code == 404


def test_list_from_basket_then_add_to_basket(api: TestClient, session_factory) -> None:
    alice = as_user("alice")
    basket = _add_oil(api, alice, 1)
    saved = api.post("/api/lists", headers=alice, json={"name": "Monthly ration", "from_baskets": ["zepto"]})
    assert saved.status_code == 201, saved.text
    list_id = saved.json()["id"]
    assert api.get("/api/lists", headers=as_user("bob")).json() == {"lists": []}
    assert api.post(f"/api/lists/{list_id}/add-to-basket", headers=as_user("bob"), json=ADDRESSES).status_code == 404

    cleared = api.post("/api/baskets/zepto/clear", headers=alice, json={"expected_revision": basket["revision"]})
    assert cleared.status_code == 200 and cleared.json()["lines"] == []
    added = api.post(f"/api/lists/{list_id}/add-to-basket", headers=alice, json=ADDRESSES).json()
    assert added["added_count"] == 1
    assert _attempts(session_factory) == 0


def test_clear_with_a_stale_revision_changes_nothing(api: TestClient) -> None:
    alice = as_user("alice")
    first = _add_oil(api, alice, 1)
    second = _add_oil(api, alice, 1)  # voice added more after the user looked
    stale = api.post("/api/baskets/zepto/clear", headers=alice, json={"expected_revision": first["revision"]})
    assert stale.status_code == 409
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["revision"] == second["revision"]


def test_clear_supersedes_the_open_bill(api: TestClient) -> None:
    alice = as_user("alice")
    _add_oil(api, alice, 1)
    api.post("/api/basket/address", headers=alice, params=ZEPTO, json={"address_id": "demo-home"})
    bill = api.post("/api/basket/approval", headers=alice, params=ZEPTO).json()
    revision = api.get("/api/basket", headers=alice, params=ZEPTO).json()["revision"]
    assert api.post("/api/baskets/zepto/clear", headers=alice, json={"expected_revision": revision}).status_code == 200
    approve = api.post(
        f"/api/basket/approval/{bill['approval']['id']}/approve",
        headers=alice,
        json={"fingerprint": bill["approval"]["fingerprint"]},
    )
    assert approve.status_code == 409
