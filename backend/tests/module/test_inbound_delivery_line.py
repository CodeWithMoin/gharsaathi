"""Inbound delivery line end to end: SQLite store, fake address book, mocked Sarvam."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commerce.swiggy.models import Address, AddressList, AddressResolution, Pagination
from app.core.config import Settings
from app.database.base import Base
from app.inbound_delivery import InboundDeliveryService, get_inbound_delivery_service
from app.inbound_delivery.models import DirectionsUpdate, PrepareRequest
from app.inbound_delivery.service import InboundDeliveryError, LineClearingAddressBook
from app.inbound_delivery.store import DeliveryLineStore
from app.main import app
from app.tracking.models import OrderWatch, TrackingProvider, TrackingStatus

OWNER = "owner-1"
HOME = "Flat 402, Lake View Apartments, 12 Test Street, HSR Layout, Bengaluru 560102"
OFFICE = "3rd floor, Prestige Tech Park, Outer Ring Road, Bengaluru"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "commerce_mode": "demo",
        "sarvam_voice_agent_api_key": "voice-agent-key",
        "sarvam_voice_agent_org_id": "org-1",
        "sarvam_voice_agent_workspace_id": "workspace-1",
        "sarvam_inbound_deployment_id": "deployment-1",
        "sarvam_inbound_phone_number": "+918064264240",
        "sarvam_demo_recipient_number": "+919876543210",
        "sarvam_inbound_owner_user_id": OWNER,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeAddressBook:
    """Saved store addresses per user; ``selected`` mirrors PUT /addresses/selected."""

    def __init__(self) -> None:
        self.addresses = {
            "zepto": [Address(id="z-home", tag="Home", address_line=HOME), Address(id="z-office", tag="Office", address_line=OFFICE)],
            "swiggy_instamart": [Address(id="s-home", tag="Home", address_line=HOME)],
        }
        self.selected = {"zepto": "z-home", "swiggy_instamart": "s-home"}

    async def saved(self, household_id: str, provider: str) -> AddressList | None:
        if household_id not in {OWNER, "other-user"}:
            return None
        return AddressList(
            addresses=self.addresses[provider],
            pagination=Pagination(total=len(self.addresses[provider])),
            resolution=AddressResolution(default_address_id=self.selected[provider]),
        )

    async def select(self, household_id: str, provider: str, address_id: str) -> AddressList | None:
        self.selected[provider] = address_id
        return await self.saved(household_id, provider)


class FakeProfiles:
    def __init__(self) -> None:
        self.names = {OWNER: "Moin"}

    async def read(self, user_id: str):
        name = self.names.get(user_id)
        return SimpleNamespace(first_name=name) if name else None


class FakeSarvam:
    """Records every request; answers each stream with the next queued cohort."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.stream_status = "completed"
        self.get_status = "completed"
        self.valid, self.rejected = 1, 0
        self.fail_with: httpx.Response | Exception | None = None
        self.count = 0

    def payload(self, cohort_id: str, status: str) -> dict:
        return {
            "name": "n", "cohort_id": cohort_id, "status": status, "source_type": "file_upload",
            "cohort_category": "cohort", "created_by": "t",
            "created_at": "2026-11-22T00:00:00Z", "updated_at": "2026-11-22T00:00:00Z",
            "result": {"total_records": 1, "valid_records": self.valid, "rejected_records": self.rejected}
            if status == "completed" else None,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            if isinstance(self.fail_with, Exception):
                raise self.fail_with
            return self.fail_with
        if request.method == "POST":
            self.count += 1
            return httpx.Response(200, json=self.payload(f"cohort-{self.count}", self.stream_status))
        return httpx.Response(200, json=self.payload(request.url.path.rsplit("/", 1)[-1], self.get_status))

    def streamed(self) -> list[dict[str, str]]:
        return [json.loads(r.content)["users"][0]["app_variables"] for r in self.requests if r.method == "POST"]


class Pushes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object, str | None]] = []

    async def send(self, user_id, payload, *, dedupe_key=None):
        self.sent.append((user_id, payload, dedupe_key))


async def no_sleep(_: float) -> None:
    return None


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[DeliveryLineStore]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'line.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield DeliveryLineStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


@pytest.fixture
def sarvam() -> FakeSarvam:
    return FakeSarvam()


@pytest.fixture
def book() -> FakeAddressBook:
    return FakeAddressBook()


@pytest.fixture
def pushes() -> Pushes:
    return Pushes()


@pytest_asyncio.fixture
async def make_line(store, sarvam, book, pushes):
    clients: list[httpx.AsyncClient] = []

    def build(**overrides: object) -> InboundDeliveryService:
        client = httpx.AsyncClient(transport=httpx.MockTransport(sarvam))
        clients.append(client)
        return InboundDeliveryService(
            settings(**overrides), store, book, FakeProfiles(), client=client, notifier=pushes, sleep=no_sleep
        )

    yield build
    for client in clients:
        await client.aclose()


@pytest_asyncio.fixture
async def line(make_line) -> InboundDeliveryService:
    return make_line()


def directions(address_id: str = "z-home", provider: str = "zepto", **overrides: object) -> DirectionsUpdate:
    values: dict[str, object] = {
        "provider": provider,
        "address_id": address_id,
        "landmark": "Opposite the public park",
        "route_directions": ["Main road pe Reliance Smart ke baad left", "Doosri gali", "Blue gate", "Lift se 4th floor, flat 402"],
        "gate_note": None,
        "handover_instructions": "Guard ke paas chhod dijiye",
    }
    values.update(overrides)
    return DirectionsUpdate.model_validate(values)


def approve(address_id: str = "z-home", provider: str = "zepto") -> PrepareRequest:
    return PrepareRequest(provider=provider, address_id=address_id, confirmed=True)  # type: ignore[arg-type]


def watch(status: TrackingStatus, provider: TrackingProvider = TrackingProvider.ZEPTO, monitor_id: str = "m1") -> OrderWatch:
    now = datetime.now(UTC)
    return OrderWatch(
        id=monitor_id, user_id=OWNER, provider=provider, order_id="o1", status=status,
        active=status not in {TrackingStatus.DELIVERED, TrackingStatus.CANCELLED},
        created_at=now, updated_at=now,
    )


# Address, name and directions come from the server --------------------------------


@pytest.mark.asyncio
async def test_shares_the_selected_store_address_with_saved_directions(line, sarvam) -> None:
    await line.save_directions(OWNER, directions())
    shared = await line.prepare(OWNER, approve())
    assert shared.phase == "ready" and shared.provider == "zepto" and shared.address_id == "z-home"
    assert sarvam.streamed() == [{
        "caller_authorized": "yes",
        "recipient_name": "Moin",
        "delivery_address": HOME,
        "landmark": "Opposite the public park",
    }]
    user = json.loads(sarvam.requests[0].content)["users"][0]
    assert user["user_identifier"] != OWNER


@pytest.mark.asyncio
async def test_route_directions_and_handover_are_sent_only_with_the_flag(make_line, sarvam) -> None:
    line = make_line(sarvam_route_directions_enabled=True)
    await line.save_directions(OWNER, directions(gate_note="Ring twice"))
    await line.prepare(OWNER, approve())
    variables = sarvam.streamed()[0]
    assert variables["route_directions"] == (
        "1) Main road pe Reliance Smart ke baad left; 2) Doosri gali; 3) Blue gate; "
        "4) Lift se 4th floor, flat 402; 5) Gate: Ring twice"
    )
    assert variables["handover_instructions"] == "Guard ke paas chhod dijiye"


@pytest.mark.asyncio
async def test_directions_are_saved_per_address_and_reused(line, sarvam, book) -> None:
    await line.save_directions(OWNER, directions("z-home"))
    await line.save_directions(OWNER, directions("z-office", landmark="Tech park gate 3", route_directions=["Tower B"], handover_instructions=None))
    await line.prepare(OWNER, approve("z-home"))
    book.selected["zepto"] = "z-office"
    await line.prepare(OWNER, approve("z-office"))
    first, second = sarvam.streamed()
    assert (first["delivery_address"], first["landmark"]) == (HOME, "Opposite the public park")
    assert (second["delivery_address"], second["landmark"]) == (OFFICE, "Tech park gate 3")
    assert (await line.directions(OWNER, "zepto", "z-home")).handover_instructions == "Guard ke paas chhod dijiye"
    assert await line.delete_directions(OWNER, "zepto", "z-home") is True
    assert await line.directions(OWNER, "zepto", "z-home") is None
    assert await line.directions(OWNER, "zepto", "z-office") is not None


@pytest.mark.asyncio
async def test_directions_only_for_the_users_own_saved_addresses(line) -> None:
    with pytest.raises(InboundDeliveryError) as error:
        await line.save_directions(OWNER, directions("someone-elses-address"))
    assert error.value.status_code == 409
    await line.save_directions(OWNER, directions())
    assert await line.directions("other-user", "zepto", "z-home") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup,code",
    [
        ("unselected", "address_changed"),
        ("no_directions", "no_directions"),
        ("no_name", "no_name"),
    ],
)
async def test_refuses_to_share_without_the_selected_address_name_or_directions(make_line, sarvam, book, setup, code) -> None:
    line = make_line()
    if setup != "no_directions":
        await line.save_directions(OWNER, directions("z-office"))
        await line.save_directions(OWNER, directions("z-home"))
    if setup == "no_name":
        line.profiles.names.clear()  # type: ignore[attr-defined]
    with pytest.raises(InboundDeliveryError) as error:
        await line.prepare(OWNER, approve("z-office" if setup == "unselected" else "z-home"))
    assert error.value.code == code
    assert sarvam.requests == []


@pytest.mark.asyncio
async def test_only_the_owner_can_share_or_clear(line, sarvam) -> None:
    with pytest.raises(InboundDeliveryError) as error:
        await line.prepare("other-user", approve())
    assert error.value.status_code == 403
    with pytest.raises(InboundDeliveryError):
        await line.revoke("other-user", "user_cleared")
    assert sarvam.requests == []


# Ready, rejected, processing ------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_needs_one_valid_and_zero_rejected(line, sarvam) -> None:
    await line.save_directions(OWNER, directions())
    sarvam.rejected = 1
    assert (await line.prepare(OWNER, approve())).phase == "rejected"
    sarvam.valid, sarvam.rejected = 0, 1
    assert (await line.prepare(OWNER, approve())).phase == "rejected"


@pytest.mark.asyncio
async def test_status_check_is_scoped_to_the_sharing_user(line, sarvam) -> None:
    await line.save_directions(OWNER, directions())
    sarvam.stream_status = "processing"
    shared = await line.prepare(OWNER, approve())
    assert shared.phase == "processing"
    with pytest.raises(InboundDeliveryError) as error:
        await line.status("other-user", shared.cohort_id or "")
    assert error.value.status_code == 404
    checked = await line.status(OWNER, shared.cohort_id or "")
    assert checked.phase == "ready"
    assert str(sarvam.requests[-1].url).endswith("/deployments/deployment-1/cohorts/cohort-1")


@pytest.mark.asyncio
async def test_timeout_is_remembered_as_unknown_so_it_is_cleared_later(line, sarvam) -> None:
    await line.save_directions(OWNER, directions())
    sarvam.fail_with = httpx.ReadTimeout("slow")
    with pytest.raises(InboundDeliveryError) as error:
        await line.prepare(OWNER, approve())
    assert (error.value.status_code, error.value.code) == (504, "outcome_unknown")
    assert len(sarvam.requests) == 1
    assert (await line.line(OWNER)).phase == "unknown"
    sarvam.fail_with = None
    cleared = await line.revoke(OWNER, "user_cleared")
    assert cleared.phase == "cleared" and sarvam.streamed()[-1]["caller_authorized"] == "no"


# Clearing -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_changing_the_selected_address_clears_the_line(line, sarvam, book, pushes) -> None:
    await line.save_directions(OWNER, directions())
    await line.prepare(OWNER, approve())
    voice_book = LineClearingAddressBook(book, line)
    await voice_book.select(OWNER, "zepto", "z-office")
    await line.drain()
    cleared = await line.line(OWNER)
    assert (cleared.phase, cleared.reason) == ("cleared", "address_changed")
    assert sarvam.streamed()[-1] == {
        "caller_authorized": "no", "recipient_name": "", "delivery_address": "", "landmark": "",
    }
    assert pushes.sent[-1][1].title == "Delivery line cleared"
    assert "Lake View" not in pushes.sent[-1][1].body


@pytest.mark.asyncio
async def test_reselecting_the_same_address_or_another_store_keeps_the_line(line, sarvam) -> None:
    await line.save_directions(OWNER, directions())
    await line.prepare(OWNER, approve())
    await line.address_selection_changed(OWNER, "zepto", "z-home")
    await line.address_selection_changed(OWNER, "swiggy", "s-home")
    await line.drain()
    assert (await line.line(OWNER)).phase == "ready"
    assert len(sarvam.streamed()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(TrackingStatus.DELIVERED, "delivered"), (TrackingStatus.CANCELLED, "cancelled")])
async def test_delivered_or_cancelled_order_clears_the_line(make_line, sarvam, status, reason) -> None:
    line = make_line(sarvam_route_directions_enabled=True)
    await line.save_directions(OWNER, directions())
    await line.prepare(OWNER, approve())
    line.tracking_updated(watch(TrackingStatus.OUT_FOR_DELIVERY))
    await line.drain()
    assert (await line.line(OWNER)).phase == "ready"
    line.tracking_updated(watch(status))
    await line.drain()
    cleared = await line.line(OWNER)
    assert (cleared.phase, cleared.reason) == ("cleared", reason)
    assert sarvam.streamed()[-1] == {
        "caller_authorized": "no", "recipient_name": "", "delivery_address": "", "landmark": "",
        "route_directions": "", "handover_instructions": "",
    }


@pytest.mark.asyncio
async def test_failed_clear_is_shown_not_hidden(line, sarvam, pushes) -> None:
    await line.save_directions(OWNER, directions())
    await line.prepare(OWNER, approve())
    sarvam.fail_with = httpx.Response(500)
    line.tracking_updated(watch(TrackingStatus.DELIVERED))
    await line.drain()
    state = await line.line(OWNER)
    assert state.phase == "clear_failed" and state.error
    assert pushes.sent[-1][1].title == "Delivery line not cleared"


# Automatic sharing ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_share_is_off_by_default(line, sarvam) -> None:
    await line.save_directions(OWNER, directions())
    line.tracking_started(watch(TrackingStatus.CONFIRMED))
    await line.drain()
    assert sarvam.requests == [] and (await line.line(OWNER)).auto_share is False


@pytest.mark.asyncio
async def test_auto_share_on_tracking_start_notifies_and_clears_on_delivery(line, sarvam, pushes) -> None:
    from app.inbound_delivery.models import LineSettingsUpdate

    await line.save_directions(OWNER, directions())
    await line.update_settings(OWNER, LineSettingsUpdate(auto_share=True, confirmed=True))
    sarvam.stream_status = "processing"
    line.tracking_started(watch(TrackingStatus.CONFIRMED, monitor_id="m7"))
    await line.drain()
    state = await line.line(OWNER)
    assert state.phase == "ready" and state.address_id == "z-home"
    assert sarvam.streamed()[0]["delivery_address"] == HOME
    assert pushes.sent[-1][1].title == "Delivery line ready"
    # Another order finishing does not clear this one's line.
    line.tracking_updated(watch(TrackingStatus.DELIVERED, monitor_id="other"))
    await line.drain()
    assert (await line.line(OWNER)).phase == "ready"
    line.tracking_updated(watch(TrackingStatus.DELIVERED, monitor_id="m7"))
    await line.drain()
    assert (await line.line(OWNER)).phase == "cleared"


@pytest.mark.asyncio
async def test_auto_share_without_directions_tells_the_user_and_sends_nothing(line, sarvam, pushes) -> None:
    from app.inbound_delivery.models import LineSettingsUpdate

    await line.update_settings(OWNER, LineSettingsUpdate(auto_share=True, confirmed=True))
    line.tracking_started(watch(TrackingStatus.CONFIRMED))
    await line.drain()
    assert sarvam.requests == []
    assert pushes.sent[-1][1].title == "Delivery line not shared"


# HTTP routes ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(line) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_inbound_delivery_service] = lambda: line
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_inbound_delivery_service, None)


def as_user(user_id: str) -> dict[str, str]:
    return {"X-Test-User-Id": user_id}


@pytest.mark.asyncio
async def test_routes_share_with_strict_approval_and_return_coded_errors(client, sarvam) -> None:
    saved = await client.put("/api/inbound-delivery/directions", headers=as_user(OWNER), json=directions().model_dump())
    assert saved.status_code == 200
    reread = await client.get("/api/inbound-delivery/directions", headers=as_user(OWNER), params={"provider": "zepto", "address_id": "z-home"})
    assert reread.json()["landmark"] == "Opposite the public park"

    for confirmed in (1, "true", False):
        refused = await client.post("/api/inbound-delivery/contexts", headers=as_user(OWNER), json={"provider": "zepto", "address_id": "z-home", "confirmed": confirmed})
        assert refused.status_code == 422
    assert sarvam.requests == []

    sarvam.fail_with = httpx.Response(403)
    denied = await client.post("/api/inbound-delivery/contexts", headers=as_user(OWNER), json={"provider": "zepto", "address_id": "z-home", "confirmed": True})
    assert denied.status_code == 503 and denied.json()["code"] == "sarvam_not_authorised"
    sarvam.fail_with = None

    shared = await client.post("/api/inbound-delivery/contexts", headers=as_user(OWNER), json={"provider": "zepto", "address_id": "z-home", "confirmed": True})
    assert shared.status_code == 200 and shared.json()["phase"] == "ready"
    assert shared.json()["inbound_phone_number"] == "+918064264240"
    assert "9876543210" not in shared.text

    config = (await client.get("/api/inbound-delivery/config", headers=as_user(OWNER))).json()
    assert config["approved_caller_hint"] == "•••• 3210" and "9876543210" not in json.dumps(config)

    cleared = await client.delete("/api/inbound-delivery/line", headers=as_user(OWNER))
    assert cleared.json()["phase"] == "cleared" and cleared.json()["reason"] == "user_cleared"

    intruder = await client.post("/api/inbound-delivery/contexts", headers=as_user("other-user"), json={"provider": "zepto", "address_id": "z-home", "confirmed": True})
    assert intruder.status_code == 403 and intruder.json()["code"] == "not_owner"
