"""Conversation history and preference memory against real PostgreSQL."""

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.integration.helpers import as_user


def _chat(query: str, *, text: str) -> dict[str, object]:
    message_id = f"m-{uuid4().hex[:8]}"
    return {
        "submitted_query": query,
        "searched": True,
        "messages": [
            {
                "id": message_id,
                "role": "user",
                "content": text,
                "modality": "voice",
                "created_at": "2026-11-22T10:00:00Z",
            }
        ],
        "results_after_message_id": message_id,
        "products": [
            {
                "provider": "zepto",
                "product_id": "demo-atta",
                "sku_id": "zepto-demo-sku-atta",
                "name": "Aashirvaad Shudh Chakki Atta",
                "price": {"mrp": 310, "offer_price": 272},
                "available": True,
            }
        ],
    }


def test_new_open_and_search_history_round_trip_and_stay_user_scoped(api: TestClient) -> None:
    alice = as_user("alice")
    assert api.get("/api/conversations/current", headers=alice).json() is None

    first = api.put(
        "/api/conversations/current", headers=alice, json=_chat("atta", text="Do kilo atta chahiye")
    )
    assert first.status_code == 200
    first_id = first.json()["id"]
    # A second save updates the same open conversation rather than forking it.
    again = api.put(
        "/api/conversations/current", headers=alice, json=_chat("atta", text="Do kilo atta chahiye")
    )
    assert again.json()["id"] == first_id

    assert api.post("/api/conversations/new", headers=alice).status_code == 204
    assert api.get("/api/conversations/current", headers=alice).json() is None

    second = api.put(
        "/api/conversations/current", headers=alice, json=_chat("doodh", text="Amul doodh bhi")
    )
    second_id = second.json()["id"]
    assert second_id != first_id

    recent = api.get("/api/conversations", headers=alice).json()
    assert [item["id"] for item in recent] == [second_id, first_id]
    assert [item["is_active"] for item in recent] == [True, False]
    assert recent[1]["title"] == "Do kilo atta chahiye"

    found = api.get("/api/conversations", headers=alice, params={"query": "ATTA"}).json()
    assert [item["id"] for item in found] == [first_id]

    reopened = api.post(f"/api/conversations/{first_id}/open", headers=alice)
    assert reopened.status_code == 200
    assert reopened.json()["submitted_query"] == "atta"
    assert reopened.json()["products"][0]["sku_id"] == "zepto-demo-sku-atta"
    assert api.get("/api/conversations/current", headers=alice).json()["id"] == first_id
    active = {item["id"]: item["is_active"] for item in api.get("/api/conversations", headers=alice).json()}
    assert active == {first_id: True, second_id: False}

    bob = as_user("bob")
    assert api.get("/api/conversations", headers=bob).json() == []
    assert api.post(f"/api/conversations/{first_id}/open", headers=bob).status_code == 404
    assert api.post(f"/api/conversations/{uuid4()}/open", headers=alice).status_code == 404
    assert api.post("/api/conversations/not-a-uuid/open", headers=alice).status_code == 422


def _proposal() -> dict[str, object]:
    return {
        "memory_type": "brand_preference",
        "value": {"brand": "Fortune", "pack_size": "1 L", "product_category": "oil"},
        "text": "Prefers Fortune oil in one litre packs.",
        "source_type": "explicit_statement",
        "sensitivity": "low",
        "confidence": 1.0,
        "allowed_purposes": ["personalize_search"],
    }


def _retrieved_ids(api: TestClient, headers: dict[str, str]) -> list[str]:
    response = api.post(
        "/api/memory/retrieve", headers=headers, json={"purpose": "personalize_search"}
    )
    assert response.status_code == 200
    return [item["id"] for item in response.json()["memories"]]


def test_preference_needs_confirmation_and_permission_and_is_forgettable(api: TestClient) -> None:
    alice, bob = as_user("alice"), as_user("bob")
    created = api.post("/api/memory/proposals", headers=alice, json=_proposal())
    assert created.status_code == 201
    memory_id = created.json()["id"]

    assert _retrieved_ids(api, alice) == []  # proposed, not confirmed

    # Another household cannot confirm, see or delete Alice's memory.
    assert api.post(f"/api/memory/{memory_id}/confirm", headers=bob).status_code == 404
    assert api.get("/api/memory", headers=bob).json() == []
    assert api.delete(f"/api/memory/{memory_id}", headers=bob).status_code == 404

    assert api.post(f"/api/memory/{memory_id}/confirm", headers=alice).json()["status"] == "confirmed"
    assert _retrieved_ids(api, alice) == []  # confirmed but no permission yet

    granted = api.put(
        "/api/memory/permissions/brand_preference/personalize_search",
        headers=alice,
        json={"allowed": True, "retention_days": 365},
    )
    assert granted.status_code == 200
    assert _retrieved_ids(api, alice) == [memory_id]
    assert _retrieved_ids(api, bob) == []
    permissions = api.get("/api/memory/permissions", headers=alice).json()
    assert any(
        item["data_category"] == "brand_preference" and item["allowed"] for item in permissions
    )
    assert api.get("/api/memory/permissions", headers=bob).json() == []

    revoked = api.put(
        "/api/memory/permissions/brand_preference/personalize_search",
        headers=alice,
        json={"allowed": False},
    )
    assert revoked.status_code == 200
    assert _retrieved_ids(api, alice) == []

    api.put(
        "/api/memory/permissions/brand_preference/personalize_search",
        headers=alice,
        json={"allowed": True, "retention_days": 365},
    )
    assert api.delete(f"/api/memory/{memory_id}", headers=alice).status_code == 204
    assert _retrieved_ids(api, alice) == []
    assert all(item["id"] != memory_id for item in api.get("/api/memory", headers=alice).json())


class _RecordingHistoryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: dict[str, object], **_: object) -> dict:
        self.calls.append((name, arguments))
        return {"data": {"orders": [], "hasMore": False}}


class _HistoryOnlySwiggy:
    def __init__(self, client: _RecordingHistoryClient) -> None:
        self.client = client


def test_purchase_history_import_is_permission_gated_cached_and_purgeable(
    api: TestClient, override
) -> None:
    from app.dependencies import get_swiggy_service

    history = _RecordingHistoryClient()
    override(get_swiggy_service, lambda: _HistoryOnlySwiggy(history))
    alice = as_user("alice")

    refused = api.post("/api/memory/purchase-history/sync", headers=alice)
    assert refused.status_code == 403
    assert history.calls == []  # no provider read before consent

    api.put(
        "/api/memory/permissions/purchase_history/proactive_reorder",
        headers=alice,
        json={"allowed": True, "retention_days": 90},
    )
    synced = api.post("/api/memory/purchase-history/sync", headers=alice)
    assert synced.status_code == 200, synced.text
    assert synced.json()["cached"] is False
    assert history.calls == [("get_orders", {"count": 15})]

    cached = api.post("/api/memory/purchase-history/sync", headers=alice)
    assert cached.json()["cached"] is True
    assert len(history.calls) == 1

    # Bob's consent is independent of Alice's.
    assert api.post("/api/memory/purchase-history/sync", headers=as_user("bob")).status_code == 403
    assert len(history.calls) == 1

    # Regression: this route used to be shadowed by DELETE /api/memory/{memory_id} (422).
    assert api.delete("/api/memory/purchase-history", headers=alice).status_code == 204
    # Purging also revokes consent, so the next import is refused before any provider read.
    assert api.post("/api/memory/purchase-history/sync", headers=alice).status_code == 403
    assert len(history.calls) == 1
    permissions = api.get("/api/memory/permissions", headers=alice).json()
    assert [item["allowed"] for item in permissions if item["data_category"] == "purchase_history"] == [False]
