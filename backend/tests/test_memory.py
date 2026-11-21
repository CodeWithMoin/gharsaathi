from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import MemoryEvent
from app.database.session import get_db_session
from app.commerce.swiggy.models import Price, Product, ProductSearchResult
from app.dependencies import get_swiggy_service
from app.main import app
from app.memory.dependencies import get_memory_extractor
from app.memory.schemas import MemoryExtractionResult


@pytest_asyncio.fixture
async def memory_db(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def memory_client(memory_db: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield memory_db

    app.dependency_overrides[get_db_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.pop(get_db_session, None)


def headers(user_id: str) -> dict[str, str]:
    return {"X-Test-User-Id": user_id}


class ReadOnlyHistoryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, object], **_: object
    ) -> dict[str, object]:
        self.calls.append((name, arguments))
        return {"data": {"orders": [], "hasMore": False}}


class HistoryService:
    def __init__(self, client: ReadOnlyHistoryClient) -> None:
        self.client = client


def proposal(
    text: str = "Prefers Fortune oil in one litre packs.",
    allowed_purposes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "memory_type": "brand_preference",
        "value": {
            "brand": "Fortune",
            "pack_size": "1 L",
            "product_category": "oil",
        },
        "text": text,
        "source_type": "explicit_statement",
        "sensitivity": "low",
        "confidence": 1.0,
        "allowed_purposes": allowed_purposes or ["personalize_search"],
    }


class FakeExtractor:
    name = "local_rules"

    async def extract(self, message: str, locale: str) -> MemoryExtractionResult:
        del locale
        return MemoryExtractionResult.model_validate(
            {
                "candidates": [
                    {
                        "memory_type": "brand_preference",
                        "attributes": [
                            {"key": "brand", "value": "Fortune"},
                            {"key": "product_category", "value": "oil"},
                        ],
                        "summary": "Prefers Fortune oil.",
                        "evidence": "prefer Fortune oil",
                        "confidence": 0.97,
                    },
                    {
                        "memory_type": "pack_size_preference",
                        "attributes": [{"key": "pack_size", "value": "5 L"}],
                        "summary": "Unsupported model output.",
                        "evidence": "not in the message",
                        "confidence": 0.8,
                    },
                ]
            }
        )


class FakePersonalizedSearchService:
    query: str | None = None

    async def search(
        self, query: str, address_id: str, offset: int
    ) -> ProductSearchResult:
        del address_id, offset
        self.query = query
        return ProductSearchResult(
            products=[
                Product(
                    product_id="p1",
                    sku_id="sku1",
                    spin_id="spin1",
                    name="Fortune cooking oil 1 L",
                    brand="Fortune",
                    price=Price(mrp=180, offer_price=160),
                    available=True,
                )
            ]
        )


@pytest.mark.asyncio
async def test_memory_requires_confirmation_and_permission_for_retrieval(
    memory_client: AsyncClient,
) -> None:
    created = await memory_client.post(
        "/api/memory/proposals",
        headers=headers("user-a"),
        json=proposal(),
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]
    assert created.json()["status"] == "proposed"

    before_confirmation = await memory_client.post(
        "/api/memory/retrieve",
        headers=headers("user-a"),
        json={"purpose": "personalize_search"},
    )
    assert before_confirmation.json()["memories"] == []

    confirmed = await memory_client.post(
        f"/api/memory/{memory_id}/confirm",
        headers=headers("user-a"),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    without_permission = await memory_client.post(
        "/api/memory/retrieve",
        headers=headers("user-a"),
        json={"purpose": "personalize_search"},
    )
    assert without_permission.json()["memories"] == []

    permission = await memory_client.put(
        "/api/memory/permissions/brand_preference/personalize_search",
        headers=headers("user-a"),
        json={"allowed": True, "retention_days": 365},
    )
    assert permission.status_code == 200

    retrieved = await memory_client.post(
        "/api/memory/retrieve",
        headers=headers("user-a"),
        json={"purpose": "personalize_search"},
    )
    assert [item["id"] for item in retrieved.json()["memories"]] == [memory_id]

    revoked = await memory_client.put(
        "/api/memory/permissions/brand_preference/personalize_search",
        headers=headers("user-a"),
        json={"allowed": False},
    )
    assert revoked.status_code == 200
    after_revocation = await memory_client.post(
        "/api/memory/retrieve",
        headers=headers("user-a"),
        json={"purpose": "personalize_search"},
    )
    assert after_revocation.json()["memories"] == []


@pytest.mark.asyncio
async def test_purchase_history_sync_is_explicit_and_consent_gated(
    memory_client: AsyncClient,
) -> None:
    history_client = ReadOnlyHistoryClient()
    app.dependency_overrides[get_swiggy_service] = lambda: HistoryService(
        history_client
    )
    try:
        denied = await memory_client.post(
            "/api/memory/purchase-history/sync",
            headers=headers("history-user"),
        )
        assert denied.status_code == 403
        assert history_client.calls == []

        permission = await memory_client.put(
            "/api/memory/permissions/purchase_history/proactive_reorder",
            headers=headers("history-user"),
            json={"allowed": True, "retention_days": 90},
        )
        assert permission.status_code == 200

        synced = await memory_client.post(
            "/api/memory/purchase-history/sync",
            headers=headers("history-user"),
        )
        assert synced.status_code == 200
        assert synced.json() == {
            "imported_orders": 0,
            "imported_products": 0,
            "imported_occurrences": 0,
            "pruned_orders": 0,
            "cached": False,
        }
        assert history_client.calls == [("get_orders", {"count": 15})]

        cached = await memory_client.post(
            "/api/memory/purchase-history/sync",
            headers=headers("history-user"),
        )
        assert cached.status_code == 200
        assert cached.json()["cached"] is True
        assert history_client.calls == [("get_orders", {"count": 15})]
    finally:
        app.dependency_overrides.pop(get_swiggy_service, None)


@pytest.mark.asyncio
async def test_extraction_creates_unconfirmed_evidence_backed_proposals(
    memory_client: AsyncClient,
) -> None:
    app.dependency_overrides[get_memory_extractor] = lambda: FakeExtractor()
    try:
        response = await memory_client.post(
            "/api/memory/extract",
            headers=headers("user-a"),
            json={
                "message": "We always prefer Fortune oil at home.",
                "conversation_id": "conversation-1",
            },
        )
    finally:
        app.dependency_overrides.pop(get_memory_extractor, None)

    assert response.status_code == 200
    assert response.json()["prompt_version"] == "1.0.0"
    assert len(response.json()["proposals"]) == 1
    proposed = response.json()["proposals"][0]
    assert proposed["status"] == "proposed"
    assert proposed["source_conversation_id"] == "conversation-1"
    assert proposed["value"] == {"brand": "Fortune", "product_category": "oil"}

    app.dependency_overrides[get_memory_extractor] = lambda: FakeExtractor()
    try:
        duplicate = await memory_client.post(
            "/api/memory/extract",
            headers=headers("user-a"),
            json={"message": "We always prefer Fortune oil at home."},
        )
    finally:
        app.dependency_overrides.pop(get_memory_extractor, None)
    assert duplicate.json()["proposals"] == []


@pytest.mark.asyncio
async def test_personalized_search_uses_only_confirmed_permitted_memory(
    memory_client: AsyncClient,
) -> None:
    created = await memory_client.post(
        "/api/memory/proposals",
        headers=headers("user-a"),
        json=proposal(),
    )
    memory_id = created.json()["id"]
    await memory_client.post(
        f"/api/memory/{memory_id}/confirm", headers=headers("user-a")
    )
    await memory_client.put(
        "/api/memory/permissions/brand_preference/personalize_search",
        headers=headers("user-a"),
        json={"allowed": True},
    )

    fake_service = FakePersonalizedSearchService()
    previous = app.dependency_overrides.get(get_swiggy_service)
    app.dependency_overrides[get_swiggy_service] = lambda: fake_service
    try:
        response = await memory_client.post(
            "/api/swiggy/search/personalized",
            headers=headers("user-a"),
            json={"query": "cooking oil", "address_id": "addr-home"},
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_swiggy_service, None)
        else:
            app.dependency_overrides[get_swiggy_service] = previous

    assert response.status_code == 200
    assert fake_service.query == "cooking oil Fortune"
    assert response.json()["provider_query"] == "cooking oil Fortune"
    assert {item["memory_type"] for item in response.json()["applied_memories"]} == {
        "brand_preference"
    }


@pytest.mark.asyncio
async def test_person_memories_are_isolated(memory_client: AsyncClient) -> None:
    created = await memory_client.post(
        "/api/memory/proposals",
        headers=headers("alice"),
        json=proposal(),
    )
    memory_id = created.json()["id"]

    bob_list = await memory_client.get("/api/memory", headers=headers("bob"))
    assert bob_list.status_code == 200
    assert bob_list.json() == []

    bob_confirm = await memory_client.post(
        f"/api/memory/{memory_id}/confirm",
        headers=headers("bob"),
    )
    assert bob_confirm.status_code == 404


@pytest.mark.asyncio
async def test_correction_preserves_history_and_supersedes_old_memory(
    memory_client: AsyncClient,
    memory_db: AsyncSession,
) -> None:
    created = await memory_client.post(
        "/api/memory/proposals", headers=headers("user-a"), json=proposal()
    )
    original_id = created.json()["id"]
    await memory_client.post(
        f"/api/memory/{original_id}/confirm", headers=headers("user-a")
    )

    correction_payload = proposal(
        text="Prefers Saffola oil in one litre packs."
    ) | {
        "value": {"brand": "Saffola", "pack_size": "1 L"},
        "source_type": "correction",
        "reason": "The user changed the preferred brand.",
    }
    corrected = await memory_client.post(
        f"/api/memory/{original_id}/correct",
        headers=headers("user-a"),
        json=correction_payload,
    )
    assert corrected.status_code == 201
    assert corrected.json()["status"] == "confirmed"
    assert corrected.json()["supersedes_memory_id"] == original_id

    memories = await memory_client.get("/api/memory", headers=headers("user-a"))
    statuses = {item["id"]: item["status"] for item in memories.json()}
    assert statuses[original_id] == "superseded"
    assert statuses[corrected.json()["id"]] == "confirmed"

    events = list((await memory_db.execute(select(MemoryEvent))).scalars().all())
    assert {event.event_type.value for event in events} >= {
        "proposed",
        "confirmed",
        "superseded",
        "corrected",
    }


@pytest.mark.asyncio
async def test_forget_is_soft_delete_and_hidden_by_default(
    memory_client: AsyncClient,
) -> None:
    created = await memory_client.post(
        "/api/memory/proposals", headers=headers("user-a"), json=proposal()
    )
    memory_id = created.json()["id"]

    deleted = await memory_client.delete(
        f"/api/memory/{memory_id}", headers=headers("user-a")
    )
    assert deleted.status_code == 204

    default_list = await memory_client.get("/api/memory", headers=headers("user-a"))
    assert default_list.json() == []

    audit_list = await memory_client.get(
        "/api/memory?include_deleted=true", headers=headers("user-a")
    )
    assert audit_list.json()[0]["status"] == "deleted"


@pytest.mark.asyncio
async def test_prohibited_purpose_cannot_be_enabled_or_stored(
    memory_client: AsyncClient,
) -> None:
    proposed = await memory_client.post(
        "/api/memory/proposals",
        headers=headers("user-a"),
        json=proposal(allowed_purposes=["differential_pricing"]),
    )
    assert proposed.status_code == 403

    permission = await memory_client.put(
        "/api/memory/permissions/financial_profile/differential_pricing",
        headers=headers("user-a"),
        json={"allowed": True},
    )
    assert permission.status_code == 403


@pytest.mark.asyncio
async def test_invalid_transition_returns_conflict(memory_client: AsyncClient) -> None:
    created = await memory_client.post(
        "/api/memory/proposals", headers=headers("user-a"), json=proposal()
    )
    memory_id = created.json()["id"]
    await memory_client.post(
        f"/api/memory/{memory_id}/confirm", headers=headers("user-a")
    )
    second_confirmation = await memory_client.post(
        f"/api/memory/{memory_id}/confirm", headers=headers("user-a")
    )
    assert second_confirmation.status_code == 409


@pytest.mark.asyncio
async def test_blank_development_identity_is_rejected(
    memory_client: AsyncClient,
) -> None:
    response = await memory_client.get("/api/memory", headers=headers("   "))
    assert response.status_code == 422
