from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.shopping.models import DisplayedPrice

# A long voice session produces many spoken turns, and a single turn can run long.
MAX_CHAT_MESSAGES = 1_000
MAX_CHAT_MESSAGE_CHARS = 20_000


class PriceSnapshot(DisplayedPrice):
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
    # Household annotations from a voice/typed search, kept so a reopened chat
    # shows the same "best pick first" order and badge.
    remembered: str | None = Field(default=None, max_length=40)
    preferred: bool | None = None
    avoided: bool | None = None
    # Card number from the assistant's search, the same one the model refers to.
    position: int | None = Field(default=None, ge=1, le=50)


class ChatMessageSnapshot(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS)
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


MAX_TIMELINE_SUMMARIES = 50

StoreName = Literal["swiggy_instamart", "zepto"]


class TimelineCardSnapshot(BaseModel):
    """A basket card placed after a chat message, ordered by ``seq``."""

    provider: StoreName
    after_message_id: str | None = Field(default=None, max_length=128)
    seq: int = Field(ge=0)


class TimelineSummarySnapshot(TimelineCardSnapshot):
    """A collapsed, non-interactive record of an earlier basket card."""

    stage: Literal["updated", "bill_ready", "bill_approved", "ordered"]
    item_count: int | None = Field(default=None, ge=0)
    item_total_paise: int | None = Field(default=None, ge=0)


MAX_TIMELINE_ORDERS = 10

OrderCardStatus = Literal[
    "unknown", "confirmed", "preparing", "out_for_delivery", "delivered", "cancelled", "paused", "unavailable"
]


class TimelineOrderSnapshot(BaseModel):
    """A placed order's live status card, placed after a chat message by ``seq``.

    Display state only: the order and its status come from the provider via the
    tracking monitor, never from this snapshot.
    """

    model_config = ConfigDict(extra="ignore")

    provider: Literal["zepto"]
    order_id: str = Field(min_length=1, max_length=160)
    monitor_id: str | None = Field(default=None, max_length=64)
    store: str = Field(default="Zepto", max_length=40)
    after_message_id: str | None = Field(default=None, max_length=128)
    seq: int = Field(ge=0)
    item_count: int | None = Field(default=None, ge=0)
    payable: str | None = Field(default=None, max_length=40)
    status: OrderCardStatus = "confirmed"
    provider_status: str | None = Field(default=None, max_length=160)
    eta_at: datetime | None = None
    checked_at: datetime | None = None
    active: bool = True
    last_notification_id: str | None = Field(default=None, max_length=64)


class ChatTimelineSnapshot(BaseModel):
    seq: int = Field(default=0, ge=0)
    results_seq: int = Field(default=0, ge=0)
    live: list[TimelineCardSnapshot] = Field(default_factory=list, max_length=2)
    summaries: list[TimelineSummarySnapshot] = Field(
        default_factory=list, max_length=MAX_TIMELINE_SUMMARIES
    )
    orders: list[TimelineOrderSnapshot] = Field(default_factory=list, max_length=MAX_TIMELINE_ORDERS)

    @field_validator("live")
    @classmethod
    def one_live_card_per_store(cls, value: list[TimelineCardSnapshot]) -> list[TimelineCardSnapshot]:
        if len({card.provider for card in value}) != len(value):
            raise ValueError("only one live basket card per store")
        return value


MAX_RESULT_GROUPS = 20
MAX_PRODUCTS_PER_GROUP = 50


class ResultGroupSnapshot(BaseModel):
    """One searched item's results, placed after a chat message by ``seq``.

    ``item_key``/``item_label`` match the voice ``tool.results`` event fields.
    """

    model_config = ConfigDict(extra="ignore")

    item_key: str = Field(min_length=1, max_length=120)
    item_label: str = Field(min_length=1, max_length=120)
    query: str = Field(default="", max_length=120)
    products: list[ProductSnapshot] = Field(default_factory=list, max_length=MAX_PRODUCTS_PER_GROUP)
    after_message_id: str | None = Field(default=None, max_length=128)
    seq: int = Field(default=0, ge=0)


class ConversationStateUpsert(BaseModel):
    submitted_query: str = Field(default="", max_length=120)
    products: list[ProductSnapshot] = Field(default_factory=list, max_length=100)
    searched: bool = False
    messages: list[ChatMessageSnapshot] = Field(default_factory=list, max_length=MAX_CHAT_MESSAGES)
    results_after_message_id: str | None = Field(default=None, max_length=128)
    timeline: ChatTimelineSnapshot = Field(default_factory=ChatTimelineSnapshot)
    # One entry per searched item; chats saved before this field load with [].
    result_groups: list[ResultGroupSnapshot] = Field(default_factory=list, max_length=MAX_RESULT_GROUPS)

    @field_validator("result_groups")
    @classmethod
    def one_group_per_item(cls, value: list[ResultGroupSnapshot]) -> list[ResultGroupSnapshot]:
        # The latest group for an item wins, as on screen; a duplicate must not reject the save.
        latest = {group.item_key: group for group in value}
        return [group for group in value if latest[group.item_key] is group]

    @field_validator("submitted_query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        return value.strip()


class ConversationStateResponse(ConversationStateUpsert):
    id: UUID
    updated_at: datetime


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    preview: str
    updated_at: datetime
    is_active: bool
    # Messages with text, and up to five distinct items searched in the chat.
    message_count: int = 0
    items: list[str] = Field(default_factory=list, max_length=5)
