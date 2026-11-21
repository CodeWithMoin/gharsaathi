from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.session import get_db_session
from app.legal_consent import CURRENT_TERMS_VERSION
from app.main import app


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'consent.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db_session, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_legal_consent_requires_explicit_terms_acceptance(client: AsyncClient) -> None:
    headers = {"X-Test-User-Id": "legal-user"}

    initial = await client.get("/api/legal-consent", headers=headers)
    assert initial.status_code == 200
    assert initial.json() == {
        "terms_version": CURRENT_TERMS_VERSION,
        "accepted": False,
        "accepted_at": None,
        "personalization_allowed": False,
        "personalization_decided_at": None,
    }

    rejected = await client.post(
        "/api/legal-consent",
        headers=headers,
        json={"terms_accepted": False, "personalization_allowed": True},
    )
    assert rejected.status_code == 422

    accepted = await client.post(
        "/api/legal-consent",
        headers=headers,
        json={"terms_accepted": True, "personalization_allowed": True},
    )
    assert accepted.status_code == 201
    payload = accepted.json()
    assert payload["terms_version"] == CURRENT_TERMS_VERSION
    assert payload["accepted"] is True
    assert payload["accepted_at"] is not None
    assert payload["personalization_allowed"] is True
    assert payload["personalization_decided_at"] is not None


@pytest.mark.asyncio
async def test_consent_is_user_scoped_and_returning_user_keeps_original_record(
    client: AsyncClient,
) -> None:
    headers = {"X-Test-User-Id": "returning-user"}
    first = await client.post(
        "/api/legal-consent",
        headers=headers,
        json={"terms_accepted": True, "personalization_allowed": False},
    )
    assert first.status_code == 201

    repeated = await client.post(
        "/api/legal-consent",
        headers=headers,
        json={"terms_accepted": True, "personalization_allowed": True},
    )
    assert repeated.status_code == 201
    assert repeated.json()["accepted_at"] == first.json()["accepted_at"]
    assert repeated.json()["personalization_allowed"] is False

    other_user = await client.get(
        "/api/legal-consent", headers={"X-Test-User-Id": "another-user"}
    )
    assert other_user.json()["accepted"] is False
