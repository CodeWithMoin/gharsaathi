"""Regression tests for the approval and order boundary.

These tests deliberately exercise only local state and mocked provider behavior.
They must never call a live payment, checkout, or order tool.
"""

import pytest
from fastapi.testclient import TestClient

from app.local_callback_bridge import app as callback_bridge
from app.main import app
from app.shopping.errors import ApprovalRefused
from app.shopping.models import ApprovalStatus, BasketStatus
from tests.shopping_fakes import HOUSEHOLD, PROVIDER, World


async def test_address_change_supersedes_the_exact_bill_before_approval() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)

    await world.baskets.set_address(HOUSEHOLD, PROVIDER, "addr_work")

    with pytest.raises(ApprovalRefused, match="superseded"):
        await world.approvals.approve(
            requested.approval.id, requested.approval.fingerprint
        )
    stored = await world.approval_repo.get(requested.approval.id)
    assert stored is not None and stored.status == ApprovalStatus.SUPERSEDED


async def test_an_approved_bill_is_superseded_if_the_basket_changes() -> None:
    world = World()
    basket = await world.ready_basket()
    requested = await world.approvals.request(basket.id)
    await world.approvals.approve(requested.approval.id, requested.approval.fingerprint)

    changed = await world.baskets.set_address(HOUSEHOLD, PROVIDER, "addr_work")

    stored = await world.approval_repo.get(requested.approval.id)
    assert changed.status == BasketStatus.DRAFT
    assert stored is not None and stored.status == ApprovalStatus.SUPERSEDED


def test_public_api_exposes_only_the_guarded_zepto_cod_checkout_mutation() -> None:
    schema = app.openapi()
    exposed = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method in operations
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    ]
    checkout_operations = [
        operation for operation in exposed if "checkout" in operation.lower()
    ]
    assert checkout_operations == ["POST /api/zepto/checkout/cod"]
    order_operations = [
        operation for operation in exposed if "order" in operation.lower()
    ]
    assert order_operations
    # The only order mutation is the user's tap on the bound order card. Cancel,
    # choosing another payment method (a fresh card, no order) and the single
    # payment-status read are the only other order routes.
    assert sorted(op for op in order_operations if not op.startswith("GET ")) == [
        "POST /api/orders/cancel",
        "POST /api/orders/confirm",
        "POST /api/orders/payment-method",
        "POST /api/orders/payment-status",
    ]


def test_local_zepto_bridge_only_forwards_oauth_fields_to_fixed_backend() -> None:
    response = TestClient(callback_bridge, follow_redirects=False).get(
        "/callback",
        params={
            "state": "s" * 32,
            "code": "authorization-code",
            "iss": "https://auth.zepto.co.in",
        },
    )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "http://127.0.0.1:8000/api/zepto/oauth/callback?"
        "state=" + "s" * 32 + "&code=authorization-code&iss=https%3A%2F%2Fauth.zepto.co.in"
    )
