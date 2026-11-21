from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.commerce.swiggy.errors import SwiggyAuthenticationRequired
from app.api.memory import router as memory_router
from app.api.purchase_memory import router as purchase_memory_router
from app.commerce.swiggy.models import (
    Address,
    AddressList,
    AddressResolution,
    ConnectionStatus,
    OrderItem,
    OrderList,
    OrderSummary,
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

    async def orders(self, count: int, active_only: bool) -> OrderList:
        assert count == 5
        assert active_only is True
        return OrderList(
            orders=[
                OrderSummary(
                    order_id="IM-1",
                    status="DELIVERED",
                    current_status="DELIVERED",
                    history_status="COMPLETED",
                    created_at=datetime(2026, 11, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 11, 1, tzinfo=UTC),
                    item_count=1,
                    total_paise=15400,
                    order_type="DASH",
                    is_active=False,
                    items=[OrderItem(name="Fortune Oil 1 L", quantity=1)],
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


def test_order_history_is_read_only_and_typed() -> None:
    response = client.get("/api/swiggy/orders?count=5&activeOnly=true")
    assert response.status_code == 200
    assert response.json()["orders"][0]["order_id"] == "IM-1"
    order_paths = {
        path: methods
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/api/swiggy/orders")
    }
    assert order_paths
    assert all(set(methods) <= {"get"} for methods in order_paths.values())


def test_purchase_memory_has_explicit_sync_but_no_purchase_action() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/memory/purchase-history/sync"]) == {"post"}
    assert set(paths["/api/memory/purchase-history"]) == {"delete"}
    assert set(paths["/api/memory/reorder-suggestions"]) == {"get"}
    assert set(paths["/api/memory/reorder-suggestions/{product_id}/dismiss"]) == {"post"}
    assert set(paths["/api/memory/reorder-suggestions/{product_id}/review"]) == {"post"}
    dangerous = ("checkout", "place-order", "place_order", "add-to-cart", "add_to_cart")
    assert not any(token in path for path in paths for token in dangerous)
    included_routers = [
        route.original_router
        for route in app.routes
        if hasattr(route, "original_router")
    ]
    assert included_routers.index(purchase_memory_router) < included_routers.index(
        memory_router
    )


def test_authentication_required_is_401() -> None:
    fake_service.authenticated = False
    try:
        response = client.get("/api/swiggy/addresses")
        assert response.status_code == 401
        assert response.json()["detail"] == "Connect your Swiggy account first."
    finally:
        fake_service.authenticated = True
