from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PriceSnapshot(BaseModel):
    mrp: float = Field(ge=0)
    offer_price: float = Field(ge=0)


class ProductSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["swiggy_instamart", "zepto"] | None = None
    provider_label: str | None = Field(default=None, max_length=80)
    product_id: str = Field(min_length=1, max_length=256)
    sku_id: str = Field(min_length=1, max_length=256)
    spin_id: str | None = Field(default=None, max_length=256)
    name: str = Field(min_length=1, max_length=500)
    brand: str | None = Field(default=None, max_length=200)
    pack_size: str | None = Field(default=None, max_length=120)
    price: PriceSnapshot
    available: bool
    image_url: str | None = Field(default=None, max_length=2048)
    rating_value: str | None = Field(default=None, max_length=40)


class ChatMessageSnapshot(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10_000)
    modality: Literal["text", "voice"] = "text"
    created_at: datetime

    @field_validator("content", mode="before")
    @classmethod
    def strip_content(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if not value.strip():
            raise ValueError("content must not be empty")
        return value.strip()


class ConversationStateUpsert(BaseModel):
    submitted_query: str = Field(default="", max_length=120)
    products: list[ProductSnapshot] = Field(default_factory=list, max_length=100)
    searched: bool = False
    messages: list[ChatMessageSnapshot] = Field(default_factory=list, max_length=200)
    results_after_message_id: str | None = Field(default=None, max_length=128)

    @field_validator("submitted_query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        return value.strip()


class ConversationStateResponse(ConversationStateUpsert):
    id: UUID
    updated_at: datetime
