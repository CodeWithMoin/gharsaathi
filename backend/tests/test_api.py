from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.commerce.swiggy.errors import SwiggyAuthenticationRequired
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
from app.main import app


class FakeService:
    authenticated = True

    async def connection_status(self) -> ConnectionStatus:
        return ConnectionStatus(connected=self.authenticated)

    async def addresses(self, page: int, page_size: int) -> AddressList:
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
app.dependency_overrides[get_swiggy_service] = lambda: fake_service
client = TestClient(app)


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_addresses_are_typed() -> None:
    response = client.get("/api/swiggy/addresses?page=1&pageSize=10")
    assert response.status_code == 200
    assert response.json()["addresses"][0] == {
        "id": "addr_home",
        "address_line": "Test address",
        "category": None,
        "tag": None,
    }


def test_search_validates_blank_query() -> None:
    response = client.post(
        "/api/swiggy/search",
        json={"query": "  ", "address_id": "addr_home"},
    )
    assert response.status_code == 422


def test_search_returns_normalized_product() -> None:
    response = client.post(
        "/api/swiggy/search",
        json={"query": "Fortune cooking oil 1 litre", "address_id": "addr_home"},
    )
    assert response.status_code == 200
    assert response.json()["products"][0]["sku_id"] == "sku1"


def test_authentication_required_is_401() -> None:
    fake_service.authenticated = False
    try:
        response = client.get("/api/swiggy/addresses")
        assert response.status_code == 401
        assert response.json()["detail"] == "Connect your Swiggy account first."
    finally:
        fake_service.authenticated = True

