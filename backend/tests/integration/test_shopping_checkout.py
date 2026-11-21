"""Search → basket → exact-bill approval → Zepto COD checkout on PostgreSQL.

Pricing uses the deterministic demo quote providers. The only consequential
capability, Zepto's COD confirmation, is a recording fake: these tests prove
when it is (and is not) reached. Nothing here can place a real order.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.commerce.demo import DemoZeptoQuoteProvider
from app.dependencies import (
    get_approval_repository,
    get_basket_repository,
    get_zepto_checkout_service,
)
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.shopping.checkout import CheckoutService, DatabaseCheckoutAttemptRepository
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.errors import ApprovalRefused
from app.shopping.models import Basket, BasketLine, ProductRef, Quote
from tests.integration.helpers import as_user

ZEPTO = {"provider": "zepto"}
SWIGGY = {"provider": "swiggy_instamart"}
OIL_UNIT_PAISE = 15100  # demo Zepto offer price for Fortune oil
DEMO_HANDLING_PAISE = 500
DEMO_DELIVERY_PAISE = 2500  # charged below ₹499


class RecordingZeptoGateway:
    """Stands in for ZeptoCodCheckoutGateway; records every would-be order."""

    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = fail
        self.delay = delay

    async def confirm_cod(self, basket: Basket) -> object:
        self.calls.append(
            {
                "basket_id": basket.id,
                "revision": basket.revision,
                "address_id": basket.address_id,
                "lines": [(line.ref.variant_id, line.quantity) for line in basket.lines],
            }
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise TimeoutError("provider timed out after accepting the request")
        quote = await DemoZeptoQuoteProvider().quote(basket)
        return SimpleNamespace(
            order_id=f"ZEP-{len(self.calls)}", payable_paise=quote.payable_paise, status="placed"
        )


class SurchargingQuoteProvider(DemoZeptoQuoteProvider):
    """The provider bill moved after approval (e.g. a surge fee)."""

    async def quote(self, basket: Basket) -> Quote:
        quote = await super().quote(basket)
        return quote.model_copy(update={"payable_paise": quote.payable_paise + 1000})


def _checkout_service(gateway: RecordingZeptoGateway, quotes=None) -> CheckoutService:
    from app.database.session import get_session_factory

    return CheckoutService(
        get_basket_repository(),
        get_approval_repository(),
        DatabaseCheckoutAttemptRepository(get_session_factory()),
        quotes or DemoZeptoQuoteProvider(),
        gateway,
    )


@pytest.fixture
def gateway(override) -> RecordingZeptoGateway:
    recording = RecordingZeptoGateway()
    service = _checkout_service(recording)
    override(get_zepto_checkout_service, lambda: service)
    return recording


def _search(api: TestClient, user: dict[str, str], store: str, query: str) -> dict:
    response = api.post(f"/api/{store}/search", headers=user, json={"query": query, "address_id": "demo-home"})
    assert response.status_code == 200, response.text
    products = response.json()["products"]
    assert products, f"demo {store} search returned nothing for {query!r}"
    return products[0]


def _add(api: TestClient, user: dict[str, str], provider: dict, product: dict, quantity: int = 1) -> dict:
    response = api.post(
        "/api/basket/lines",
        headers=user,
        params=provider,
        json={
            "ref": {
                "provider": provider["provider"],
                "product_id": product["product_id"],
                "variant_id": product["spin_id"],
                "sku_id": product["sku_id"],
            },
            "name": product["name"],
            "brand": product.get("brand"),
            "pack_size": product.get("pack_size"),
            "unit_price_paise": round(product["price"]["offer_price"] * 100),
            "mrp_paise": round(product["price"]["mrp"] * 100),
            "quantity": quantity,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _ready_basket(api: TestClient, user: dict[str, str], *, oil: int = 2) -> dict:
    product = _search(api, user, "zepto", "Fortune tel")
    assert product["name"] == "Fortune Sunflower Oil"
    _add(api, user, ZEPTO, product, oil)
    response = api.post("/api/basket/address", headers=user, params=ZEPTO, json={"address_id": "demo-home"})
    assert response.status_code == 200
    return product


def _request_bill(api: TestClient, user: dict[str, str], provider: dict = ZEPTO) -> dict:
    response = api.post("/api/basket/approval", headers=user, params=provider)
    assert response.status_code == 200, response.text
    return response.json()


def _approve(api: TestClient, user: dict[str, str], approval: dict):
    return api.post(
        f"/api/basket/approval/{approval['id']}/approve",
        headers=user,
        json={"fingerprint": approval["fingerprint"]},
    )


def _checkout(api: TestClient, user: dict[str, str], approval: dict, key: str | None = "key-1"):
    headers = {**user, **({"Idempotency-Key": key} if key is not None else {})}
    return api.post(
        "/api/zepto/checkout/cod",
        headers=headers,
        json={"approval_id": approval["id"], "fingerprint": approval["fingerprint"]},
    )


def test_approval_is_bound_to_the_exact_basket_revision_and_bill(api: TestClient) -> None:
    alice = as_user("alice")
    product = _ready_basket(api, alice, oil=2)

    bill = _request_bill(api, alice)
    items = 2 * OIL_UNIT_PAISE
    assert bill["quote"]["item_total"]["paise"] == items
    assert [charge["amount"]["paise"] for charge in bill["quote"]["charges"]] == [
        DEMO_HANDLING_PAISE,
        DEMO_DELIVERY_PAISE,
    ]
    assert bill["quote"]["payable"]["paise"] == items + DEMO_HANDLING_PAISE + DEMO_DELIVERY_PAISE
    assert bill["approval"]["payable"] == bill["quote"]["payable"]
    assert bill["item_total_matches"] is True
    assert bill["basket"]["status"] == "awaiting_approval"
    first = bill["approval"]

    # Wrong fingerprint and another household are both refused.
    forged = api.post(
        f"/api/basket/approval/{first['id']}/approve", headers=alice, json={"fingerprint": "0" * 64}
    )
    assert forged.status_code == 409
    assert _approve(api, as_user("bob"), first).status_code == 404

    # A quantity change after the quote invalidates the bill that was shown.
    changed = api.patch(
        f"/api/basket/lines/{product['spin_id']}", headers=alice, params=ZEPTO, json={"quantity": 3}
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "draft"
    assert changed.json()["revision"] > bill["basket"]["revision"]
    stale = _approve(api, alice, first)
    assert stale.status_code == 409
    assert "superseded" in stale.json()["detail"]

    second = _request_bill(api, alice)
    assert second["approval"]["id"] != first["id"]
    assert second["approval"]["fingerprint"] != first["fingerprint"]
    assert second["quote"]["item_total"]["paise"] == 3 * OIL_UNIT_PAISE
    approved = _approve(api, alice, second["approval"])
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["status"] == "approved"

    # Changing the address after approval also reopens the basket.
    api.post("/api/basket/address", headers=alice, params=ZEPTO, json={"address_id": "office"})
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["status"] == "draft"
    assert _approve(api, alice, second["approval"]).status_code == 409

    third = _request_bill(api, alice)
    rejected = api.post(f"/api/basket/approval/{third['approval']['id']}/reject", headers=alice)
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["status"] == "draft"
    assert api.post(f"/api/basket/approval/{third['approval']['id']}/reject", headers=alice).status_code == 409
    assert _approve(api, alice, third["approval"]).status_code == 409


def test_baskets_are_separate_per_store_and_per_household(api: TestClient) -> None:
    alice, bob = as_user("alice"), as_user("bob")
    _ready_basket(api, alice)
    zepto_bill = _request_bill(api, alice)

    swiggy_milk = _search(api, alice, "swiggy", "doodh")
    swiggy = _add(api, alice, SWIGGY, swiggy_milk)
    assert [line["name"] for line in swiggy["lines"]] == ["Amul Taaza Homogenised Milk"]

    # Editing the Swiggy basket leaves the Zepto bill approvable.
    assert _approve(api, alice, zepto_bill["approval"]).status_code == 200
    assert api.get("/api/basket", headers=bob, params=ZEPTO).json()["lines"] == []

    mixed = api.post(
        "/api/basket/lines",
        headers=alice,
        params=ZEPTO,
        json={
            "ref": {"provider": "swiggy_instamart", "product_id": "x", "variant_id": "y"},
            "name": "Wrong store",
            "unit_price_paise": 100,
        },
    )
    assert mixed.status_code == 422


def test_empty_or_addressless_basket_cannot_be_quoted(api: TestClient) -> None:
    alice = as_user("alice")
    assert api.post("/api/basket/approval", headers=alice, params=ZEPTO).status_code == 409
    _add(api, alice, ZEPTO, _search(api, alice, "zepto", "atta"))
    no_address = api.post("/api/basket/approval", headers=alice, params=ZEPTO)
    assert no_address.status_code == 409
    assert "address" in no_address.json()["detail"]


def test_cod_checkout_needs_an_approved_exact_bill_and_is_idempotent(
    api: TestClient, gateway: RecordingZeptoGateway
) -> None:
    alice = as_user("alice")
    _ready_basket(api, alice, oil=2)
    bill = _request_bill(api, alice)
    approval = bill["approval"]

    assert _checkout(api, alice, approval, key=None).status_code == 400
    assert _checkout(api, alice, approval, key="   ").status_code == 400
    pending = _checkout(api, alice, approval)
    assert pending.status_code == 409  # quoted but not yet approved
    assert gateway.calls == []

    assert _approve(api, alice, approval).status_code == 200
    unknown = _checkout(api, alice, {**approval, "id": uuid4().hex})
    assert unknown.status_code == 404
    forged = _checkout(api, alice, {**approval, "fingerprint": "f" * 64})
    assert forged.status_code == 409
    assert _checkout(api, as_user("bob"), approval).status_code == 404
    assert gateway.calls == []

    placed = _checkout(api, alice, approval, key="key-1")
    assert placed.status_code == 200, placed.text
    attempt = placed.json()
    assert attempt["status"] == "confirmed"
    assert attempt["provider_order_id"] == "ZEP-1"
    assert attempt["payable"]["paise"] == approval["payable"]["paise"]
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["lines"] == [(bill["basket"]["lines"][0]["ref"]["variant_id"], 2)]
    assert gateway.calls[0]["address_id"] == "demo-home"

    retried = _checkout(api, alice, approval, key="key-1")
    assert retried.status_code == 200
    assert retried.json()["id"] == attempt["id"]
    other_key = _checkout(api, alice, approval, key="key-2")
    assert other_key.status_code == 409
    assert len(gateway.calls) == 1

    # The ordered basket is closed; the next request starts a fresh, empty basket.
    fresh = api.get("/api/basket", headers=alice, params=ZEPTO).json()
    assert fresh["id"] != bill["basket"]["id"]
    assert fresh["lines"] == []
    assert _approve(api, alice, approval).status_code == 409


def test_basket_change_after_approval_blocks_checkout(
    api: TestClient, gateway: RecordingZeptoGateway
) -> None:
    alice = as_user("alice")
    product = _ready_basket(api, alice, oil=1)
    approval = _request_bill(api, alice)["approval"]
    assert _approve(api, alice, approval).status_code == 200

    api.patch(f"/api/basket/lines/{product['spin_id']}", headers=alice, params=ZEPTO, json={"quantity": 2})

    refused = _checkout(api, alice, approval)
    assert refused.status_code == 409
    assert gateway.calls == []


def test_price_change_after_approval_refuses_checkout(api: TestClient, override) -> None:
    recording = RecordingZeptoGateway()
    service = _checkout_service(recording, quotes=SurchargingQuoteProvider())
    override(get_zepto_checkout_service, lambda: service)
    alice = as_user("alice")
    _ready_basket(api, alice)
    approval = _request_bill(api, alice)["approval"]
    assert _approve(api, alice, approval).status_code == 200

    refused = _checkout(api, alice, approval)
    assert refused.status_code == 409
    assert "payable amount changed" in refused.json()["detail"]
    assert recording.calls == []


def test_uncertain_provider_outcome_is_durable_and_never_retried(api: TestClient, override) -> None:
    recording = RecordingZeptoGateway(fail=True)
    service = _checkout_service(recording)
    override(get_zepto_checkout_service, lambda: service)
    alice = as_user("alice")
    product = _ready_basket(api, alice)
    approval = _request_bill(api, alice)["approval"]
    assert _approve(api, alice, approval).status_code == 200

    first = _checkout(api, alice, approval)
    assert first.status_code == 202
    assert first.json()["status"] == "outcome_unknown"
    assert first.json()["provider_order_id"] is None

    again = _checkout(api, alice, approval)
    assert again.status_code == 202
    assert again.json()["id"] == first.json()["id"]
    assert len(recording.calls) == 1

    # The frozen basket cannot be edited back into a "new" order.
    frozen = api.patch(
        f"/api/basket/lines/{product['spin_id']}", headers=alice, params=ZEPTO, json={"quantity": 5}
    )
    assert frozen.status_code == 422
    assert "Checkout already started" in frozen.json()["detail"]


def test_demo_mode_checkout_is_disabled_without_touching_state(api: TestClient) -> None:
    alice = as_user("alice")
    _ready_basket(api, alice)
    approval = _request_bill(api, alice)["approval"]
    assert _approve(api, alice, approval).status_code == 200

    disabled = _checkout(api, alice, approval)
    assert disabled.status_code == 502
    assert "demo" in disabled.json()["detail"]
    assert api.get("/api/basket", headers=alice, params=ZEPTO).json()["status"] == "approved"


async def _approved_zepto_basket(sessions, household: str) -> tuple[DatabaseBasketRepository, DatabaseApprovalRepository, str]:
    baskets = DatabaseBasketRepository(sessions)
    approvals = DatabaseApprovalRepository(sessions)
    basket_service = BasketService(baskets, approvals)
    approval_service = ApprovalService(baskets, approvals, lambda _: DemoZeptoQuoteProvider())
    await basket_service.add_line(
        household,
        "zepto",
        BasketLine(
            ref=ProductRef(provider="zepto", product_id="demo-oil", variant_id="zepto-demo-spin-oil"),
            name="Fortune Sunflower Oil",
            unit_price_paise=OIL_UNIT_PAISE,
            quantity=1,
        ),
    )
    basket = await basket_service.set_address(household, "zepto", "demo-home")
    requested = await approval_service.request(basket.id)
    await approval_service.approve(requested.approval.id, requested.approval.fingerprint, household)
    return baskets, approvals, requested.approval.id


@pytest.mark.parametrize("same_key", [True, False], ids=["same-key", "different-keys"])
async def test_concurrent_checkouts_from_separate_workers_dispatch_once(session_factory, same_key: bool) -> None:
    """Two processes (no shared asyncio lock) race the same approval on PostgreSQL."""

    baskets, approvals, approval_id = await _approved_zepto_basket(session_factory, "alice")
    gateway = RecordingZeptoGateway(delay=0.05)
    workers = [
        CheckoutService(
            baskets, approvals, DatabaseCheckoutAttemptRepository(session_factory), DemoZeptoQuoteProvider(), gateway
        )
        for _ in range(2)
    ]
    keys = ["race", "race"] if same_key else ["race-a", "race-b"]

    results = await asyncio.gather(
        *(worker.checkout_cod(approval_id, "alice", key) for worker, key in zip(workers, keys)),
        return_exceptions=True,
    )

    assert len(gateway.calls) == 1
    attempts = [result for result in results if not isinstance(result, BaseException)]
    errors = [result for result in results if isinstance(result, BaseException)]
    assert all(isinstance(error, ApprovalRefused) for error in errors), errors
    assert attempts, "at least one worker returns the durable attempt"
    assert len({attempt.id for attempt in attempts}) == 1
    if same_key:
        assert len(attempts) == 2
