from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.shopping.models import DisplayedPrice


class PendingAuthorization(BaseModel):
    code_verifier: str = Field(repr=False)
    client_id: str
    created_at: datetime


class StoredConnection(BaseModel):
    client_id: str
    access_token: str | None = Field(default=None, repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: str = "Bearer"
    scope: str | None = None
    expires_at: datetime | None = None
    rate_limited_until: datetime | None = None
    pending_authorizations: dict[str, PendingAuthorization] = Field(
        default_factory=dict,
        repr=False,
    )


class ConnectionStatus(BaseModel):
    connected: bool
    expires_at: datetime | None = None
    mode: Literal["demo", "live"] = "live"


class Address(BaseModel):
    id: str
    address_line: str
    category: str | None = None
    tag: str | None = None


class Pagination(BaseModel):
    page: int = 1
    page_size: int = 10
    total: int = 0
    total_pages: int = 0
    has_more: bool = False


class AddressResolution(BaseModel):
    default_address_id: str | None = None
    resolution_source: str | None = None
    needs_user_clarification: bool = False
    candidate_address_ids: list[str] = Field(default_factory=list)


class AddressList(BaseModel):
    addresses: list[Address]
    pagination: Pagination
    resolution: AddressResolution
    cached: bool = False
    needs_refresh: bool = False
    refreshed_at: datetime | None = None
    # The selected saved address disappeared at the store; the user must pick again.
    selection_removed: bool = False


class Price(DisplayedPrice):
    unit_level_price: str | None = None


class Product(BaseModel):
    product_id: str
    parent_product_id: str | None = None
    sku_id: str
    spin_id: str
    name: str
    brand: str | None = None
    pack_size: str | None = None
    price: Price
    available: bool
    image_url: str | None = None
    rating_value: str | None = None
    rating_count: str | None = None
    delivery_value: str | None = None
    delivery_unit: str | None = None


class ProductSearchResult(BaseModel):
    products: list[Product]
    similar_products: list[Product] = Field(default_factory=list)
    next_offset: str | None = None


class AppliedMemory(BaseModel):
    id: UUID
    memory_type: str
    text: str


class PersonalizedProductSearchResult(ProductSearchResult):
    original_query: str
    provider_query: str
    applied_memories: list[AppliedMemory] = Field(default_factory=list)
    excluded_product_count: int = 0


class OrderItem(BaseModel):
    name: str
    quantity: int = Field(ge=1)
    item_id: str | None = None


class OrderSummary(BaseModel):
    order_id: str
    status: str
    current_status: str
    history_status: str
    status_message: str | None = None
    created_at: datetime
    updated_at: datetime
    estimated_delivery_time: str | None = None
    item_count: int = Field(ge=0)
    total_paise: int = Field(ge=0)
    payment_method: str | None = None
    payment_status: str | None = None
    refund_status: str | None = None
    order_type: str
    is_active: bool
    store_name: str | None = None
    items: list[OrderItem]
    # The provider's order-list id when it differs from ``order_id`` (Zepto).
    lookup_id: str | None = Field(default=None, exclude=True)


class OrderList(BaseModel):
    orders: list[OrderSummary]
    has_more: bool = False
    next_cursor: str | None = Field(default=None, exclude=True)


class ToolDescription(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any]


class OAuthTokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: Literal["Bearer", "bearer"] = "Bearer"
    expires_in: int = Field(gt=0)
    scope: str | None = None
