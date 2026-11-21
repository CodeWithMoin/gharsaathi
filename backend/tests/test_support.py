from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_support_service
from app.main import app
from app.support.service import SupportService
from app.support.store import ComplaintStore


@pytest.fixture
def support_client(tmp_path: Path):
    service = SupportService(ComplaintStore(tmp_path / "support"))
    app.dependency_overrides[get_support_service] = lambda: service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_support_service, None)


def create_complaint(client: TestClient) -> dict:
    response = client.post(
        "/api/support/complaints",
        json={
            "provider": "swiggy",
            "order_id": "ORDER-123",
            "affected_product": "Amul Taaza milk 1 L",
            "category": "spoiled_product",
            "description": "The milk smelled sour when the sealed pack was opened.",
            "desired_resolution": "replacement",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_prepare_and_approve_exact_revision(support_client: TestClient) -> None:
    created = create_complaint(support_client)
    prepared_response = support_client.post(
        f"/api/support/complaints/{created['id']}/prepare"
    )
    assert prepared_response.status_code == 200
    prepared = prepared_response.json()
    assert prepared["status"] == "awaiting_approval"
    assert "not independently verified" in prepared["prepared_summary"]

    approved_response = support_client.post(
        f"/api/support/complaints/{created['id']}/approve",
        json={"expected_revision": prepared["revision"]},
    )
    assert approved_response.status_code == 200
    approved = approved_response.json()
    assert approved["status"] == "approved_pending_submission"
    assert len(approved["approval"]["complaint_digest"]) == 64


def test_stale_revision_cannot_be_approved(support_client: TestClient) -> None:
    created = create_complaint(support_client)
    prepared = support_client.post(
        f"/api/support/complaints/{created['id']}/prepare"
    ).json()
    response = support_client.post(
        f"/api/support/complaints/{created['id']}/approve",
        json={"expected_revision": prepared["revision"] - 1},
    )
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_new_evidence_invalidates_approval(support_client: TestClient) -> None:
    created = create_complaint(support_client)
    prepared = support_client.post(
        f"/api/support/complaints/{created['id']}/prepare"
    ).json()
    approved = support_client.post(
        f"/api/support/complaints/{created['id']}/approve",
        json={"expected_revision": prepared["revision"]},
    ).json()
    assert approved["approval"] is not None

    png = b"\x89PNG\r\n\x1a\n" + b"test-image-content"
    response = support_client.post(
        f"/api/support/complaints/{created['id']}/evidence",
        files={"evidence": ("damaged-pack.png", png, "image/png")},
    )
    assert response.status_code == 200
    changed = response.json()
    assert changed["approval"] is None
    assert changed["prepared_summary"] is None
    assert changed["status"] == "collecting_evidence"
    assert changed["evidence"][0]["source"] == "user_provided"


def test_rejects_file_disguised_as_image(support_client: TestClient) -> None:
    created = create_complaint(support_client)
    response = support_client.post(
        f"/api/support/complaints/{created['id']}/evidence",
        files={"evidence": ("not-an-image.png", b"plain text", "image/png")},
    )
    assert response.status_code == 415


def test_submit_is_explicitly_blocked_without_authorised_channel(
    support_client: TestClient,
) -> None:
    created = create_complaint(support_client)
    prepared = support_client.post(
        f"/api/support/complaints/{created['id']}/prepare"
    ).json()
    support_client.post(
        f"/api/support/complaints/{created['id']}/approve",
        json={"expected_revision": prepared["revision"]},
    )
    response = support_client.post(
        f"/api/support/complaints/{created['id']}/submit"
    )
    assert response.status_code == 409
    assert "has not been sent" in response.json()["detail"]


def test_agent_tools_prepare_and_approve_without_submission(
    support_client: TestClient,
) -> None:
    catalogue = support_client.get("/api/support/agent/tools")
    assert catalogue.status_code == 200
    names = {tool["name"] for tool in catalogue.json()}
    assert names == {
        "report_order_issue",
        "get_support_complaint",
        "prepare_support_complaint",
        "approve_support_complaint",
    }
    assert "submit_support_complaint" not in names

    report = support_client.post(
        "/api/support/agent/execute",
        json={
            "name": "report_order_issue",
            "arguments": {
                "provider": "swiggy",
                "order_id": "VERIFIED-ORDER-9",
                "affected_product": "Milk 1 L",
                "category": "missing_product",
                "description": "The item was not present in the delivered bag.",
                "desired_resolution": "refund",
            },
        },
    )
    assert report.status_code == 200
    prepared = report.json()
    assert prepared["requires_user_approval"] is True
    assert prepared["submitted"] is False
    assert prepared["complaint"]["status"] == "awaiting_approval"

    approve = support_client.post(
        "/api/support/agent/execute",
        json={
            "name": "approve_support_complaint",
            "arguments": {
                "complaint_id": prepared["complaint"]["id"],
                "expected_revision": prepared["complaint"]["revision"],
            },
        },
    )
    assert approve.status_code == 200
    result = approve.json()
    assert result["requires_user_approval"] is False
    assert result["submitted"] is False
    assert result["complaint"]["status"] == "approved_pending_submission"


def test_agent_tool_rejects_incomplete_extracted_details(
    support_client: TestClient,
) -> None:
    response = support_client.post(
        "/api/support/agent/execute",
        json={
            "name": "report_order_issue",
            "arguments": {"description": "Milk was missing."},
        },
    )
    assert response.status_code == 409
    assert "incomplete" in response.json()["detail"]
