"""Support complaints and order-tracking monitors through the real app lifespan.

Both features persist to files under a per-test tmp directory (configured via
SUPPORT_DATA_PATH / TRACKING_DATA_PATH); tracking uses the demo status adapter.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.integration.helpers import as_user

PNG = b"\x89PNG\r\n\x1a\n" + b"evidence-bytes"


def _complaint(api: TestClient, user: dict[str, str]) -> dict:
    response = api.post(
        "/api/support/complaints",
        headers=user,
        json={
            "provider": "zepto",
            "order_id": "ZEP-1",
            "affected_product": "Amul Taaza milk 1 L",
            "category": "spoiled_product",
            "description": "The sealed pack smelled sour when opened.",
            "desired_resolution": "replacement",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_complaint_lifecycle_ends_in_a_refused_submission(
    api: TestClient, integration_settings: Settings
) -> None:
    alice, bob = as_user("alice"), as_user("bob")
    complaint = _complaint(api, alice)
    complaint_id = complaint["id"]
    assert "user_id" not in complaint

    assert api.get(f"/api/support/complaints/{complaint_id}", headers=bob).status_code == 404
    assert api.post(f"/api/support/complaints/{complaint_id}/prepare", headers=bob).status_code == 404

    mismatched = api.post(
        f"/api/support/complaints/{complaint_id}/evidence",
        headers=alice,
        files={"evidence": ("photo.jpg", PNG, "image/jpeg")},
    )
    assert mismatched.status_code == 415
    with_photo = api.post(
        f"/api/support/complaints/{complaint_id}/evidence",
        headers=alice,
        files={"evidence": ("../../photo.png", PNG, "image/png")},
    )
    assert with_photo.status_code == 200
    assert len(with_photo.json()["evidence"]) == 1
    assert "/" not in with_photo.json()["evidence"][0]["file_name"]

    unapproved = api.post(f"/api/support/complaints/{complaint_id}/submit", headers=alice)
    assert unapproved.status_code == 409

    prepared = api.post(f"/api/support/complaints/{complaint_id}/prepare", headers=alice).json()
    assert prepared["status"] == "awaiting_approval"
    approved = api.post(
        f"/api/support/complaints/{complaint_id}/approve",
        headers=alice,
        json={"expected_revision": prepared["revision"]},
    )
    assert approved.status_code == 200
    assert approved.json()["approval"] is not None

    # Even an approved complaint is never sent: no official channel exists.
    refused = api.post(f"/api/support/complaints/{complaint_id}/submit", headers=alice)
    assert refused.status_code == 409
    after = api.get(f"/api/support/complaints/{complaint_id}", headers=alice).json()
    assert after["status"] == approved.json()["status"]
    assert after["revision"] == approved.json()["revision"]

    # Evidence stays inside the configured data directory.
    support_root = Path(integration_settings.support_data_path)
    stored = [path for path in support_root.rglob("*") if path.is_file()]
    assert stored and all(support_root in path.parents for path in stored)


def test_support_agent_tools_never_submit_and_are_user_scoped(api: TestClient) -> None:
    alice, bob = as_user("alice"), as_user("bob")
    names = {tool["name"] for tool in api.get("/api/support/agent/tools").json()}
    assert "report_order_issue" in names
    assert not any("submit" in name for name in names)

    created = api.post(
        "/api/support/agent/execute",
        headers=alice,
        json={
            "name": "report_order_issue",
            "arguments": {
                "provider": "swiggy",
                "order_id": "SW-9",
                "affected_product": "Eggs, 12 pieces",
                "category": "damaged_product",
                "description": "Three eggs were cracked inside the tray.",
                "desired_resolution": "refund",
            },
        },
    )
    assert created.status_code == 200, created.text
    result = created.json()
    assert result["submitted"] is False
    assert result["requires_user_approval"] is True
    complaint_id = result["complaint"]["id"]

    foreign = api.post(
        "/api/support/agent/execute",
        headers=bob,
        json={"name": "get_support_complaint", "arguments": {"complaint_id": complaint_id}},
    )
    assert foreign.status_code == 404
    unknown_tool = api.post(
        "/api/support/agent/execute",
        headers=alice,
        json={"name": "submit_support_complaint", "arguments": {"complaint_id": complaint_id}},
    )
    assert unknown_tool.status_code == 422


def test_tracking_monitor_lifecycle_is_read_only_and_user_scoped(api: TestClient) -> None:
    alice, bob = as_user("alice"), as_user("bob")
    started = api.post(
        "/api/tracking/monitors",
        headers=alice,
        json={"provider": "zepto", "order_id": "ZEP-1"},
    )
    assert started.status_code == 201, started.text
    read = started.json()
    monitor_id = read["monitor_id"]
    assert read["order"]["status"] == "out_for_delivery"  # demo adapter snapshot
    assert read["order"]["active"] is True

    duplicate = api.post(
        "/api/tracking/monitors", headers=alice, json={"provider": "zepto", "order_id": "ZEP-1"}
    )
    assert duplicate.status_code == 409

    listed = api.get("/api/tracking/monitors", headers=alice).json()
    assert [item["id"] for item in listed] == [monitor_id]
    assert api.get("/api/tracking/monitors", headers=bob).json() == []
    assert api.get(f"/api/tracking/monitors/{monitor_id}", headers=bob).status_code == 404
    assert api.delete(f"/api/tracking/monitors/{monitor_id}", headers=bob).status_code == 404
    assert api.get("/api/tracking/monitors/not-a-uuid", headers=alice).status_code == 404

    # A manual refresh inside the provider's poll interval does not re-query.
    refreshed = api.post(f"/api/tracking/monitors/{monitor_id}/refresh", headers=alice).json()
    assert refreshed["checked_at"] == read["checked_at"]

    stopped = api.delete(f"/api/tracking/monitors/{monitor_id}", headers=alice)
    assert stopped.status_code == 200
    assert stopped.json()["order"]["active"] is False
    assert stopped.json()["order"]["stop_reason"] == "stopped_by_user"
    assert api.get("/api/tracking/monitors", headers=alice, params={"active_only": "true"}).json() == []

    restarted = api.post(
        "/api/tracking/monitors", headers=alice, json={"provider": "zepto", "order_id": "ZEP-1"}
    )
    assert restarted.status_code == 201
    assert restarted.json()["monitor_id"] == monitor_id
