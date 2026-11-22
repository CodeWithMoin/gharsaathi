"""Owner memory seed + 'What GharSaathi knows' on PostgreSQL: seed, summary route, edit, delete."""

import argparse
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.memory import demo_seed
from tests.integration.helpers import as_user

OWNER = "user_seed_owner_01"
ANSWERS = {
    "diet": "jain",
    "allergies": ["sesame"],
    "usual_buys": [{"category": "milk", "brand": "Amul", "size": "1 L", "quantity": 2}],
    "avoid": ["chocolate"],
    "value_preference": "premium",
    "language": "english",
}


def _allow(api: TestClient, headers: dict[str, str]) -> None:
    for category in ("brand_preference", "pack_size_preference", "quantity_preference", "product_avoidance"):
        response = api.put(
            f"/api/memory/permissions/{category}/personalize_search",
            headers=headers, json={"allowed": True, "granularity": "explicit_preferences"},
        )
        assert response.status_code == 200, response.text


async def _seed(session_factory, tmp_path: Path, *, apply: bool, remove: bool = False) -> list[str]:
    path = tmp_path / "home.json"
    path.write_text(json.dumps(ANSWERS), encoding="utf-8")
    namespace = argparse.Namespace(
        file=str(path), user_id=OWNER, dry_run=not apply, apply=apply, remove_seeded=remove, turn_on_memory=False
    )
    async with session_factory() as session:
        return await demo_seed.run(namespace, session)


@pytest.mark.asyncio
async def test_seed_summary_edit_and_remove(api: TestClient, session_factory, tmp_path: Path) -> None:
    owner = as_user(OWNER)
    _allow(api, owner)
    assert api.get("/api/memory/summary", headers=owner).json()["total"] == 0

    dry = await _seed(session_factory, tmp_path, apply=False)
    assert "Dry run: nothing written." in dry
    assert api.get("/api/memory", headers=owner).json() == []

    await _seed(session_factory, tmp_path, apply=True)
    again = await _seed(session_factory, tmp_path, apply=True)
    assert again[2].startswith("create 0, update 0, unchanged 5, remove 0")

    summary = api.get("/api/memory/summary", headers=owner).json()
    groups = {group["id"]: group["items"] for group in summary["groups"]}
    assert [item["label"] for item in groups["food"]] == [
        "Keep out: sesame", "Diet: Jain food (no onion, garlic or root vegetables)",
    ] or [item["label"] for item in groups["food"]] == [
        "Diet: Jain food (no onion, garlic or root vegetables)", "Keep out: sesame",
    ]
    assert {item["source"] for group in summary["groups"] for item in group["items"]} == {"told"}
    assert groups["home"][0]["id"] == "profile:language"
    # Another user sees none of it.
    assert api.get("/api/memory/summary", headers=as_user("someone_else_02")).json()["total"] == 0

    memories = {memory["value"].get("kind"): memory for memory in api.get("/api/memory?status=confirmed", headers=owner).json()}
    diet = memories["diet"]

    def edit(memory: dict, **value: str):
        return api.post(
            f"/api/memory/{memory['id']}/correct", headers=owner,
            json={
                "memory_type": memory["memory_type"], "value": {**memory["value"], **value},
                "text": "client text is replaced", "source_type": "correction",
                "source_conversation_id": memory["source_conversation_id"], "sensitivity": "low",
                "confidence": 1, "allowed_purposes": ["personalize_search"], "reason": "Edited in settings",
            },
        )

    edited = edit(diet, diet="eggetarian")
    assert edited.status_code == 201, edited.text
    assert edited.json()["text"] == "Diet: Vegetarian, eggs are fine"
    assert edit(edited.json(), diet="keto").status_code == 422
    sesame = memories["avoid_ingredient"]
    refused = edit(sesame, ingredient="diabetes sugar")
    assert refused.status_code == 422 and "health" in refused.json()["detail"]
    assert api.delete(f"/api/memory/{sesame['id']}", headers=owner).status_code == 204

    removed = await _seed(session_factory, tmp_path, apply=True, remove=True)
    # The edited diet stays tagged as seeded; the deleted keep-out is already gone.
    assert removed[-1].startswith("Removed 4 seeded memories.")
    assert api.get("/api/memory?status=confirmed", headers=owner).json() == []
    assert api.get("/api/profile", headers=owner).json()["language"] == "english"
