"""Consequential actions need current-version legal consent, checked by the server."""

import pytest
from fastapi.testclient import TestClient

from app.legal_consent import LEGAL_CONSENT_REQUIRED
from tests.integration.helpers import as_user

pytestmark = pytest.mark.usefixtures("enforce_legal_consent")

ZEPTO = {"provider": "zepto"}
LINE = {
    "ref": {"provider": "zepto", "product_id": "atta", "variant_id": "atta"},
    "name": "Atta",
    "unit_price_paise": 1000,
}
APPROVAL = "00000000000000000000000000000000"
FINGERPRINT = {"fingerprint": "0" * 64}

GATED = [
    ("POST", "/api/basket/lines", {"params": ZEPTO, "json": LINE}),
    ("PATCH", "/api/basket/lines/atta", {"params": ZEPTO, "json": {"quantity": 2}}),
    ("DELETE", "/api/basket/lines/atta", {"params": ZEPTO}),
    ("POST", "/api/basket/address", {"params": ZEPTO, "json": {"address_id": "demo-home"}}),
    ("POST", "/api/basket/approval", {"params": ZEPTO}),
    ("POST", f"/api/basket/approval/{APPROVAL}/approve", {"json": FINGERPRINT}),
    (
        "POST",
        "/api/zepto/checkout/cod",
        {"json": {"approval_id": APPROVAL, **FINGERPRINT}, "headers": {"Idempotency-Key": "k"}},
    ),
    ("POST", "/api/memory/purchase-history/sync", {"params": {"provider": "zepto"}}),
    ("POST", "/api/memory/purchase-history/auto-sync/run", {"params": {"provider": "zepto"}}),
    ("POST", "/api/support/complaints/c1/prepare", {}),
    ("POST", "/api/support/complaints/c1/approve", {"json": {"expected_revision": 1}}),
    ("POST", "/api/support/complaints/c1/submit", {}),
    (
        "POST",
        "/api/support/agent/execute",
        {"json": {"name": "prepare_support_complaint", "arguments": {"complaint_id": "c1"}}},
    ),
    ("POST", "/api/voice/session", {"json": {"sdp": "v=0\r\nm=audio 9 UDP 0\r\n"}}),
    ("POST", "/api/chat/turn", {"json": {"message": "Fortune tel add karo"}}),
    (
        "POST",
        "/api/inbound-delivery/contexts",
        {"json": {"provider": "zepto", "address_id": "demo-home", "confirmed": True}},
    ),
    ("PUT", "/api/inbound-delivery/line/settings", {"json": {"auto_share": True, "confirmed": True}}),
]


def _call(api: TestClient, user: dict[str, str], method: str, path: str, options: dict):
    options = dict(options)
    headers = {**user, **options.pop("headers", {})}
    return api.request(method, path, headers=headers, **options)


@pytest.mark.parametrize(("method", "path", "options"), GATED, ids=[f"{m} {p}" for m, p, _ in GATED])
def test_consequential_action_without_consent_is_refused(
    api: TestClient, method: str, path: str, options: dict
) -> None:
    response = _call(api, as_user("no-consent"), method, path, options)

    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Accept the current Terms and Privacy Policy before continuing.",
        "code": LEGAL_CONSENT_REQUIRED,
    }


def test_refusal_changes_nothing_and_reads_stay_available(api: TestClient) -> None:
    alice = as_user("alice")
    assert api.post("/api/basket/lines", headers=alice, params=ZEPTO, json=LINE).status_code == 403

    basket = api.get("/api/basket", headers=alice, params=ZEPTO)
    assert basket.status_code == 200
    assert basket.json()["lines"] == []
    assert api.get("/api/legal-consent", headers=alice).status_code == 200


def test_accepting_consent_unlocks_actions_for_that_user_only(api: TestClient) -> None:
    alice, bob = as_user("alice"), as_user("bob")
    accepted = api.post(
        "/api/legal-consent", headers=alice, json={"terms_accepted": True, "personalization_allowed": False}
    )
    assert accepted.status_code == 201

    added = api.post("/api/basket/lines", headers=alice, params=ZEPTO, json=LINE)
    assert added.status_code == 200, added.text
    assert [line["name"] for line in added.json()["lines"]] == ["Atta"]
    assert api.post("/api/basket/lines", headers=bob, params=ZEPTO, json=LINE).status_code == 403


def test_read_only_support_agent_tool_does_not_need_consent(api: TestClient) -> None:
    response = api.post(
        "/api/support/agent/execute",
        headers=as_user("no-consent"),
        json={"name": "get_support_complaint", "arguments": {"complaint_id": "missing"}},
    )
    assert response.status_code != 403
