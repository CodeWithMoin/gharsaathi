import json
from pathlib import Path

from app.commerce.zepto.policy import ToolCapability, classify_catalogue


FIXTURE = Path(__file__).parent / "fixtures" / "zepto_tools_catalogue_2026-11-21.json"


def _catalogue() -> dict[str, dict[str, object]]:
    entries = json.loads(FIXTURE.read_text())
    return {entry["name"]: entry["input_schema"] for entry in entries}


def test_redacted_live_catalogue_covers_supported_read_capabilities() -> None:
    catalogue = _catalogue()

    assert catalogue["view_cart"]["required"] == []
    assert catalogue["list_order_history"]["required"] == []
    assert catalogue["get_order_detail"]["required"] == ["orderId"]
    assert set(catalogue["list_order_history"]["properties"]) == {
        "limit",
        "pageNumber",
    }


def test_redacted_live_catalogue_keeps_cart_and_order_tools_consequential() -> None:
    catalogue = _catalogue()
    safety = classify_catalogue(catalogue)

    assert safety["view_cart"].capability is ToolCapability.CART
    assert safety["view_cart"].retry_on_transport_failure is False
    assert safety["update_cart"].capability is ToolCapability.CART
    assert safety["create_order"].capability is ToolCapability.ORDER
    assert safety["create_online_payment_order"].capability is ToolCapability.ORDER

    update = catalogue["update_cart"]
    assert update["required"] == ["deviceId", "cartItems"]
    item_schema = update["properties"]["cartItems"]["items"]
    assert item_schema["required"] == [
        "productVariantId",
        "storeProductId",
        "quantity",
    ]


def test_order_confirmation_flag_is_present_on_every_live_order_tool() -> None:
    catalogue = _catalogue()
    order_tools = {
        "create_order",
        "create_online_payment_order",
        "create_wallet_order",
        "create_upi_reserve_pay_order",
    }

    for name in order_tools:
        confirm = catalogue[name]["properties"]["confirmOrder"]
        assert confirm["type"] == "boolean"
        assert "confirm" in confirm["description"].lower()
