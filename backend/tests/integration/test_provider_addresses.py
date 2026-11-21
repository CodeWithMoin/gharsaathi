"""Provider address snapshots and selection, persisted in PostgreSQL.

The provider services are local fakes that are *not* the demo classes, so the
routes take the live-account path (snapshot cache) without any network call.
"""

from fastapi.testclient import TestClient

from app.commerce.swiggy.models import Address, AddressList, AddressResolution, Pagination
from app.dependencies import get_swiggy_service, get_zepto_service
from app.main import app
from tests.integration.helpers import as_user


def _addresses(*ids: str, default: str | None) -> AddressList:
    return AddressList(
        addresses=[
            Address(id=item, tag=item.title(), category="HOME", address_line=f"{item} street")
            for item in ids
        ],
        pagination=Pagination(page=1, page_size=10, total=len(ids), total_pages=1),
        resolution=AddressResolution(default_address_id=default, resolution_source="provider"),
    )


class FakeSwiggyAddresses:
    def __init__(self) -> None:
        self.calls = 0
        self.next = _addresses("home", "office", default="home")

    async def addresses(self, page: int, page_size: int) -> AddressList:
        self.calls += 1
        return self.next


class FakeZeptoAddresses:
    def __init__(self) -> None:
        self.calls = 0

    async def addresses(self) -> AddressList:
        self.calls += 1
        return _addresses("zepto-flat", default="zepto-flat")


def test_address_snapshot_refresh_selection_and_isolation(api: TestClient, override) -> None:
    swiggy, zepto = FakeSwiggyAddresses(), FakeZeptoAddresses()
    override(get_swiggy_service, lambda: swiggy)
    override(get_zepto_service, lambda: zepto)
    alice = as_user("alice")

    # No snapshot yet: the route asks for an explicit refresh instead of calling the provider.
    empty = api.get("/api/swiggy/addresses", headers=alice).json()
    assert empty["needs_refresh"] is True and empty["addresses"] == []
    assert swiggy.calls == 0
    unknown = api.put("/api/swiggy/addresses/selected", headers=alice, json={"address_id": "home"})
    assert unknown.status_code == 409

    refreshed = api.get("/api/swiggy/addresses", headers=alice, params={"refresh": "true"}).json()
    assert swiggy.calls == 1
    assert [item["id"] for item in refreshed["addresses"]] == ["home", "office"]
    assert refreshed["resolution"]["default_address_id"] == "home"

    selected = api.put("/api/swiggy/addresses/selected", headers=alice, json={"address_id": " office "})
    assert selected.status_code == 200
    assert selected.json()["resolution"] == {
        **selected.json()["resolution"],
        "default_address_id": "office",
        "resolution_source": "saved_selection",
    }
    assert (
        api.put("/api/swiggy/addresses/selected", headers=alice, json={"address_id": "elsewhere"}).status_code
        == 409
    )

    cached = api.get("/api/swiggy/addresses", headers=alice).json()
    assert swiggy.calls == 1  # served from the PostgreSQL snapshot
    assert cached["cached"] is True and cached["needs_refresh"] is False
    assert cached["resolution"]["default_address_id"] == "office"

    # The selected address disappears from the account: fall back to the provider default.
    swiggy.next = _addresses("home", "parents", default="home")
    after = api.get("/api/swiggy/addresses", headers=alice, params={"refresh": "true"}).json()
    assert [item["id"] for item in after["addresses"]] == ["home", "parents"]
    assert after["resolution"]["default_address_id"] == "home"

    # Snapshots are per user and per provider.
    bob = api.get("/api/swiggy/addresses", headers=as_user("bob")).json()
    assert bob["needs_refresh"] is True and bob["addresses"] == []
    zepto_empty = api.get("/api/zepto/addresses", headers=alice).json()
    assert zepto_empty["needs_refresh"] is True
    assert zepto.calls == 0
    zepto_list = api.get("/api/zepto/addresses", headers=alice, params={"refresh": "true"}).json()
    assert [item["id"] for item in zepto_list["addresses"]] == ["zepto-flat"]
    assert (
        api.put("/api/zepto/addresses/selected", headers=alice, json={"address_id": "home"}).status_code
        == 409
    )
    assert api.get("/api/swiggy/addresses", headers=alice).json()["resolution"]["default_address_id"] == "home"


class DemoLeakingSwiggy(FakeSwiggyAddresses):
    def __init__(self) -> None:
        super().__init__()
        self.next = _addresses("demo-home", default="demo-home")


def test_demo_addresses_never_become_live_account_state(api: TestClient, override) -> None:
    override(get_swiggy_service, DemoLeakingSwiggy)
    alice = as_user("alice")
    unguarded = TestClient(app, raise_server_exceptions=False)
    refused = unguarded.get("/api/swiggy/addresses", headers=alice, params={"refresh": "true"})
    assert refused.status_code == 500  # ProviderAddressRepository refuses demo IDs
    assert api.get("/api/swiggy/addresses", headers=alice).json()["needs_refresh"] is True


def test_demo_mode_serves_fixture_addresses_without_persisting(api: TestClient) -> None:
    alice = as_user("alice")
    demo = api.get("/api/zepto/addresses", headers=alice).json()
    assert [item["id"] for item in demo["addresses"]] == ["demo-home"]
    assert (
        api.put("/api/zepto/addresses/selected", headers=alice, json={"address_id": "demo-home"}).status_code
        == 409
    )
