"""Sarvam API tool get_delivery_context: fake tracking, addresses, directions and profile.

Nothing leaves the machine: no Sarvam call, no store call, no tunnel.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.sarvam_tool import get_live_context_service
from app.commerce.swiggy.models import Address, AddressList, AddressResolution, Pagination
from app.core.config import Settings
from app.inbound_delivery.live_context import (
    NO_ORDER_MESSAGE,
    TOKEN_HEADER,
    LiveDeliveryContextService,
    RateLimiter,
    format_time,
    tool_spec,
)
from app.main import app
from app.tracking.models import OrderWatch, TrackingProvider, TrackingSnapshot, TrackingStatus

OWNER = "owner-1"
TOKEN = "tool-token-" + "a" * 40
NOW = datetime(2026, 11, 22, 14, 40, tzinfo=UTC)  # 8:10 pm IST
HOME = "Flat 402, Lake View Apartments, 12 Test Street, HSR Layout, Bengaluru 560102"
OWNER_PHONE = "+91 98765 43210"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "sarvam_live_context_enabled": True,
        "sarvam_tool_token": TOKEN,
        "sarvam_inbound_owner_user_id": OWNER,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def watch(
    status: TrackingStatus,
    *,
    provider: TrackingProvider = TrackingProvider.ZEPTO,
    address_id: str | None = "z-home",
    active: bool = True,
    delivered_ago: timedelta | None = None,
    eta: datetime | None = None,
    order_id: str = "order-1",
) -> OrderWatch:
    delivered_at = NOW - delivered_ago if delivered_ago is not None else None
    snapshot = TrackingSnapshot(
        status=status,
        eta_at=eta,
        delivered=status == TrackingStatus.DELIVERED,
        cancelled=status == TrackingStatus.CANCELLED,
        delivered_at=delivered_at,
    )
    return OrderWatch(
        id="00000000-0000-4000-8000-000000000001",
        user_id=OWNER,
        provider=provider,
        order_id=order_id,
        address_id=address_id,
        active=active,
        status=status,
        snapshot=snapshot,
        created_at=NOW - timedelta(minutes=20),
        updated_at=delivered_at or NOW - timedelta(minutes=1),
        stopped_at=delivered_at,
    )


class FakeWatches:
    def __init__(self, *watches: OrderWatch) -> None:
        self.watches = list(watches)
        self.users: list[str] = []

    def list(self, user_id: str, *, active_only: bool = False) -> list[OrderWatch]:
        self.users.append(user_id)
        return [item for item in self.watches if item.user_id == user_id and (item.active or not active_only)]


class FakeAddressBook:
    async def saved(self, household_id: str, provider: str) -> AddressList | None:
        if household_id != OWNER:
            return None
        prefix = "z" if provider == "zepto" else "s"
        return AddressList(
            addresses=[
                Address(id=f"{prefix}-home", tag="Home", address_line=HOME),
                Address(id=f"{prefix}-office", tag="Office", address_line="3rd floor, Tech Park, Bengaluru"),
            ],
            pagination=Pagination(total=2),
            resolution=AddressResolution(default_address_id=f"{prefix}-office"),
        )


class FakeDirections:
    def __init__(self) -> None:
        self.saved = {
            ("zepto", "z-home"): SimpleNamespace(
                landmark="Opposite the blue temple",
                route_directions=["Enter by gate 2", "Tower B, lift to 4th floor", f"Call {OWNER_PHONE} if lost"],
                gate_note="Tell the guard flat 402",
                handover_instructions="Guard ke paas chhod dijiye",
            )
        }

    async def directions(self, user_id: str, provider: str, address_id: str):
        assert user_id == OWNER
        return self.saved.get((provider, address_id))


class FakeProfiles:
    async def read(self, user_id: str):
        return SimpleNamespace(first_name="Moin", phone=OWNER_PHONE) if user_id == OWNER else None


class FakeOrderAddresses:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}

    async def address_id(self, user_id: str, provider: str, order_id: str) -> str | None:
        return self.mapping.get(order_id)


def service(*watches: OrderWatch, limiter: RateLimiter | None = None, order_addresses=None, **overrides: object):
    return LiveDeliveryContextService(
        settings(**overrides),
        FakeWatches(*watches),
        FakeAddressBook(),
        FakeDirections(),
        FakeProfiles(),
        order_addresses=order_addresses,
        clock=lambda: NOW,
        limiter=limiter,
    )


@pytest.fixture
def call():
    def run(svc: LiveDeliveryContextService, body: object | None = None, *, token: str | None = TOKEN, params=None):
        app.dependency_overrides[get_live_context_service] = lambda: svc
        try:
            headers = {TOKEN_HEADER: token} if token is not None else {}
            return TestClient(app).post(
                "/api/sarvam/delivery-context",
                json=body if body is not None else {"provider": "zepto"},
                headers=headers,
                params=params,
            )
        finally:
            app.dependency_overrides.pop(get_live_context_service, None)

    return run


def test_no_order_is_not_active(call) -> None:
    response = call(service())
    assert response.status_code == 200
    body = response.json()
    assert body["active"] == "no"
    assert body["delivery_address"] == "NOT_AVAILABLE"
    assert body["message_for_agent"] == NO_ORDER_MESSAGE
    assert all(isinstance(value, str) for value in body.values())


def test_out_for_delivery_returns_the_full_context(call) -> None:
    eta = datetime(2026, 11, 22, 14, 56, tzinfo=UTC)  # 8:26 pm IST
    svc = service(watch(TrackingStatus.OUT_FOR_DELIVERY, eta=eta))
    body = call(svc, {"provider": "Zepto", "rider_location_hint": "near the temple"}).json()
    assert body["active"] == "yes"
    assert body["provider"] == "zepto"
    assert body["provider_mismatch"] == "no"
    assert body["order_status"] == "out for delivery"
    assert body["recipient_name"] == "Moin"
    assert body["delivery_address"] == HOME
    assert body["landmark"] == "Opposite the blue temple"
    assert body["route_directions"].startswith("1) Enter by gate 2; 2) Tower B, lift to 4th floor; 3) Call")
    assert body["route_directions"].endswith("4) Gate: Tell the guard flat 402")
    assert body["handover_instructions"] == "Guard ke paas chhod dijiye"
    assert body["eta"] == "8:26 pm"
    assert "Zepto order is on its way" in body["message_for_agent"]
    assert all(isinstance(value, str) for value in body.values())
    assert svc.watches.users == [OWNER]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(TrackingStatus.CONFIRMED, "order placed"), (TrackingStatus.PREPARING, "being packed")],
)
def test_placed_and_packed_orders_are_active(call, status: TrackingStatus, expected: str) -> None:
    body = call(service(watch(status))).json()
    assert body["active"] == "yes"
    assert body["order_status"] == expected


def test_delivered_ten_minutes_ago_is_still_active(call) -> None:
    body = call(service(watch(TrackingStatus.DELIVERED, active=False, delivered_ago=timedelta(minutes=10)))).json()
    assert body["active"] == "yes"
    assert body["order_status"] == "delivered"
    assert body["eta"] == "NOT_AVAILABLE"
    assert "delivered a few minutes ago" in body["message_for_agent"]


def test_delivered_thirty_minutes_ago_is_not_active(call) -> None:
    body = call(service(watch(TrackingStatus.DELIVERED, active=False, delivered_ago=timedelta(minutes=30)))).json()
    assert body["active"] == "no"
    assert body["delivery_address"] == "NOT_AVAILABLE"


def test_cancelled_is_not_active(call) -> None:
    assert call(service(watch(TrackingStatus.CANCELLED, active=False))).json()["active"] == "no"


@pytest.mark.parametrize("status", [TrackingStatus.UNKNOWN, TrackingStatus.PAUSED, TrackingStatus.UNAVAILABLE])
def test_unconfirmed_statuses_are_not_active(call, status: TrackingStatus) -> None:
    assert call(service(watch(status))).json()["active"] == "no"


def test_a_stale_or_stopped_watch_is_not_active(call) -> None:
    stale = watch(TrackingStatus.OUT_FOR_DELIVERY)
    stale.created_at = NOW - timedelta(hours=13)
    stopped = watch(TrackingStatus.OUT_FOR_DELIVERY, active=False)
    assert call(service(stale)).json()["active"] == "no"
    assert call(service(stopped)).json()["active"] == "no"


def test_other_users_orders_are_never_used(call) -> None:
    other = watch(TrackingStatus.OUT_FOR_DELIVERY)
    other.user_id = "someone-else"
    assert call(service(other)).json()["active"] == "no"


@pytest.mark.failure_mode
@pytest.mark.parametrize("token", [None, "", "wrong-" + "b" * 40, TOKEN + "x", TOKEN[:-1]])
def test_fm_lc01_wrong_or_missing_token_is_401_without_details(call, token: str | None) -> None:
    response = call(service(watch(TrackingStatus.OUT_FOR_DELIVERY)), token=token)
    assert response.status_code == 401
    body = response.json()
    assert body["active"] == "no"
    assert "connect Moin" in body["message_for_agent"]
    assert HOME not in response.text and "temple" not in response.text


def test_token_may_also_be_a_query_param(call) -> None:
    response = call(service(watch(TrackingStatus.OUT_FOR_DELIVERY)), token=None, params={"tool_token": TOKEN})
    assert response.status_code == 200
    assert response.json()["active"] == "yes"


@pytest.mark.parametrize(
    "overrides",
    [
        {"sarvam_live_context_enabled": False},
        {"sarvam_tool_token": ""},
        {"sarvam_tool_token": "short-token"},
        {"sarvam_inbound_owner_user_id": ""},
    ],
)
def test_feature_off_or_not_set_up_is_404(call, overrides: dict[str, object]) -> None:
    response = call(service(watch(TrackingStatus.OUT_FOR_DELIVERY), **overrides))
    assert response.status_code == 404
    assert HOME not in response.text


def test_feature_is_off_by_default() -> None:
    assert Settings(_env_file=None).sarvam_live_context_enabled is False
    assert Settings(_env_file=None).sarvam_tool_token.get_secret_value() == ""


def test_rate_limit(call) -> None:
    ticks = [0.0]
    svc = service(watch(TrackingStatus.OUT_FOR_DELIVERY), limiter=RateLimiter(clock=lambda: ticks[0]))
    codes = [call(svc).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
    ticks[0] = 61.0
    assert call(svc).status_code == 200


def test_no_phone_numbers_in_output(call) -> None:
    svc = service(watch(TrackingStatus.OUT_FOR_DELIVERY))
    svc.directions.saved[("zepto", "z-home")].landmark = "Opposite temple, call 080-2345-6789"
    svc.directions.saved[("zepto", "z-home")].handover_instructions = "Ring 9876543210 at the gate"
    text = call(svc).text
    digits = "".join(ch for ch in text if ch.isdigit() or ch == " ")
    for number in ("9876543210", "98765 43210", "08023456789", "2345-6789"):
        assert number not in text
    assert "43210" not in digits.replace(" ", "")
    assert "560102" in text  # the PIN code of the address stays


def test_otp_like_codes_are_hidden(call) -> None:
    svc = service(watch(TrackingStatus.OUT_FOR_DELIVERY))
    svc.directions.saved[("zepto", "z-home")].handover_instructions = "OTP is 4821, give to guard"
    text = call(svc).text
    assert "4821" not in text


def test_provider_mismatch_is_flagged_but_still_active(call) -> None:
    body = call(service(watch(TrackingStatus.OUT_FOR_DELIVERY)), {"provider": "swiggy"}).json()
    assert body["active"] == "yes"
    assert body["provider"] == "zepto"
    assert body["provider_mismatch"] == "yes"
    assert "confirm the store" in body["message_for_agent"]


def test_unknown_or_missing_provider_is_not_a_mismatch(call) -> None:
    svc = service(watch(TrackingStatus.OUT_FOR_DELIVERY))
    for body in ({"provider": "unknown"}, {}, {"provider": 7}, ["junk"]):
        result = call(svc, body).json()
        assert result["active"] == "yes"
        assert result["provider_mismatch"] == "no"


def test_the_matching_store_order_is_preferred(call) -> None:
    zepto = watch(TrackingStatus.OUT_FOR_DELIVERY)
    swiggy = watch(TrackingStatus.PREPARING, provider=TrackingProvider.SWIGGY, address_id="s-home", order_id="s-1")
    body = call(service(zepto, swiggy), {"provider": "swiggy"}).json()
    assert body["provider"] == "swiggy"
    assert body["provider_mismatch"] == "no"
    assert body["delivery_address"] == HOME
    assert body["landmark"] == "NOT_AVAILABLE"  # nothing saved for the Swiggy address
    assert "No saved directions" in body["message_for_agent"]


def test_order_address_comes_from_the_checkout_basket_then_the_selected_address(call) -> None:
    no_id = watch(TrackingStatus.OUT_FOR_DELIVERY, address_id=None)
    from_basket = call(service(no_id, order_addresses=FakeOrderAddresses({"order-1": "z-home"}))).json()
    assert from_basket["delivery_address"] == HOME
    assert from_basket["landmark"] == "Opposite the blue temple"
    selected = call(service(no_id)).json()
    assert selected["delivery_address"] == "3rd floor, Tech Park, Bengaluru"
    assert selected["route_directions"] == "NOT_AVAILABLE"


def test_logs_are_content_free(call, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.inbound_delivery.live_context")
    svc = service(watch(TrackingStatus.OUT_FOR_DELIVERY))
    call(svc, {"provider": "zepto", "rider_location_hint": "SECRET-HINT near temple"})
    call(svc, token="wrong-" + "c" * 40)
    messages = [record.getMessage() for record in caplog.records if record.name == "app.inbound_delivery.live_context"]
    assert "event=sarvam_live_context active=yes provider=zepto mismatch=no" in messages
    assert "event=sarvam_live_context result=unauthorized" in messages
    joined = " ".join(messages)
    for secret in ("SECRET-HINT", "temple", "Moin", "402", "Guard", TOKEN, "order-1", OWNER):
        assert secret not in joined


def test_query_token_is_redacted_from_access_logs() -> None:
    from app.core.logging import RedactOAuthCallbackFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "POST", f"/api/sarvam/delivery-context?tool_token={TOKEN}", "1.1", 200), None,
    )
    RedactOAuthCallbackFilter().filter(record)
    assert TOKEN not in record.getMessage()


def test_eta_is_india_time() -> None:
    assert format_time(datetime(2026, 11, 22, 14, 56, tzinfo=UTC)) == "8:26 pm"
    assert format_time(datetime(2026, 11, 22, 3, 5, tzinfo=UTC)) == "8:35 am"
    assert format_time(datetime(2026, 11, 22, 6, 30, tzinfo=UTC)) == "12:00 pm"


def test_tool_spec_never_contains_the_token() -> None:
    spec = tool_spec("https://example.trycloudflare.com/")
    assert spec["url"] == "https://example.trycloudflare.com/api/sarvam/delivery-context"
    assert spec["name"] == "get_delivery_context" and spec["lifecycle"] == "run" and spec["method"] == "POST"
    assert spec["headers"][0] == {"name": TOKEN_HEADER, "type": "static", "value": "<SARVAM_TOOL_TOKEN>"}
    assert [param["name"] for param in spec["body_params"]] == ["provider", "rider_location_hint"]
    assert tool_spec("http://insecure")["url"].startswith("<public origin>")
