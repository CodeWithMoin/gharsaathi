from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class StoredConnection(BaseModel):
    client_id: str
    access_token: str | None = Field(default=None, repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: str = "Bearer"
    scope: str | None = None
    expires_at: datetime | None = None


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


class Price(BaseModel):
    mrp: float
    offer_price: float
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
