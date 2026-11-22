"""Settings memory management (confirm / update / undo / forget / forget-all) on PostgreSQL."""

from fastapi.testclient import TestClient

from tests.integration.helpers import as_user


def _propose(api: TestClient, headers: dict[str, str], brand: str = "Amul Taaza") -> dict:
    response = api.post(
        "/api/memory/proposals",
        headers=headers,
        json={
            "memory_type": "brand_preference",
            "value": {"kind": "prefer_brand", "product_category": "milk", "brand": brand},
            "text": f"milk: hamesha {brand}",
            "source_type": "explicit_statement",
            "allowed_purposes": ["personalize_search"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _active(api: TestClient, headers: dict[str, str]) -> list[dict]:
    return api.get("/api/memory?status=confirmed", headers=headers).json()


def test_confirm_update_undo_and_forget_flow(api: TestClient) -> None:
    alice = as_user("alice-memory")
    assert api.get("/api/memory/household-preferences/permission", headers=alice).json() == {
        "allowed": False
    }
    assert api.put(
        "/api/memory/household-preferences/permission", headers=alice, json={"allowed": True}
    ).json() == {"allowed": True}

    proposal = _propose(api, alice)
    assert proposal["status"] == "proposed"
    assert (proposal["kind"], proposal["group"], proposal["origin"]) == (
        "prefer_brand", "pasand", "explicit",
    )
    assert _active(api, alice) == []

    confirmed = api.post(f"/api/memory/{proposal['id']}/confirm", headers=alice).json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["display_value"] == "Amul Taaza"
    assert confirmed["item"] == "milk"

    updated = api.post(
        f"/api/memory/{proposal['id']}/update", headers=alice, json={"value": "Mother Dairy"}
    )
    assert updated.status_code == 201, updated.text
    replacement = updated.json()
    assert replacement["supersedes_memory_id"] == proposal["id"]
    assert replacement["display_value"] == "Mother Dairy"
    assert replacement["item"] == "milk"
    assert [item["id"] for item in _active(api, alice)] == [replacement["id"]]
    superseded = api.get("/api/memory?status=superseded", headers=alice).json()
    assert [item["id"] for item in superseded] == [proposal["id"]]

    undone = api.post(f"/api/memory/{replacement['id']}/undo", headers=alice).json()
    assert undone["restored"]["id"] == proposal["id"]
    assert [item["id"] for item in _active(api, alice)] == [proposal["id"]]

    assert api.delete(f"/api/memory/{proposal['id']}", headers=alice).status_code == 204
    assert _active(api, alice) == []


def test_update_rejects_sensitive_values_and_needs_consent(api: TestClient) -> None:
    carol = as_user("carol-memory")
    api.put("/api/memory/household-preferences/permission", headers=carol, json={"allowed": True})
    memory = _propose(api, carol)
    api.post(f"/api/memory/{memory['id']}/confirm", headers=carol)

    sensitive = api.post(
        f"/api/memory/{memory['id']}/update", headers=carol, json={"value": "diabetes wala"}
    )
    assert sensitive.status_code == 422

    api.put("/api/memory/household-preferences/permission", headers=carol, json={"allowed": False})
    blocked = api.post(
        f"/api/memory/{memory['id']}/update", headers=carol, json={"value": "Mother Dairy"}
    )
    assert blocked.status_code == 403


def test_reject_and_household_scoping(api: TestClient) -> None:
    alice, bob = as_user("alice-scope"), as_user("bob-scope")
    api.put("/api/memory/household-preferences/permission", headers=alice, json={"allowed": True})
    memory = _propose(api, alice)

    assert api.post(f"/api/memory/{memory['id']}/confirm", headers=bob).status_code == 404
    assert api.post(f"/api/memory/{memory['id']}/undo", headers=bob).status_code == 404
    assert api.post(
        f"/api/memory/{memory['id']}/update", headers=bob, json={"value": "Nandini"}
    ).status_code == 404

    rejected = api.post(f"/api/memory/{memory['id']}/reject", headers=alice, json={"reason": None})
    assert rejected.json()["status"] == "rejected"


def test_forget_all_needs_a_valid_token_for_the_same_person(api: TestClient) -> None:
    alice, bob = as_user("alice-forget"), as_user("bob-forget")
    api.put("/api/memory/household-preferences/permission", headers=alice, json={"allowed": True})
    first = _propose(api, alice)
    api.post(f"/api/memory/{first['id']}/confirm", headers=alice)
    _propose(api, alice, brand="Nandini")

    assert api.post(
        "/api/memory/forget-all", headers=alice, json={"confirm_token": "12345.invalid"}
    ).status_code == 409
    confirmation = api.post("/api/memory/forget-all/confirmation", headers=alice).json()
    assert confirmation["count"] == 2
    # Another person's token never deletes alice's memories, or their own.
    assert api.post(
        "/api/memory/forget-all", headers=bob, json={"confirm_token": confirmation["confirm_token"]}
    ).status_code == 409
    assert len(api.get("/api/memory", headers=alice).json()) == 2

    done = api.post(
        "/api/memory/forget-all", headers=alice, json={"confirm_token": confirmation["confirm_token"]}
    )
    assert done.json() == {"deleted": 2}
    assert api.get("/api/memory", headers=alice).json() == []
