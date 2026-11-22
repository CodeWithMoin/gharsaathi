from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class TrackingProvider(StrEnum):
    SWIGGY = "swiggy"
    ZEPTO = "zepto"


class TrackingStatus(StrEnum):
    UNKNOWN = "unknown"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    UNAVAILABLE = "unavailable"


TERMINAL_STATUSES = {TrackingStatus.DELIVERED, TrackingStatus.CANCELLED}


class TrackingSnapshot(BaseModel):
    """Normalised result of a *read-only* provider tracking operation."""

    status: TrackingStatus = TrackingStatus.UNKNOWN
    provider_status: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=500)
    eta_at: datetime | None = None
    # Store-reported times (Zepto ``get_order_detail``'s latest shipment). All
    # optional: older saved watches and other providers have none of them.
    placed_at: datetime | None = None
    arrived_at: datetime | None = None
    delivered_at: datetime | None = None
    # The store's own promise when the order was placed, kept after delivery.
    committed_eta_minutes: int | None = Field(default=None, ge=0)
    total_arrival_minutes: int | None = Field(default=None, ge=0)
    # Provider cadence is authoritative.  Do not impose an upper cap that
    # could cause us to reject or shorten a legitimate provider interval.
    poll_interval_seconds: int | None = Field(default=None, ge=1)
    delivered: bool = False
    cancelled: bool = False
    disabled: bool = False
    # The store's own short order code ("Z123"), when its status tool returns one.
    order_code: str | None = Field(default=None, max_length=64)
    # True only when the live provider catalogue exposes an order-cancellation
    # tool. GharSaathi never calls it; this only tells the assistant what exists.
    cancel_supported: bool = False

    @computed_field
    @property
    def took_minutes(self) -> int | None:
        """Placed to delivered, rounded to the nearest minute; only from store times."""

        if self.placed_at is None or self.delivered_at is None:
            return None
        if self.placed_at.utcoffset() is None or self.delivered_at.utcoffset() is None:
            return None
        seconds = (self.delivered_at - self.placed_at).total_seconds()
        return int((seconds + 30) // 60) if seconds >= 0 else None

    @property
    def terminal(self) -> bool:
        return self.delivered or self.cancelled or self.status in TERMINAL_STATUSES

    def fingerprint(self) -> str:
        """Only fields meaningful to a household should create a new event."""

        return "|".join(
            (
                self.status.value,
                (self.provider_status or "").strip().casefold(),
                (self.message or "").strip().casefold(),
                self.eta_at.isoformat() if self.eta_at else "",
                "delivered" if self.delivered else "",
                "cancelled" if self.cancelled else "",
                "disabled" if self.disabled else "",
            )
        )


class TrackingEvent(BaseModel):
    occurred_at: datetime
    snapshot: TrackingSnapshot


NotificationKind = Literal["out_for_delivery", "delivered", "delayed", "cancelled"]


class TrackingNotification(BaseModel):
    """One in-app update worth telling the household about (never push/SMS/calls)."""

    id: str
    kind: NotificationKind
    occurred_at: datetime
    message: str = Field(max_length=200)
    eta_at: datetime | None = None


class OrderWatch(BaseModel):
    """A persisted user-requested tracking subscription for one order."""

    id: str
    user_id: str = Field(min_length=1, max_length=128)
    provider: TrackingProvider
    order_id: str = Field(min_length=1, max_length=160)
    address_id: str | None = Field(default=None, max_length=160)
    active: bool = True
    status: TrackingStatus = TrackingStatus.UNKNOWN
    snapshot: TrackingSnapshot | None = None
    last_fingerprint: str | None = None
    events: list[TrackingEvent] = Field(default_factory=list, max_length=100)
    notifications: list[TrackingNotification] = Field(default_factory=list, max_length=20)
    consecutive_failures: int = Field(default=0, ge=0)
    last_error: str | None = Field(default=None, max_length=300)
    checked_at: datetime | None = None
    # Last successful provider read: when ``snapshot`` was reported. A failed
    # read updates ``checked_at`` only, so stale status stays visible.
    status_at: datetime | None = None
    next_check_at: datetime | None = None
    stopped_at: datetime | None = None
    stop_reason: str | None = Field(default=None, max_length=160)
    created_at: datetime
    updated_at: datetime


class StartTrackingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: TrackingProvider
    order_id: str = Field(min_length=1, max_length=160)
    address_id: str | None = Field(default=None, max_length=160)

    @field_validator("order_id", "address_id")
    @classmethod
    def strip_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class TrackingRead(BaseModel):
    order: OrderWatch
    changed: bool = False
    checked_at: datetime | None = None
    next_check_at: datetime | None = None

    @computed_field
    @property
    def monitor_id(self) -> str:
        """Opaque identifier for URLs and refresh/stop operations."""

        return self.order.id

    @computed_field
    @property
    def live_tracking_supported(self) -> bool:
        return self.order.stop_reason not in {
            "provider_unavailable",
            "provider_tracking_disabled",
        }

    @computed_field
    @property
    def provider_message(self) -> str | None:
        return self.order.last_error
