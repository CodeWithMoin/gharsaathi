"""Rider assist settings and call summaries on the migrated PostgreSQL schema."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.rider.service import get_rider_service
from app.rider.store import RiderCallSummary
from tests.integration.helpers import as_user


def test_settings_and_call_summaries_round_trip(api: TestClient) -> None:
    owner = as_user("rider-owner")
    initial = api.get("/api/rider/settings", headers=owner)
    assert initial.status_code == 200, initial.text
    assert initial.json()["enabled"] is False

    saved = api.put(
        "/api/rider/settings",
        headers=owner,
        json={"enabled": True, "notes": {"landmark": "Near the temple", "directions": "Gate 2, flat 402", "handoff": "leave_with_guard", "preferred_language": "hinglish"}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["enabled"] is True
    assert saved.json()["notes"]["landmark"] == "Near the temple"

    store = get_rider_service().store
    summary = RiderCallSummary(
        started_at=datetime.now(UTC), caller="•••• 210", outcome="answered",
        summary="Rider asked for the flat number; told from your notes. otp 4821", rider_said="Gate pe hoon",
        answered_from=["notes"],
    )
    # The app's own event loop (NullPool engine), as the stream handler would.
    assert api.portal is not None
    api.portal.call(store.add_call, "rider-owner", summary)
    calls = api.get("/api/rider/calls", headers=owner).json()["calls"]
    assert len(calls) == 1 and "4821" not in calls[0]["summary"]
    assert api.get("/api/rider/calls", headers=as_user("someone-else")).json()["calls"] == []
    assert api.post(f"/api/rider/calls/{calls[0]['id']}/seen", headers=owner).status_code == 204
    assert api.delete("/api/rider/calls", headers=owner).status_code == 204
    assert api.get("/api/rider/calls", headers=owner).json()["calls"] == []
