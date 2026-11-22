"""Payment state machine, payment-link safety and defensive response parsing."""

from __future__ import annotations

import logging

import pytest

from app.commerce.zepto.payments import (
    ORDER_TOOLS,
    PAYMENT_DIAGNOSTIC_EVENT,
    VERIFIED_CONTRACT,
    PaymentShapeUnknown,
    parse_allowed_methods,
    parse_payment_methods,
    parse_payment_start,
    parse_payment_status,
)
from app.shopping.models import CheckoutStatus
from app.shopping.payments import (
    CHECKABLE_STATUSES,
    PaymentStatusReport,
    next_checkout_status,
    safe_payment_url,
)
from tests.payment_fakes import (
    PAY_LINK,
    TEST_CONTRACT,
    ScriptedZeptoClient,
    methods_payload,
    payment_gateway,
    start_payload,
    status_payload,
)

S = CheckoutStatus


def report(payment: str, order: str = "unknown", poll: bool = False) -> PaymentStatusReport:
    return PaymentStatusReport(payment=payment, order=order, poll_instructed=poll)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("current", "payment", "order", "expected"),
    [
        (S.AWAITING_PAYMENT, "succeeded", "confirmed", S.CONFIRMED),
        (S.AWAITING_PAYMENT, "succeeded", "failed", S.PAID_ORDER_FAILED),
        (S.AWAITING_PAYMENT, "succeeded", "pending", S.PAYMENT_SUCCEEDED),
        (S.AWAITING_PAYMENT, "succeeded", "unknown", S.PAYMENT_SUCCEEDED),
        # Order OK but payment still pending: keep waiting, never "placed".
        (S.AWAITING_PAYMENT, "pending", "confirmed", S.AWAITING_PAYMENT),
        (S.AWAITING_PAYMENT, "pending", "unknown", S.AWAITING_PAYMENT),
        (S.AWAITING_PAYMENT, "pending", "failed", S.PAYMENT_UNKNOWN),
        (S.AWAITING_PAYMENT, "failed", "confirmed", S.PAYMENT_FAILED),
        (S.AWAITING_PAYMENT, "failed", "unknown", S.PAYMENT_FAILED),
        (S.AWAITING_PAYMENT, "unknown", "confirmed", S.PAYMENT_UNKNOWN),
        (S.PAYMENT_UNKNOWN, "pending", "unknown", S.AWAITING_PAYMENT),
        (S.PAYMENT_UNKNOWN, "succeeded", "confirmed", S.CONFIRMED),
        (S.PAYMENT_SUCCEEDED, "succeeded", "confirmed", S.CONFIRMED),
        # A confirmed payment read later as pending/failed is a contradiction.
        (S.PAYMENT_SUCCEEDED, "pending", "unknown", S.PAYMENT_UNKNOWN),
        (S.PAYMENT_SUCCEEDED, "failed", "unknown", S.PAYMENT_UNKNOWN),
    ],
)
def test_status_transitions(current, payment, order, expected) -> None:
    assert next_checkout_status(current, report(payment, order)) == expected


@pytest.mark.parametrize(
    "terminal",
    [S.CONFIRMED, S.OUTCOME_UNKNOWN, S.PAYMENT_FAILED, S.PAID_ORDER_FAILED, S.DISPATCHING],
)
def test_terminal_and_undispatched_states_never_change(terminal) -> None:
    assert terminal not in CHECKABLE_STATUSES
    for payment in ("succeeded", "failed", "pending", "unknown"):
        assert next_checkout_status(terminal, report(payment, "confirmed")) == terminal


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        (PAY_LINK, True),
        ("upi://pay?pa=merchant@bank&am=222", True),
        ("http://pay.example.test/x", False),
        ("javascript:alert(1)", False),
        ("data:text/html,hi", False),
        ("https://user:pw@pay.example.test/", False),
        ("https:///no-host", False),
        ("https://pay.example.test/a b", False),
        ("https://pay.example.test/\n", True),  # outer whitespace is trimmed
        ("https://pay.example.test/\x00", False),
        ("https://x.test/" + "a" * 2100, False),
        ("", False),
        (None, False),
        (123, False),
    ],
)
def test_only_https_pages_and_upi_intents_are_openable(raw, ok) -> None:
    assert (safe_payment_url(raw) is not None) is ok


def test_production_contract_is_unverified_so_only_cod_is_offered() -> None:
    assert VERIFIED_CONTRACT is None
    gateway = payment_gateway(ScriptedZeptoClient(), contract=VERIFIED_CONTRACT)
    assert gateway.offered() == frozenset()
    assert ORDER_TOOLS == {
        "cod": "create_order",
        "upi_reserve": "create_upi_reserve_pay_order",
        "wallet": "create_wallet_order",
        "online": "create_online_payment_order",
    }


def test_flag_and_allow_list_gate_what_may_be_offered() -> None:
    client = ScriptedZeptoClient()
    assert payment_gateway(client, enabled=False).offered() == frozenset()
    assert payment_gateway(client, allowed=frozenset({"online"})).offered() == {"online"}
    assert parse_allowed_methods(" online, wallet ,cod,bogus,") == {"online", "wallet"}
    assert parse_allowed_methods("") == frozenset()


def test_methods_listing_ignores_unknown_and_disabled_entries() -> None:
    payload = {
        "fakeMethods": [
            {"fakeId": "FAKE_ONLINE", "fakeEnabled": True},
            {"fakeId": "FAKE_WALLET", "fakeEnabled": False},
            {"fakeId": "FAKE_UPI"},  # enabled flag missing → not offered
            {"fakeId": "SOMETHING_NEW", "fakeEnabled": True},
            "junk",
            {"fakeEnabled": True},
        ]
    }
    assert parse_payment_methods(payload, TEST_CONTRACT.methods) == {"online"}
    assert parse_payment_methods({"data": methods_payload("FAKE_UPI")}, TEST_CONTRACT.methods) == {"upi_reserve"}
    for unknown in ({}, {"fakeMethods": {}}, {"content_text": "ok"}, [], "x"):
        with pytest.raises(PaymentShapeUnknown):
            parse_payment_methods(unknown, TEST_CONTRACT.methods)


def test_payment_start_needs_an_order_id_the_exact_amount_and_a_safe_link() -> None:
    online = TEST_CONTRACT.per_method["online"]
    start = parse_payment_start(start_payload(22200), online, 22200)
    assert start.provider_order_id == "ZP-1" and start.payment_url == PAY_LINK and not start.placed

    cases = {
        "order_id": {"fakeOrder": {"amount": 22200, "payLink": PAY_LINK}},
        "payable": {"fakeOrder": {"ref": "ZP-1", "amount": "222", "payLink": PAY_LINK}},
        "payable.mismatch": start_payload(22300),
        "payment_url": start_payload(22200, link="javascript:alert(1)"),
        "envelope": {"content_text": "Order created"},
    }
    for step, payload in cases.items():
        with pytest.raises(PaymentShapeUnknown) as error:
            parse_payment_start(payload, online, 22200)
        assert error.value.step == step


def test_wallet_is_placed_only_on_the_contract_value() -> None:
    wallet = TEST_CONTRACT.per_method["wallet"]
    assert parse_payment_start(start_payload(100, link=None, state="FAKE_PAID"), wallet, 100).placed
    pending = parse_payment_start(start_payload(100, link=None, state="SOMETHING"), wallet, 100)
    assert not pending.placed and pending.payment_url is None


def test_status_parsing_maps_unrecognised_values_to_unknown() -> None:
    contract = TEST_CONTRACT.status
    assert parse_payment_status(status_payload("FAKE_OK", "FAKE_PLACED"), contract) == report("succeeded", "confirmed")
    assert parse_payment_status(status_payload("FAKE_WAIT"), contract) == report("pending", "unknown")
    assert parse_payment_status(status_payload("NEW_VALUE", "OTHER"), contract) == report("unknown", "pending")
    assert parse_payment_status({"other": 1}, contract) == report("unknown", "unknown")
    assert parse_payment_status(status_payload("FAKE_WAIT", poll_next=True), contract).poll_instructed
    # Only a literal true instructs polling.
    assert not parse_payment_status({"fakePayment": "FAKE_WAIT", "fakePollNext": "true"}, contract).poll_instructed


async def test_unrecognised_status_logs_shape_only(caplog: pytest.LogCaptureFixture) -> None:
    client = ScriptedZeptoClient()
    client.script("check_payment_status", {"content_text": "Paid ₹222 by Asha, UPI asha@okbank"})
    gateway = payment_gateway(client)
    with caplog.at_level(logging.WARNING):
        result = await gateway.check_status("ZP-1", poll=False)
    assert result == report("unknown", "unknown")
    assert client.calls == [("check_payment_status", {"orderId": "ZP-1", "poll": False})]
    text = caplog.text
    assert PAYMENT_DIAGNOSTIC_EVENT in text and "content_text" in text
    assert "Asha" not in text and "okbank" not in text and "222" not in text


async def test_methods_are_cached_and_read_failures_hide_prepaid_methods() -> None:
    client = ScriptedZeptoClient()
    client.script("get_payment_methods", methods_payload("FAKE_ONLINE", "FAKE_UPI"))
    now = [0.0]
    gateway = payment_gateway(client)
    gateway.monotonic = lambda: now[0]
    assert await gateway.available_methods("h") == ("cod", "upi_reserve", "online")
    assert await gateway.available_methods("h") == ("cod", "upi_reserve", "online")
    assert client.names() == ["get_payment_methods"]

    failing = ScriptedZeptoClient()
    failing.script("get_payment_methods", TimeoutError("slow"))
    assert await payment_gateway(failing).available_methods("h") == ("cod",)

    off = ScriptedZeptoClient()
    assert await payment_gateway(off, enabled=False).available_methods("h") == ("cod",)
    assert off.calls == []
