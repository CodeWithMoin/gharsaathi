"""Settings memory management (confirm / correct / undo / forget / forget-all) on PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.database.models import MemoryItem
from app.memory.household import PreferenceSpec
from app.memory.household_service import HouseholdPreferenceService
from app.memory.service import MemoryService
from tests.integration.helpers import as_user

PREFERENCE_CATEGORIES = (
    "brand_preference",
    "pack_size_preference",
    "quantity_preference",
    "product_avoidance",
)


def _allow(api: TestClient, headers: dict[str, str], allowed: bool = True) -> None:
    # Mirrors the Settings toggle in frontend/app/memory-api.ts.
    for category in PREFERENCE_CATEGORIES:
        response = api.put(
            f"/api/memory/permissions/{category}/personalize_search",
            headers=headers,
            json={"allowed": allowed, "granularity": "explicit_preferences" if allowed else "disabled"},
        )
        assert response.status_code == 200, response.text


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


def _correction(memory: dict, brand: str) -> dict:
    """The body memory-groups.mjs correctionPayload builds for an inline edit."""
    return {
        "memory_type": memory["memory_type"],
        "value": {**memory["value"], "brand": brand},
        "text": f"Doodh: hamesha {brand}",
        "source_type": "correction",
        "source_conversation_id": None,
        "sensitivity": "low",
        "confidence": 1,
        "allowed_purposes": ["personalize_search"],
        "reason": "Edited in settings",
    }


def _active(api: TestClient, headers: dict[str, str]) -> list[dict]:
    return api.get("/api/memory?status=confirmed", headers=headers).json()


async def _save_stated(session_factory, user_id: str, brand: str) -> UUID:
    async with session_factory() as session:
        actor = await MemoryService(session).actor_for_external_id(user_id)
        memory, _ = await HouseholdPreferenceService(session).save_stated(
            actor,
            PreferenceSpec(
                kind="prefer_brand", item="milk", value=brand,
                user_words=f"hum hamesha {brand} lete hain",
            ),
        )
        return memory.id


def test_confirm_correct_and_forget_flow(api: TestClient) -> None:
    alice = as_user("alice-memory")
    _allow(api, alice)
    proposal = _propose(api, alice)
    assert (proposal["status"], proposal["origin"]) == ("proposed", "explicit")
    assert proposal["learned_at"]
    assert _active(api, alice) == []

    confirmed = api.post(f"/api/memory/{proposal['id']}/confirm", headers=alice).json()
    assert confirmed["status"] == "confirmed"

    corrected = api.post(
        f"/api/memory/{proposal['id']}/correct", headers=alice, json=_correction(confirmed, "Mother Dairy")
    )
    assert corrected.status_code == 201, corrected.text
    replacement = corrected.json()
    assert replacement["supersedes_memory_id"] == proposal["id"]
    assert replacement["value"]["brand"] == "Mother Dairy"
    assert [item["id"] for item in _active(api, alice)] == [replacement["id"]]

    # An explicit (not learned-in-passing) memory is outside the undo window.
    assert api.post(f"/api/memory/{replacement['id']}/undo", headers=alice).status_code == 409

    assert api.delete(f"/api/memory/{replacement['id']}", headers=alice).status_code == 204
    assert _active(api, alice) == []


def test_correct_rejects_sensitive_values(api: TestClient) -> None:
    carol = as_user("carol-memory")
    _allow(api, carol)
    memory = _propose(api, carol)
    confirmed = api.post(f"/api/memory/{memory['id']}/confirm", headers=carol).json()
    response = api.post(
        f"/api/memory/{memory['id']}/correct", headers=carol, json=_correction(confirmed, "diabetes wala")
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_undo_reverts_a_stated_memory_and_restores_what_it_replaced(
    api: TestClient, session_factory
) -> None:
    dana = as_user("dana-memory")
    _allow(api, dana)
    first = await _save_stated(session_factory, "dana-memory", "Amul Taaza")
    second = await _save_stated(session_factory, "dana-memory", "Mother Dairy")

    active = _active(api, dana)
    assert [(item["id"], item["origin"]) for item in active] == [(str(second), "stated")]
    assert active[0]["supersedes_memory_id"] == str(first)

    undone = api.post(f"/api/memory/{second}/undo", headers=dana)
    assert undone.status_code == 200, undone.text
    assert undone.json()["id"] == str(first)
    assert [item["id"] for item in _active(api, dana)] == [str(first)]

    # Undo of the restored, first memory is still allowed; afterwards nothing restores.
    assert api.post(f"/api/memory/{first}/undo", headers=dana).json() is None
    assert _active(api, dana) == []

    missing = api.post("/api/memory/00000000-0000-0000-0000-000000000000/undo", headers=dana)
    assert missing.status_code == 404
    assert missing.json()["detail"] != "Not Found"


@pytest.mark.asyncio
async def test_undo_window_is_24_hours(api: TestClient, session_factory) -> None:
    erin = as_user("erin-memory")
    _allow(api, erin)
    memory_id = await _save_stated(session_factory, "erin-memory", "Nandini")
    async with session_factory() as session:
        await session.execute(
            update(MemoryItem)
            .where(MemoryItem.id == memory_id)
            .values(confirmed_at=datetime.now(UTC) - timedelta(hours=25))
        )
        await session.commit()
    assert api.post(f"/api/memory/{memory_id}/undo", headers=erin).status_code == 409


def test_household_scoping(api: TestClient) -> None:
    alice, bob = as_user("alice-scope"), as_user("bob-scope")
    _allow(api, alice)
    memory = _propose(api, alice)
    assert api.post(f"/api/memory/{memory['id']}/confirm", headers=bob).status_code == 404
    assert api.post(f"/api/memory/{memory['id']}/undo", headers=bob).status_code == 404
    rejected = api.post(f"/api/memory/{memory['id']}/reject", headers=alice, json={"reason": None})
    assert rejected.json()["status"] == "rejected"


def test_forget_all_needs_a_single_use_token_for_the_same_person(api: TestClient) -> None:
    alice, bob = as_user("alice-forget"), as_user("bob-forget")
    _allow(api, alice)
    first = _propose(api, alice)
    api.post(f"/api/memory/{first['id']}/confirm", headers=alice)
    _propose(api, alice, brand="Nandini")

    assert api.post(
        "/api/memory/forget-all", headers=alice, json={"confirm_token": "12345.invalid"}
    ).status_code == 400
    issued = api.post("/api/memory/forget-all/token", headers=alice)
    assert issued.status_code == 200
    token = issued.json()
    assert token["count"] == 2 and token["confirm_token"]
    assert api.post(
        "/api/memory/forget-all", headers=bob, json={"confirm_token": token["confirm_token"]}
    ).status_code == 400
    assert len(api.get("/api/memory", headers=alice).json()) == 2

    done = api.post("/api/memory/forget-all", headers=alice, json={"confirm_token": token["confirm_token"]})
    assert done.status_code == 204
    assert api.get("/api/memory", headers=alice).json() == []
    # Permissions are kept; the token cannot be replayed.
    permissions = api.get("/api/memory/permissions", headers=alice).json()
    assert any(item["allowed"] for item in permissions)
    assert api.post(
        "/api/memory/forget-all", headers=alice, json={"confirm_token": token["confirm_token"]}
    ).status_code == 400


@pytest.mark.asyncio
async def test_conversation_save_turns_repeated_choices_into_a_pending_suggestion(
    api: TestClient, session_factory
) -> None:
    from app.database.models import MemoryChoiceSignal

    frank = as_user("frank-memory")
    _allow(api, frank)
    async with session_factory() as session:
        actor = await MemoryService(session).actor_for_external_id("frank-memory")
        for occasion in ("c1", "c2", "c3"):
            session.add(
                MemoryChoiceSignal(
                    subject_id=actor.id, occasion_key=occasion, provider="swiggy_instamart",
                    item="milk", product_name="Amul Taaza Toned Milk", brand="Amul",
                    pack_size="1 L", competing_providers=[],
                )
            )
        await session.commit()

    saved = api.put("/api/conversations/current", headers=frank, json={"submitted_query": "doodh"})
    assert saved.status_code == 200
    (suggestion,) = api.get("/api/memory?status=proposed", headers=frank).json()
    assert suggestion["origin"] == "inferred"
    assert suggestion["value"]["brand"] == "Amul" and suggestion["value"]["pack_size"] == "1 L"
    assert _active(api, frank) == []


@pytest.mark.asyncio
async def test_forget_all_also_removes_inbound_delivery_calls(api: TestClient, session_factory) -> None:
    from app.inbound_calls import InboundCallStore, ParsedCall

    store = InboundCallStore(session_factory)
    started = datetime.now(UTC) - timedelta(minutes=5)
    for user, n in (("gita-memory", 1), ("hari-memory", 2)):
        await store.record_context(user, f"cohort-{user}", started)
        call = ParsedCall(
            interaction_id=f"20261122/forget-{n}", started_at=started, ended_at=None, provider="zepto",
            outcome="route_confirmed", summary="Guided to the gate.", caller="•••• 2590",
            user_identifier=None, cohort_id=None,
        )
        assert await store.add_call(user, call, "route_confirmed", started) is not None
    gita = as_user("gita-memory")
    assert len(api.get("/api/inbound-delivery/calls", headers=gita).json()["calls"]) == 1

    token = api.post("/api/memory/forget-all/token", headers=gita).json()["confirm_token"]
    assert api.post("/api/memory/forget-all", headers=gita, json={"confirm_token": token}).status_code == 204

    assert api.get("/api/inbound-delivery/calls", headers=gita).json()["calls"] == []
    assert len(await store.list_calls("hari-memory")) == 1
    assert {context.user_id for context in await store.contexts_since(started - timedelta(minutes=1))} == {"hari-memory"}
