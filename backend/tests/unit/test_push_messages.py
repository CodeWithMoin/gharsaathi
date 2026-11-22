"""Browser notification text and payload rules: short, private, meaningful only."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from app.notifications import messages
from app.notifications.models import PUSH_KINDS, PushKind, PushPayload
from app.notifications.store import InvalidSubscription, validate_endpoint
from app.notifications.vapid import generate, public_key_for

AT = datetime(2026, 11, 22, 2, 56, tzinfo=UTC)  # 8:26 am in India
NAMES = ["Amul Taaza Toned Milk 1 L", "Britannia Bread", "Eggs 6 pack", "Tata Salt"]


def every_message() -> list[PushPayload]:
    return [
        messages.order_placed("Zepto", "ORD1", 21250, NAMES),
        messages.order_placed("Zepto", "ORD1", 21250, NAMES[:1]),
        messages.order_placed("Zepto", "ORD1", 21250, []),
        messages.order_placed("Zepto", "ORD1", 21250, NAMES, earlier=True),
        messages.out_for_delivery("Zepto", "ORD1", AT),
        messages.out_for_delivery("Zepto", "ORD1", None),
        messages.delivered("Zepto", "ORD1", AT),
        messages.delayed("Zepto", "ORD1", AT),
        messages.cancelled("Zepto", "ORD1"),
        messages.order_still_unknown("Zepto", 21250),
        messages.payment_needed("Zepto", 21250),
        messages.payment_unclear("Zepto"),
        messages.payment_failed("Zepto"),
        messages.paid_order_failed("Zepto"),
        messages.checkout_expiring("Zepto"),
        messages.test_message(),
    ]


def test_clock_time_is_india_time() -> None:
    assert messages.clock_time(AT) == "8:26 am"
    assert messages.clock_time(datetime(2026, 11, 22, 14, 5, tzinfo=UTC)) == "7:35 pm"


def test_item_phrase_names_at_most_two_products() -> None:
    assert messages.item_phrase([]) == ""
    assert messages.item_phrase(NAMES[:1]) == "Amul Taaza Toned Milk 1 L"
    assert messages.item_phrase(NAMES[:2]) == "Amul Taaza Toned Milk 1 L and Britannia Bread"
    assert messages.item_phrase(NAMES) == "Amul Taaza Toned Milk 1 L, Britannia Bread and 2 more"
    assert messages.item_phrase(["x" * 60])[-1] == "…"


@pytest.mark.parametrize("payload", every_message(), ids=lambda payload: payload.title)
def test_payloads_are_short_and_private(payload: PushPayload) -> None:
    text = f"{payload.title} {payload.body}"
    assert len(payload.title) <= 48 and len(payload.body) <= 160
    # Only the order total is ever an amount.
    assert set(re.findall(r"₹[\d,]+(?:\.\d+)?", text)) <= {"₹212.50"}
    assert "Eggs" not in text and "Tata Salt" not in text
    assert payload.url.startswith("/") and not payload.url.startswith("//")
    lowered = text.casefold()
    for banned in ("stock", "khatam", "usually", "running out", "reorder", "address", "flat", "upi pin", "otp"):
        assert banned not in lowered


def test_delivered_uses_the_stores_time() -> None:
    assert messages.delivered("Zepto", "ORD1", AT).body == "Your Zepto order was delivered at 8:26 am."


def test_order_url_links_to_the_order_card() -> None:
    assert messages.out_for_delivery("Zepto", "A/B 1", None).url == "/?order=A%2FB%201"
    assert messages.out_for_delivery("Zepto", "ORD1", None).tag == "order:ORD1"


def test_only_meaningful_kinds_exist() -> None:
    assert set(PUSH_KINDS) == set(get_args(PushKind))
    for kind in PUSH_KINDS:
        assert not any(word in kind for word in ("usual", "replenish", "stock", "reminder", "rider"))


@pytest.mark.parametrize("url", ["https://evil.example/x", "//evil.example", "javascript:alert(1)", "/\\evil"])
def test_payload_url_must_be_same_origin(url: str) -> None:
    with pytest.raises(ValidationError):
        PushPayload(kind="test", title="t", body="b", url=url)


def test_payload_text_is_one_line_and_bounded() -> None:
    assert PushPayload(kind="test", title="a\n b", body="c\t\td").title == "a b"
    with pytest.raises(ValidationError):
        PushPayload(kind="test", title="t" * 49, body="b")
    with pytest.raises(ValidationError):
        PushPayload(kind="test", title="t", body="b" * 161)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/QAbc",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
    ],
)
def test_known_push_services_are_accepted(endpoint: str) -> None:
    assert validate_endpoint(endpoint) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/abc",
        "https://127.0.0.1/x",
        "https://evil.example/fcm.googleapis.com",
        "https://fcm.googleapis.com.evil.example/x",
        "https://user:pw@fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x",
    ],
)
def test_other_endpoints_are_refused(endpoint: str) -> None:
    with pytest.raises(InvalidSubscription):
        validate_endpoint(endpoint)


def test_generated_vapid_keys_pair_up() -> None:
    public, private = generate()
    assert public_key_for(private) == public
    assert len(public) == 87 and public.startswith("B")
    assert public_key_for("not-a-key") is None
    assert public_key_for(generate()[1]) != public
