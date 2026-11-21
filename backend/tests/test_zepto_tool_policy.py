"""TDD safety net for future Zepto cart and ordering work.

These tests use only locally constructed catalogue metadata.  They must never
connect to Zepto or invoke an MCP tool.
"""

import pytest

from app.commerce.zepto.policy import (
    ExactOrderApproval,
    OrderAction,
    OrderApprovalRequired,
    ToolCapability,
    classify_catalogue,
    classify_tool,
    require_exact_order_approval,
)
from app.main import app


@pytest.mark.parametrize(
    ("tool_name", "capability", "may_retry"),
    [
        ("search_products", ToolCapability.READ, True),
        ("getOrderStatus", ToolCapability.READ, True),
        ("list_saved_addresses", ToolCapability.READ, True),
        ("add_to_cart", ToolCapability.CART, False),
        ("updateBasket", ToolCapability.CART, False),
        ("clear_cart", ToolCapability.CART, False),
        ("checkout", ToolCapability.ORDER, False),
        ("place_order", ToolCapability.ORDER, False),
        ("confirmPayment", ToolCapability.ORDER, False),
        ("future_provider_action", ToolCapability.UNKNOWN, False),
    ],
)
def test_discovered_tool_names_are_classified_conservatively(
    tool_name: str, capability: ToolCapability, may_retry: bool
) -> None:
    safety = classify_tool(tool_name)

    assert safety.capability is capability
    assert safety.retry_on_transport_failure is may_retry


def test_catalogue_classification_never_turns_discovery_into_authorization() -> None:
    catalogue = classify_catalogue(["search_products", "add_to_cart", "place_order"])

    assert catalogue["search_products"].capability is ToolCapability.READ
    assert catalogue["add_to_cart"].capability is ToolCapability.CART
    assert catalogue["place_order"].capability is ToolCapability.ORDER
    assert catalogue["place_order"].requires_exact_approval is True


def test_exact_approval_is_bound_to_the_order_tool_basket_price_and_payload() -> None:
    action = OrderAction(
        tool_name="place_order",
        basket_id="basket-1",
        basket_revision=3,
        basket_fingerprint="f" * 64,
        payable_paise=32750,
        arguments={"addressId": "addr-1", "payment": "cod"},
    )
    approval = ExactOrderApproval.for_action("approval-1", action)

    require_exact_order_approval(action, approval)

    changed_price = OrderAction(
        tool_name=action.tool_name,
        basket_id=action.basket_id,
        basket_revision=action.basket_revision,
        basket_fingerprint=action.basket_fingerprint,
        payable_paise=32751,
        arguments=action.arguments,
    )
    with pytest.raises(OrderApprovalRequired, match="does not match"):
        require_exact_order_approval(changed_price, approval)

    changed_payload = OrderAction(
        tool_name=action.tool_name,
        basket_id=action.basket_id,
        basket_revision=action.basket_revision,
        basket_fingerprint=action.basket_fingerprint,
        payable_paise=action.payable_paise,
        arguments={"addressId": "addr-2", "payment": "cod"},
    )
    with pytest.raises(OrderApprovalRequired, match="does not match"):
        require_exact_order_approval(changed_payload, approval)


def test_zepto_public_api_has_no_cart_or_order_mutation_until_live_schemas_are_verified() -> None:
    methods = {"post", "put", "patch", "delete"}
    mutations = [
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/zepto")
        for method in operations
        if method.lower() in methods
    ]

    forbidden = ("cart", "basket", "checkout", "order", "payment", "purchase")
    assert all(not any(word in operation.lower() for word in forbidden) for operation in mutations)
