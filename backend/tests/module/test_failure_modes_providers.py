"""Failure modes at the store boundary: search, addresses, bills (docs/FAILURE_MODES.md).

Every test injects one fault with tests/fault_injection.py and asserts what the
catalogue row says must (and must not) happen. Search failures use the
``provider_error`` contract (prompt 1.23.0): a ``reason`` of ``unavailable``,
``rate_limited``, ``session_expired`` or ``address_invalid``, and
``unreachable_stores`` next to partial results. Rows that still fail on main are
strict xfails naming the row and the branch that fixes them.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyProviderError,
    SwiggyRateLimited,
)
from app.commerce.zepto.client import ZeptoMcpClient, _SessionState
from app.commerce.zepto.errors import (
    ZeptoAuthenticationRequired,
    ZeptoProviderError,
    ZeptoRateLimited,
    ZeptoSchemaError,
)
from app.commerce.zepto.quote_provider import ZeptoQuoteProvider
from app.commerce.zepto.service import ZeptoService
from app.core.config import Settings
from app.shopping.errors import ApprovalRefused
from app.shopping.models import ApprovalStatus, BasketStatus
from app.voice.tools import MultiProviderShoppingToolExecutor, ProviderSearchTarget
from tests.fault_injection import (
    HOUSEHOLD,
    ZEPTO_ADDRESS,
    FaultWorld,
    FaultyMcpClient,
    FaultySearcher,
    Payload,
    Raise,
    Slow,
    call,
    product,
    transport_error,
    zepto_preview,
)

pytestmark = pytest.mark.failure_mode

ERROR_STATUSES = {"provider_error"}


def zepto_only(world: FaultWorld, zepto: object) -> object:
    """A session where only Zepto has a delivery address (Swiggy not ready)."""

    return world.toolbox(zepto=zepto, swiggy_address=None)


def assert_error_not_empty_results(output: dict, event: dict | None) -> None:
    """The row's core rule: a store failure is never shown as 'no results'."""

    assert output["status"] in ERROR_STATUSES, output
    assert "results" not in output
    assert event is not None and event["type"] == "tool.error"
    assert "products" not in event
    assert "never say the item was not found" in output["message"]


# ---------------------------------------------------------------- FM-P01 / P11


@pytest.mark.parametrize(
    "error",
    [
        ZeptoProviderError("Zepto MCP request failed."),
        ZeptoSchemaError("The authenticated Zepto catalogue does not expose search_products."),
    ],
    ids=["provider_down", "tool_missing"],
)
async def test_fm_p01_zepto_down_search_is_an_error_never_empty_results(error: Exception) -> None:
    world = FaultWorld()
    zepto = FaultySearcher(None, Raise(error))
    toolbox = zepto_only(world, zepto)

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert_error_not_empty_results(output, event)
    assert zepto.queries == ["Amul Taaza milk"]  # one call, no retry loop in the tool
    assert await world.snapshot() == (0, [], "draft")


async def test_fm_p01_swiggy_down_search_is_an_error_never_empty_results() -> None:
    world = FaultWorld()
    swiggy = FaultySearcher(None, Raise(SwiggyProviderError("Swiggy MCP request failed.")))
    toolbox = world.toolbox(swiggy=swiggy, zepto_address=None)

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert_error_not_empty_results(output, event)


async def test_fm_p01_zepto_service_surfaces_a_provider_error_not_zero_products() -> None:
    client = FaultyMcpClient({"search_products": [Raise(ZeptoProviderError("Zepto could not complete the request."))]})
    zepto = ZeptoService(object(), client)  # type: ignore[arg-type]

    with pytest.raises(ZeptoProviderError):
        await zepto.search("Amul Taaza milk", ZEPTO_ADDRESS)


async def test_fm_p01_provider_error_names_the_store_and_reason() -> None:
    world = FaultWorld()
    toolbox = zepto_only(world, FaultySearcher(None, Raise(ZeptoProviderError("down"))))

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert output["status"] == "provider_error"
    assert output["unreachable_stores"] == [{"store": "Zepto", "store_id": "zepto", "reason": "unavailable"}]
    assert "never say the item was not found" in output["message"]
    assert event is not None and event["message"] == "Zepto isn't responding—try again."


# ---------------------------------------------------------------- FM-P02


async def test_fm_p02_one_store_down_still_shows_the_other_stores_results() -> None:
    world = FaultWorld()
    toolbox = world.toolbox(zepto=FaultySearcher(None, Raise(ZeptoProviderError("down"))))

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert output["status"] == "ok"
    assert {result["store_id"] for result in output["results"]} == {"swiggy_instamart"}
    zepto_status = next(entry for entry in output["provider_statuses"] if entry["store"] == "Zepto")
    assert zepto_status["status"] in ERROR_STATUSES
    assert event is not None and event["type"] == "tool.results"
    assert {item["provider"] for item in event["products"]} == {"swiggy_instamart"}


async def test_fm_p02_partial_results_carry_an_unreachable_notice() -> None:
    world = FaultWorld()
    toolbox = world.toolbox(zepto=FaultySearcher(None, Raise(ZeptoProviderError("down"))))

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert output["unreachable_stores"][0]["store_id"] == "zepto"
    assert event is not None and event["notice"] == "Zepto isn't responding—try again."


# ---------------------------------------------------------------- FM-P03


@pytest.mark.xfail(
    strict=True,
    reason="FM-P03: no per-store search timeout on main; a hung store blocks the whole search "
    "(voice: until the user gives up; typed: until the 60 s turn timeout). Per-store soft/hard "
    "timeouts are on codex/turn-latency.",
)
async def test_fm_p03_a_hung_store_does_not_block_the_other_stores_results() -> None:
    swiggy = FaultySearcher([product("Amul Taaza Milk", "500 ml", 28, prefix="s")])
    zepto = FaultySearcher(None, Slow(30))
    executor = MultiProviderShoppingToolExecutor(  # type: ignore[call-arg]
        [
            ProviderSearchTarget("swiggy_instamart", "Swiggy Instamart", swiggy, "s-home", True),
            ProviderSearchTarget("zepto", "Zepto", zepto, ZEPTO_ADDRESS, True),
        ],
        provider_timeout=0.05,
        provider_hard_timeout=0.2,
    )

    outcome = await asyncio.wait_for(
        executor.execute("search_products", json.dumps({"query": "Amul Taaza milk"})), 2
    )

    assert outcome.model_output["status"] == "ok"
    assert {r["store_id"] for r in outcome.model_output["results"]} == {"swiggy_instamart"}


# ---------------------------------------------------------------- FM-P04 / P05


async def test_fm_p04_zepto_rate_limit_is_reported_and_not_retried() -> None:  # reason rate_limited
    world = FaultWorld()
    zepto = FaultySearcher(None, Raise(ZeptoRateLimited("Zepto is temporarily rate-limiting this connection.")))
    toolbox = zepto_only(world, zepto)

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert_error_not_empty_results(output, event)
    assert output["unreachable_stores"][0]["reason"] == "rate_limited"
    assert len(zepto.queries) == 1


async def test_fm_p04_zepto_service_never_retries_a_rate_limited_search() -> None:
    client = FaultyMcpClient({"search_products": [Raise(ZeptoRateLimited("rate limited"))]})
    zepto = ZeptoService(object(), client)  # type: ignore[arg-type]

    with pytest.raises(ZeptoRateLimited):
        await zepto.search("Amul Taaza milk", ZEPTO_ADDRESS)

    assert client.count("search_products") == 1
    assert client.resets == 0


async def test_fm_p05_swiggy_rate_limited_for_hours_keeps_zepto_results_and_reports_retry_after() -> None:
    world = FaultWorld()
    swiggy = FaultySearcher(None, Raise(SwiggyRateLimited("Swiggy is rate-limiting.", retry_after_seconds=3 * 3600)))
    toolbox = world.toolbox(swiggy=swiggy)

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert output["status"] == "ok"
    assert {result["store_id"] for result in output["results"]} == {"zepto"}
    swiggy_status = next(entry for entry in output["provider_statuses"] if entry["store"] == "Swiggy Instamart")
    assert swiggy_status["status"] == "provider_error" and swiggy_status["reason"] == "rate_limited"
    assert swiggy_status["retry_after_seconds"] == 3 * 3600
    assert event is not None and event["type"] == "tool.results"


async def test_fm_p05_swiggy_only_rate_limited_is_an_error_not_empty_results() -> None:
    world = FaultWorld()
    swiggy = FaultySearcher(None, Raise(SwiggyRateLimited("Swiggy is rate-limiting.", retry_after_seconds=600)))
    toolbox = world.toolbox(swiggy=swiggy, zepto_address=None)

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert_error_not_empty_results(output, event)


# ---------------------------------------------------------------- FM-P06 / P07


@pytest.mark.parametrize("store", ["swiggy", "zepto"])
async def test_fm_p06_expired_token_asks_to_reconnect_on_screen(store: str) -> None:
    world = FaultWorld()
    if store == "zepto":
        toolbox = zepto_only(world, FaultySearcher(None, Raise(ZeptoAuthenticationRequired("Connect Zepto again."))))
    else:
        toolbox = world.toolbox(
            swiggy=FaultySearcher(None, Raise(SwiggyAuthenticationRequired("Connect Swiggy again."))),
            zepto_address=None,
        )

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert output["status"] == "not_connected"
    assert event is not None and event["type"] == "tool.not_connected"
    assert "results" not in output


class RefreshRejectedOAuth:
    def __init__(self) -> None:
        self.refreshes = 0

    async def access_token(self) -> str:
        return "expired-token"

    async def refresh(self) -> str:
        self.refreshes += 1
        return "refreshed-token"


class AlwaysUnauthorizedClient(ZeptoMcpClient):
    def __init__(self) -> None:
        super().__init__(Settings(_env_file=None), RefreshRejectedOAuth())  # type: ignore[arg-type]
        self.runs = 0

    async def _run_session(self, access_token, operation):  # type: ignore[no-untyped-def]
        self.runs += 1
        raise transport_error("http_401")

    async def _open_session(self, access_token: str) -> _SessionState:  # pragma: no cover - not reached
        raise AssertionError("no real session")


async def test_fm_p07_refresh_rejected_stops_after_one_refresh_and_never_loops() -> None:
    client = AlwaysUnauthorizedClient()

    with pytest.raises(ZeptoProviderError):
        await client.list_tools()

    assert client.runs == 2  # the call, then exactly one retry after one refresh
    assert client.oauth.refreshes == 1  # type: ignore[attr-defined]


async def test_fm_p07_rejected_refresh_is_session_expired() -> None:
    client = AlwaysUnauthorizedClient()

    with pytest.raises(ZeptoProviderError) as raised:
        await client.list_tools()

    assert getattr(raised.value, "reason", None) == "session_expired"


# ---------------------------------------------------------------- FM-P08


class RecordingOAuthService:
    def __init__(self) -> None:
        self.started = 0

    async def authorization_url(self) -> str:
        self.started += 1
        return "https://provider.example.test/authorize"


@pytest.mark.parametrize("store", ["swiggy", "zepto"])
def test_fm_p08_oauth_never_starts_when_the_callback_is_unreachable(
    store: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import swiggy as swiggy_api
    from app.api import zepto as zepto_api
    from app.dependencies import get_swiggy_service, get_zepto_service
    from app.main import app

    async def not_ready(*args: object, **kwargs: object) -> bool:
        return False

    module, dependency = (zepto_api, get_zepto_service) if store == "zepto" else (swiggy_api, get_swiggy_service)
    monkeypatch.setattr(module, "local_callback_is_ready", not_ready)
    service = RecordingOAuthService()
    app.dependency_overrides[dependency] = lambda: service
    try:
        response = TestClient(app).get(f"/api/{store}/oauth/start", follow_redirects=False)
    finally:
        app.dependency_overrides.pop(dependency, None)

    assert response.status_code == 503
    assert "callback" in response.json()["detail"]
    assert service.started == 0


# ---------------------------------------------------------------- FM-P09


async def test_fm_p09_stale_session_is_reset_and_the_read_retried_once() -> None:
    client = FaultyMcpClient({"search_products": [Raise(ZeptoProviderError("Zepto could not complete the request.")), None]})
    zepto = ZeptoService(object(), client)  # type: ignore[arg-type]

    result = await zepto.search("Amul Taaza milk", ZEPTO_ADDRESS)

    assert [item.name for item in result.products] == ["Amul Taaza Milk"]
    assert client.resets == 1
    assert client.count("search_products") == 2


async def test_fm_p09_write_tools_are_never_replayed_after_a_session_failure() -> None:
    world = FaultWorld()
    basket = await world.fill()
    client = FaultyMcpClient({"update_cart": [Raise(ZeptoProviderError("The Zepto connection was interrupted."))]})
    provider = ZeptoQuoteProvider(client)

    with pytest.raises(Exception):
        await provider.quote(basket)

    assert client.count("update_cart") == 1
    assert client.count("create_order") == 0
    assert all(retry is False for name, _, retry in client.calls if name in {"update_cart", "create_order"})


# ---------------------------------------------------------------- FM-P10


async def test_fm_p10_an_empty_but_wellformed_search_is_zero_results() -> None:
    client = FaultyMcpClient({"search_products": [Payload({"products": []})]})
    zepto = ZeptoService(object(), client)  # type: ignore[arg-type]

    result = await zepto.search("Amul Taaza milk", ZEPTO_ADDRESS)

    assert result.products == []


@pytest.mark.parametrize(
    "payload",
    [
        {"content_text": "Something went wrong. Please try again."},
        {"result": {"status": "SESSION_EXPIRED"}},
        {"products": [{"name": "Amul", "price": "27"}]},
    ],
    ids=["text_envelope", "unknown_envelope", "unrecognised_items"],
)
@pytest.mark.xfail(
    strict=True,
    reason="FM-P10: parse_products returns [] for an unrecognised Zepto search payload, so a "
    "broken response is shown and spoken as 'no results' instead of a provider error.",
)
async def test_fm_p10_unrecognised_search_payload_is_an_error_not_zero_results(payload: dict) -> None:
    world = FaultWorld()
    client = FaultyMcpClient({"search_products": [Payload(payload)]})
    toolbox = zepto_only(world, ZeptoService(object(), client))  # type: ignore[arg-type]

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert_error_not_empty_results(output, event)


# ---------------------------------------------------------------- FM-A01


def deleted_address_client() -> FaultyMcpClient:
    client = FaultyMcpClient({"select_saved_address": [Raise(ZeptoProviderError("Zepto could not complete the request."))]})
    client.defaults["list_saved_addresses"] = {"addresses": [{"id": "zepto-office", "addressLine": "Tower B"}]}
    return client


async def test_fm_a01_deleted_zepto_address_never_returns_results_or_touches_the_basket() -> None:
    world = FaultWorld()
    client = deleted_address_client()
    toolbox = zepto_only(world, ZeptoService(object(), client))  # type: ignore[arg-type]

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert output["status"] in ERROR_STATUSES
    assert event is not None and event["type"] == "tool.error"
    assert client.count("search_products") == 0  # never searched against a missing address
    assert await world.snapshot() == (0, [], "draft")


async def test_fm_a01_deleted_address_is_address_invalid_and_opens_the_picker() -> None:
    world = FaultWorld()
    toolbox = zepto_only(world, ZeptoService(object(), deleted_address_client()))  # type: ignore[arg-type]

    output, event = await call(toolbox, "search_products", query="Amul Taaza milk")

    assert output["status"] == "provider_error"
    assert output["unreachable_stores"][0]["reason"] == "address_invalid"
    assert "list_delivery_addresses" in output["message"]
    assert event is not None and "choose a delivery address again" in event["message"]
    assert event.get("address_removed") == "zepto"


async def test_fm_a01_deleted_address_is_reported_once_and_never_searched_again() -> None:
    client = deleted_address_client()
    zepto = ZeptoService(object(), client)  # type: ignore[arg-type]
    removed: list[str] = []

    async def on_removed(live, address_id: str) -> None:  # type: ignore[no-untyped-def]
        removed.append(address_id)

    zepto.on_address_removed = on_removed
    with pytest.raises(ZeptoProviderError) as first:
        await zepto.search("Amul Taaza milk", ZEPTO_ADDRESS)
    await asyncio.sleep(0)
    calls = len(client.calls)
    with pytest.raises(ZeptoProviderError) as second:
        await zepto.search("Amul Taaza milk", ZEPTO_ADDRESS)

    assert first.value.reason == second.value.reason == "address_invalid"  # type: ignore[attr-defined]
    assert removed == [ZEPTO_ADDRESS]  # selection cleared + bill invalidated by the refresher
    assert len(client.calls) == calls  # no provider call for a known-missing address
    assert client.count("search_products") == 0


def test_fm_a01_deleted_address_is_a_409_on_the_direct_search_route() -> None:
    from fastapi import HTTPException

    from app.api.zepto import _raise_http
    from app.commerce.zepto.errors import ZeptoAddressInvalid

    with pytest.raises(HTTPException) as raised:
        _raise_http(ZeptoAddressInvalid("gone"))
    assert raised.value.status_code == 409
    assert "Choose a delivery address again" in raised.value.detail


# ---------------------------------------------------------------- FM-A03


def zepto_world(client: FaultyMcpClient) -> FaultWorld:
    world = FaultWorld()
    world.quotes = ZeptoQuoteProvider(client)  # type: ignore[assignment]
    return world


async def test_fm_a03_address_not_serviceable_is_refused_and_the_basket_is_kept() -> None:
    client = FaultyMcpClient()
    world = zepto_world(client)
    basket = await world.fill()
    client.script["create_order"] = [Payload(zepto_preview(basket, deliverable=False))]
    before = await world.snapshot()

    output, _ = await call(world.toolbox(), "request_approval", store="zepto")

    assert output["status"] != "awaiting_user_approval"
    assert "not deliverable" in output.get("reason", output.get("message", ""))
    assert await world.snapshot() == before
    assert await world.approval_repo.for_basket(basket.id) == []
    assert [arguments["confirmOrder"] for name, arguments, _ in client.calls if name == "create_order"] == [False]


# ---------------------------------------------------------------- FM-B01 / B09


async def test_fm_b01_out_of_stock_at_bill_time_names_the_item_and_never_removes_it() -> None:
    world = FaultWorld()
    basket = await world.fill()
    world.quotes.unavailable = [basket.lines[0].ref]
    before = await world.snapshot()

    output, _ = await call(world.toolbox(), "request_approval", store="zepto")

    assert output["status"] != "awaiting_user_approval"
    assert "Not available right now: Fortune Sunflower Oil" in output.get("reason", output.get("message", ""))
    assert await world.snapshot() == before  # the user decides what to remove


@pytest.mark.parametrize("fault", ["connection_refused", "http_500", "timeout"])
async def test_fm_b09_store_down_while_billing_creates_no_bill_and_keeps_the_basket(fault: str) -> None:
    world = FaultWorld()
    world.quotes.faults = [Raise(transport_error(fault))]
    basket = await world.fill()
    before = await world.snapshot()

    output, event = await call(world.toolbox(), "request_approval", store="zepto")

    assert output["status"] in {"error", "bill_failed"}
    assert "could not price" in output.get("reason", output.get("message", ""))
    assert event is None
    assert await world.snapshot() == before
    assert await world.approval_repo.for_basket(basket.id) == []


# ---------------------------------------------------------------- FM-B03


def broken(name: str, basket):  # type: ignore[no-untyped-def]
    preview = zepto_preview(basket)
    data = preview["data"]
    if name == "text_envelope":
        return {"content_text": "Your order preview is ready"}
    if name == "not_a_preview":
        data["isPreview"] = False
    if name == "payable_mismatch":
        data["toPayAmount"] += 100
    if name == "rupees_not_paise":
        data["subTotal"] //= 100
        data["toPayAmount"] = data["subTotal"] + data["deliveryFee"]
    if name == "item_quantity_mismatch":
        data["items"][0]["quantity"] = 1
    if name == "missing_items":
        del data["items"]
    return preview


@pytest.mark.parametrize(
    "shape",
    ["text_envelope", "not_a_preview", "payable_mismatch", "rupees_not_paise", "item_quantity_mismatch", "missing_items"],
)
async def test_fm_b03_unverifiable_bill_preview_is_refused_with_its_step_and_no_basket_edit(shape: str) -> None:
    client = FaultyMcpClient()
    world = zepto_world(client)
    basket = await world.fill(quantity=2)
    client.script["create_order"] = [Payload(broken(shape, basket))]
    before = await world.snapshot()

    output, event = await call(world.toolbox(), "request_approval", store="zepto")

    reason = output.get("reason", output.get("message", ""))
    assert output["status"] in {"error", "bill_failed"}
    assert any(text in reason for text in ("Nothing was ordered", "does not exactly match", "different item quantity"))
    assert event is None
    assert await world.snapshot() == before
    assert await world.approval_repo.for_basket(basket.id) == []
    assert all(arguments.get("confirmOrder") is False for name, arguments, _ in client.calls if name == "create_order")


async def test_fm_b03_quantity_two_with_distinct_product_count_is_a_valid_bill() -> None:
    client = FaultyMcpClient()
    world = zepto_world(client)
    basket = await world.fill(quantity=2)
    client.script["create_order"] = [Payload(zepto_preview(basket, totalItems=1))]

    output, _ = await call(world.toolbox(), "request_approval", store="zepto")

    assert output["status"] == "awaiting_user_approval"


async def test_fm_b03_contract_bill_failure_is_bill_failed() -> None:
    world = FaultWorld()
    world.quotes.faults = [Raise(transport_error("http_500"))]
    await world.fill()

    output, _ = await call(world.toolbox(), "request_approval", store="zepto")

    assert output["status"] == "bill_failed"
    assert "Do not change the basket yourself" in output["message"]


# ---------------------------------------------------------------- FM-B05


async def test_fm_b05_an_expired_bill_cannot_be_approved_and_is_reported_expired() -> None:
    world = FaultWorld()
    basket = await world.fill()
    toolbox = world.toolbox()
    output, _ = await call(toolbox, "request_approval", store="zepto")
    assert output["status"] == "awaiting_user_approval"
    approval = (await world.approval_repo.for_basket(basket.id))[0]

    world.clock.advance(minutes=11)
    viewed, _ = await call(world.toolbox(), "view_basket", store="zepto")

    assert viewed["current_bill"]["approval_status"] == "expired"
    with pytest.raises(ApprovalRefused, match="expired"):
        await world.approvals.approve(approval.id, approval.fingerprint, HOUSEHOLD)
    stored = await world.approval_repo.get(approval.id)
    assert stored is not None and stored.status == ApprovalStatus.EXPIRED
    assert world.gateway.calls == []


async def test_fm_b05_order_request_after_expiry_gets_a_fresh_bill_not_the_expired_one() -> None:
    world = FaultWorld()
    await world.fill()
    toolbox = world.toolbox()
    await call(toolbox, "request_approval", store="zepto")
    quotes_before = world.quotes.calls

    world.clock.advance(minutes=11)
    output, event = await call(toolbox, "prepare_order", store="zepto")

    assert output["status"] == "awaiting_tap"
    assert world.quotes.calls == quotes_before + 1
    assert event is not None and "request" in event
    basket = await world.baskets.active(HOUSEHOLD, "zepto")
    assert basket.status == BasketStatus.AWAITING_APPROVAL
