from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commerce.swiggy.errors import SwiggyAuthenticationRequired
from app.commerce.demo import DemoSwiggyService
from app.commerce.swiggy.models import (
    Address,
    AddressList,
    AddressResolution,
    ConnectionStatus,
    Pagination,
    Price,
    Product,
    ProductSearchResult,
)
from app.dependencies import get_swiggy_service
from app.database.base import Base
from app.database.session import get_db_session
from app.main import app


class FakeService:
    authenticated = True
    address_calls = 0

    async def connection_status(self) -> ConnectionStatus:
        return ConnectionStatus(connected=self.authenticated)

    async def addresses(self, page: int, page_size: int) -> AddressList:
        self.address_calls += 1
        if not self.authenticated:
            raise SwiggyAuthenticationRequired("Connect your Swiggy account first.")
        return AddressList(
            addresses=[Address(id="addr_home", address_line="Test address")],
            pagination=Pagination(page=page, page_size=page_size, total=1, total_pages=1),
            resolution=AddressResolution(default_address_id="addr_home"),
        )

    async def search(self, query: str, address_id: str, offset: int) -> ProductSearchResult:
        return ProductSearchResult(
            products=[
                Product(
                    product_id="p1",
                    sku_id="sku1",
                    spin_id="spin1",
                    name=query,
                    price=Price(mrp=180, offer_price=154),
                    available=True,
                )
            ]
        )


fake_service = FakeService()


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    fake_service.address_calls = 0
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_swiggy_service] = lambda: fake_service
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_swiggy_service, None)
    app.dependency_overrides.pop(get_db_session, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_browser_can_read_retry_after_header(client: AsyncClient) -> None:
    response = await client.get(
        "/health", headers={"Origin": "http://localhost:3003"}
    )
    assert response.headers["access-control-expose-headers"] == "Retry-After"


@pytest.mark.asyncio
async def test_addresses_are_typed(client: AsyncClient) -> None:
    response = await client.get(
        "/api/swiggy/addresses?page=1&pageSize=10&refresh=true",
        headers={"X-Test-User-Id": "test-user"},
    )
    assert response.status_code == 200
    assert response.json()["addresses"][0] == {
        "id": "addr_home",
        "address_line": "Test address",
        "category": None,
        "tag": None,
    }


@pytest.mark.asyncio
async def test_address_reload_uses_database_snapshot_without_provider_call(
    client: AsyncClient,
) -> None:
    headers = {"X-Test-User-Id": "reload-user"}

    empty = await client.get("/api/swiggy/addresses", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["needs_refresh"] is True
    assert fake_service.address_calls == 0

    refreshed = await client.get(
        "/api/swiggy/addresses?refresh=true", headers=headers
    )
    assert refreshed.status_code == 200
    assert fake_service.address_calls == 1

    cached = await client.get("/api/swiggy/addresses", headers=headers)
    assert cached.status_code == 200
    assert cached.json()["cached"] is True
    assert cached.json()["addresses"][0]["id"] == "addr_home"
    assert fake_service.address_calls == 1

    selected = await client.put(
        "/api/swiggy/addresses/selected",
        headers=headers,
        json={"address_id": "addr_home"},
    )
    assert selected.status_code == 200
    assert selected.json()["resolution"]["default_address_id"] == "addr_home"


@pytest.mark.asyncio
async def test_demo_addresses_do_not_leak_into_live_provider_state(
    client: AsyncClient,
) -> None:
    headers = {"X-Test-User-Id": "demo-boundary-user"}
    app.dependency_overrides[get_swiggy_service] = DemoSwiggyService

    demo = await client.get("/api/swiggy/addresses", headers=headers)
    assert demo.status_code == 200
    assert demo.json()["addresses"][0]["id"] == "demo-home"

    app.dependency_overrides[get_swiggy_service] = lambda: fake_service
    live = await client.get("/api/swiggy/addresses", headers=headers)
    assert live.status_code == 200
    assert live.json()["addresses"] == []
    assert live.json()["needs_refresh"] is True


@pytest.mark.asyncio
async def test_search_validates_blank_query(client: AsyncClient) -> None:
    response = await client.post(
        "/api/swiggy/search",
        json={"query": "  ", "address_id": "addr_home"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_returns_normalized_product(client: AsyncClient) -> None:
    response = await client.post(
        "/api/swiggy/search",
        json={"query": "Fortune cooking oil 1 litre", "address_id": "addr_home"},
    )
    assert response.status_code == 200
    assert response.json()["products"][0]["sku_id"] == "sku1"


@pytest.mark.asyncio
async def test_authentication_required_is_401(client: AsyncClient) -> None:
    fake_service.authenticated = False
    try:
        response = await client.get(
            "/api/swiggy/addresses?refresh=true",
            headers={"X-Test-User-Id": "test-user"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Connect your Swiggy account first."
    finally:
        fake_service.authenticated = True
