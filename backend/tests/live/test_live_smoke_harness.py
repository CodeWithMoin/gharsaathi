"""Offline tests for the live smoke harness. No provider is contacted."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.commerce.swiggy.errors import SwiggyRateLimited
from app.commerce.zepto.errors import ZeptoRateLimited
from app.commerce.zepto.quote_provider import (
    ZeptoCodCheckoutGateway,
    ZeptoQuoteProvider,
)
from app.shopping.models import Basket, BasketLine, ProductRef
from tests.live import conftest as live_conftest
from tests.live import test_live_read_smoke, test_live_zepto_cart_smoke
from tests.live.harness import (
    CART_ENV,
    FIXTURE_DIR,
    LIVE_ENV,
    ForbiddenLiveCall,
    GuardedToolClient,
    LiveSmokeHalted,
    RateLimitTripwire,
    SmokeRecorder,
    assert_call_permitted,
    compare_catalogues,
    guarded_for_tier,
    live_skip_reason,
    safe_key,
    shape_of,
    structural_schema,
)

BOTH = {LIVE_ENV: "1"}
CART = {LIVE_ENV: "1", CART_ENV: "1"}


class FakeClient:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_calls = 0

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, retry_on_auth: bool = True
    ) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return {"ok": True}

    async def list_tools(self) -> list[Any]:
        self.list_calls += 1
        return []

    async def prepare_session(self) -> int:
        return 1

    @property
    def session_generation(self) -> int:
        return 1

    async def aclose(self) -> None:
        return None


def guarded(
    inner: FakeClient,
    *,
    provider: str = "zepto",
    tier: str = "read",
    env: dict[str, str] | None = None,
    tripwire: RateLimitTripwire | None = None,
) -> GuardedToolClient:
    return GuardedToolClient(
        inner,
        provider=provider,
        tier=tier,
        tripwire=tripwire or RateLimitTripwire(),
        recorder=SmokeRecorder(),
        env=BOTH if env is None else env,
    )


def zepto_basket() -> Basket:
    now = datetime.now(UTC)
    return Basket(
        id="b1",
        household_id="h1",
        provider="zepto",
        address_id="addr-1",
        lines=[
            BasketLine(
                ref=ProductRef(
                    provider="zepto", product_id="p1", variant_id="v1", sku_id="s1"
                ),
                name="Milk",
                unit_price_paise=3000,
                quantity=1,
            )
        ],
        created_at=now,
        updated_at=now,
    )


# ---- opt-in gating ----------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "env", "cart", "runs"),
    [
        (False, {}, False, False),
        (False, BOTH, False, False),
        (True, {}, False, False),
        (True, {LIVE_ENV: "true"}, False, False),
        (True, {LIVE_ENV: "0"}, False, False),
        (True, BOTH, False, True),
        (True, BOTH, True, False),
        (True, {CART_ENV: "1"}, True, False),
        (False, CART, True, False),
        (True, CART, True, True),
    ],
)
def test_live_gate_requires_flag_and_env(
    flag: bool, env: dict[str, str], cart: bool, runs: bool
) -> None:
    reason = live_skip_reason(run_live_flag=flag, env=env, cart=cart)
    assert (reason is None) is runs


class _Item:
    def __init__(self, *markers: str) -> None:
        self.markers = set(markers)
        self.added: list[Any] = []

    def get_closest_marker(self, name: str) -> object | None:
        return name if name in self.markers else None

    def add_marker(self, marker: Any) -> None:
        self.added.append(marker)


class _Config:
    def __init__(self, run_live: bool) -> None:
        self.run_live = run_live

    def getoption(self, name: str, default: object = None) -> object:
        return self.run_live


def test_collection_skips_live_items_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_ENV, raising=False)
    monkeypatch.delenv(CART_ENV, raising=False)
    read, cart, offline = _Item("live"), _Item("live", "live_cart"), _Item()

    live_conftest.pytest_collection_modifyitems(_Config(False), [read, cart, offline])  # type: ignore[arg-type]

    assert [m.name for m in read.added] == ["skip"]
    assert [m.name for m in cart.added] == ["skip"]
    assert offline.added == []


def test_collection_keeps_cart_tier_skipped_without_second_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_ENV, "1")
    monkeypatch.delenv(CART_ENV, raising=False)
    read, cart = _Item("live"), _Item("live", "live_cart")

    live_conftest.pytest_collection_modifyitems(_Config(True), [read, cart])  # type: ignore[arg-type]

    assert read.added == []
    assert [m.name for m in cart.added] == ["skip"]


def test_every_live_module_is_marked_live() -> None:
    for module in (test_live_read_smoke, test_live_zepto_cart_smoke):
        names = {mark.name for mark in module.pytestmark}
        assert "live" in names
    cart_names = {mark.name for mark in test_live_zepto_cart_smoke.pytestmark}
    assert "live_cart" in cart_names


# ---- forbidden-call guard ---------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {"confirmOrder": True, "userAddressId": "a"},
        {"confirmOrder": "true", "userAddressId": "a"},
        {"confirmOrder": 1},
        {"confirmOrder": None},
        {"userAddressId": "a"},  # omitted: Zepto's schema default is not trusted
        {"confirmOrder": False, "useZeptoCash": True},
        {"confirmOrder": False, "riderTip": 10},
    ],
)
def test_guard_refuses_any_create_order_but_exact_preview(arguments: dict[str, Any]) -> None:
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "cart", "create_order", arguments, CART)


def test_guard_allows_only_the_exact_unconfirmed_preview_in_cart_tier() -> None:
    preview = {"confirmOrder": False, "userAddressId": "a"}
    assert_call_permitted("zepto", "cart", "create_order", preview, CART)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "read", "create_order", preview, CART)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "cart", "create_order", preview, BOTH)


PREPAID_TOOLS = (
    "create_online_payment_order",
    "create_wallet_order",
    "create_upi_reserve_pay_order",
)


@pytest.mark.parametrize("name", PREPAID_TOOLS)
def test_prepaid_tools_allow_only_an_unconfirmed_preview_in_cart_tier(name: str) -> None:
    preview = {"confirmOrder": False, "userAddressId": "a"}
    assert_call_permitted("zepto", "cart", name, preview, CART)
    for arguments in (
        {"confirmOrder": True, "userAddressId": "a"},
        {"confirmOrder": "false", "userAddressId": "a"},
        {"confirmOrder": 0},
        {"userAddressId": "a"},
        {"confirmOrder": False, "useZeptoCash": False},
        {"confirmOrder": False, "riderTip": 0},
    ):
        with pytest.raises(ForbiddenLiveCall):
            assert_call_permitted("zepto", "cart", name, arguments, CART)
    # Never in the read tier, never without the cart opt-in, never for Swiggy.
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "read", name, preview, CART)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "cart", name, preview, BOTH)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("swiggy", "cart", name, preview, CART)


def test_payment_methods_listing_is_read_only_and_argument_free() -> None:
    assert_call_permitted("zepto", "read", "get_payment_methods", {}, BOTH)
    assert_call_permitted("zepto", "cart", "get_payment_methods", {}, CART)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "read", "get_payment_methods", {"x": 1}, BOTH)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("swiggy", "read", "get_payment_methods", {}, BOTH)


@pytest.mark.parametrize(
    "arguments",
    [{"orderId": "o"}, {"orderId": "o", "poll": False}, {"orderId": "o", "poll": True}],
)
def test_payment_status_is_never_callable_and_polling_never_starts(
    arguments: dict[str, Any],
) -> None:
    for tier in ("read", "cart"):
        with pytest.raises(ForbiddenLiveCall):
            assert_call_permitted("zepto", tier, "check_payment_status", arguments, CART)
    with pytest.raises(ForbiddenLiveCall, match="polling"):
        assert_call_permitted("zepto", "read", "search_products", {"poll": True}, BOTH)
    with pytest.raises(ForbiddenLiveCall, match="polling"):
        assert_call_permitted("zepto", "cart", "update_cart", {"x": [{"pollNow": 1}]}, CART)


async def test_prepaid_confirmation_never_reaches_the_inner_client() -> None:
    inner = FakeClient()
    client = guarded(inner, tier="cart", env=CART)
    for name in PREPAID_TOOLS:
        with pytest.raises(ForbiddenLiveCall):
            await client.call_tool(name, {"confirmOrder": True, "userAddressId": "a"})
    with pytest.raises(ForbiddenLiveCall):
        await client.call_tool("check_payment_status", {"orderId": "o", "poll": False})
    assert inner.calls == []


@pytest.mark.parametrize(
    "name",
    [
        "checkout",
        "place_order",
        "confirm_order",
        "cancel_order",
        "zepto_shop",
        "add_saved_address",
    ],
)
def test_guard_refuses_payment_checkout_and_unlisted_tools(name: str) -> None:
    for tier in ("read", "cart"):
        with pytest.raises(ForbiddenLiveCall):
            assert_call_permitted("zepto", tier, name, {"confirmOrder": False}, CART)


def test_guard_refuses_nested_confirm_flags_and_unknown_tiers() -> None:
    nested = {"deviceId": "d", "cartItems": [{"confirmNow": True}]}
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "cart", "update_cart", nested, CART)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "admin", "view_cart", {}, CART)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("swiggy", "cart", "update_cart", {}, CART)
    with pytest.raises(ForbiddenLiveCall):
        assert_call_permitted("zepto", "read", "update_cart", {}, CART)


async def test_forbidden_call_never_reaches_the_inner_client() -> None:
    inner = FakeClient()
    client = guarded(inner, tier="cart", env=CART)

    with pytest.raises(ForbiddenLiveCall):
        await client.call_tool("create_order", {"confirmOrder": True})

    assert inner.calls == []


async def test_forbidden_call_escapes_quote_provider_exception_wrapping() -> None:
    """``quote_unlocked`` catches Exception; the guard must still stop it."""

    inner = FakeClient()
    client = guarded(inner, tier="read")

    with pytest.raises(ForbiddenLiveCall):
        await ZeptoQuoteProvider(client).quote_unlocked(zepto_basket())

    assert [name for name, _ in inner.calls] == ["select_saved_address"]


async def test_production_confirm_gateway_is_unusable_through_the_guard() -> None:
    inner = FakeClient()
    client = guarded(inner, tier="cart", env=CART)

    with pytest.raises(ForbiddenLiveCall):
        await ZeptoCodCheckoutGateway(client).confirm_cod_unlocked(zepto_basket())

    assert inner.calls == []


async def test_guard_forwards_a_copy_of_checked_arguments() -> None:
    inner = FakeClient()
    client = guarded(inner, tier="cart", env=CART)
    arguments = {"confirmOrder": False, "userAddressId": "a"}

    await client.call_tool("create_order", arguments)
    arguments["confirmOrder"] = True

    assert inner.calls == [("create_order", {"confirmOrder": False, "userAddressId": "a"})]


async def test_cart_tier_sibling_shares_tripwire_and_transport() -> None:
    inner = FakeClient(SwiggyRateLimited("limited", 7))
    read = guarded(inner, tier="read", env=CART)
    cart = guarded_for_tier(read, "cart")

    with pytest.raises(LiveSmokeHalted):
        await read.call_tool("list_saved_addresses", {})
    with pytest.raises(LiveSmokeHalted):
        await cart.call_tool("view_cart", {})
    assert len(inner.calls) == 1


# ---- 429 stop behaviour -----------------------------------------------------


async def test_first_429_halts_every_later_call_without_retrying() -> None:
    inner = FakeClient(SwiggyRateLimited("Swiggy is rate-limiting", 42))
    tripwire = RateLimitTripwire()
    client = guarded(inner, provider="swiggy", tripwire=tripwire)

    with pytest.raises(LiveSmokeHalted) as first:
        await client.call_tool("get_addresses", {"page": 1, "pageSize": 10})
    with pytest.raises(LiveSmokeHalted):
        await client.call_tool("search_products", {"query": "atta"})
    with pytest.raises(LiveSmokeHalted):
        await client.list_tools()

    assert len(inner.calls) == 1
    assert inner.list_calls == 0
    assert tripwire.trip is not None
    assert tripwire.trip.retry_after_seconds == 42
    assert "Retry-After 42s" in str(first.value)
    assert "swiggy" in tripwire.halt_message()


async def test_zepto_429_reports_missing_retry_after() -> None:
    tripwire = RateLimitTripwire()
    client = guarded(FakeClient(ZeptoRateLimited("429")), tripwire=tripwire)

    with pytest.raises(LiveSmokeHalted):
        await client.call_tool("list_saved_addresses", {})

    assert tripwire.trip is not None and tripwire.trip.retry_after_seconds is None
    assert "Retry-After not provided" in tripwire.halt_message()


def test_tripped_wire_exits_the_whole_session() -> None:
    tripwire = RateLimitTripwire()
    tripwire.stop_session_if_tripped()  # untripped: no-op

    tripwire.record_cooldown("swiggy", 120)

    with pytest.raises(pytest.exit.Exception) as exited:
        tripwire.stop_session_if_tripped()
    assert "Retry-After 120s" in str(exited.value)
    assert exited.value.returncode == 3


async def test_non_rate_limit_errors_do_not_trip_the_wire() -> None:
    tripwire = RateLimitTripwire()
    client = guarded(FakeClient(RuntimeError("boom")), tripwire=tripwire)

    with pytest.raises(RuntimeError):
        await client.call_tool("list_saved_addresses", {})

    assert tripwire.trip is None


# ---- content-free reporter --------------------------------------------------

PII_PAYLOAD = {
    "data": {
        "addresses": [
            {
                "id": "addr_8f3a9c2e11",
                "addressLine": "221B Baker Street, Koramangala 560102",
                "name": "Ravi Kumar",
                "phone": "+91 9876543210",
                "email": "ravi.kumar@example.com",
                "latitude": 12.9716,
                "longitude": 77.5946,
            }
        ],
        "byPhone": {"9876543210": {"name": "Sunita Sharma"}},
        "ravi.kumar@example.com": True,
        "Ravi Kumar": "owner",
        "products": [
            {"name": "Aashirvaad Atta 5kg", "price": 28950, "mrp": 31999.5},
        ],
        "orderTotal": 4999,
    }
}
PII_STRINGS = [
    "addr_8f3a9c2e11",
    "221B",
    "Baker",
    "560102",
    "Ravi",
    "Kumar",
    "9876543210",
    "example.com",
    "12.9716",
    "77.5946",
    "Sunita",
    "Aashirvaad",
    "28950",
    "31999",
    "4999",
    "owner",
]


def test_shape_report_never_emits_values_or_value_like_keys(tmp_path: Path) -> None:
    recorder = SmokeRecorder()
    recorder.record_response("zepto", "list_saved_addresses", PII_PAYLOAD)
    recorder.record_check("zepto", "addresses", address_count=1, has_default=True)

    path = recorder.write(directory=tmp_path)
    written = path.read_text()
    latest = (tmp_path / "latest.json").read_text()

    assert written == latest
    for secret in PII_STRINGS:
        assert secret not in written, secret
    shape = json.loads(written)["responses"][0]["shape"]
    assert shape["data.addresses[].phone"]["types"] == ["string"]
    assert shape["data.addresses[].latitude"]["types"] == ["number"]
    assert shape["data.addresses"]["max_len"] == 1
    assert "data.byPhone.<key>.name" in shape
    assert "data.<key>" in shape


def test_shape_of_records_types_and_counts_only() -> None:
    shape = shape_of({"items": [{"a": 1}, {"a": None}, {"b": "x"}], "ok": True})

    assert shape["items"] == {"types": ["array"], "seen": 1, "min_len": 3, "max_len": 3}
    assert shape["items[].a"] == {"types": ["integer", "null"], "seen": 2}
    assert shape["ok"]["types"] == ["boolean"]


@pytest.mark.parametrize(
    ("key", "kept"),
    [
        ("addressLine", True),
        ("line1", True),
        ("pageSize", True),
        ("9876543210", False),
        ("user@example.com", False),
        ("Ravi Kumar", False),
        ("order_12345678", False),
        ("x" * 60, False),
    ],
)
def test_safe_key(key: str, kept: bool) -> None:
    assert (safe_key(key) == key) is kept


def test_recorder_rejects_non_scalar_facts() -> None:
    with pytest.raises(TypeError):
        SmokeRecorder().record_check("zepto", "x", value=object())


# ---- catalogue comparison ---------------------------------------------------


def _tool(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "input_schema": schema}


def test_catalogue_diff_ignores_descriptions_but_not_structure() -> None:
    base = {
        "type": "object",
        "description": "old",
        "properties": {
            "description": {"type": "string", "description": "a field literally named description"},
            "q": {"type": "string"},
        },
        "required": ["q"],
    }
    reworded = json.loads(json.dumps(base))
    reworded["description"] = "new words"
    restructured = json.loads(json.dumps(base))
    restructured["required"] = ["q", "description"]

    diff = compare_catalogues(
        [_tool("a", base), _tool("b", base), _tool("gone", base)],
        [_tool("a", reworded), _tool("b", restructured), _tool("new", base)],
    )

    assert diff == {
        "added": ["new"],
        "removed": ["gone"],
        "schema_changed": ["b"],
        "description_only_changed": ["a"],
    }
    assert "description" in structural_schema(base)["properties"]  # type: ignore[index]


def test_committed_zepto_snapshot_compares_cleanly_with_itself() -> None:
    entries = json.loads((FIXTURE_DIR / "zepto_tools_catalogue_2026-11-21.json").read_text())
    diff = compare_catalogues(entries, entries)
    assert not any(diff.values())


async def test_production_prepaid_gateway_is_unusable_through_the_guard() -> None:
    """Even with a contract and the flag on, the guard stops every real payment call."""

    from tests.payment_fakes import payment_gateway

    inner = FakeClient()
    client = guarded(inner, tier="cart", env=CART)
    gateway = payment_gateway(client)  # type: ignore[arg-type]
    for method in ("online", "upi_reserve", "wallet"):
        with pytest.raises(ForbiddenLiveCall):
            await gateway.start_unlocked(zepto_basket(), method, 100)  # type: ignore[arg-type]
    with pytest.raises(ForbiddenLiveCall):
        await gateway.check_status("ZP-1", poll=False)
    assert inner.calls == []
