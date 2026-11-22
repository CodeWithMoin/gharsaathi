"""Browser notification kinds, the payload sent to the service worker, and settings."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Every kind the user can switch off. Each one is a meaningful change to an
# order the user placed, or (rider_called) a delivery partner's call about one. There is deliberately no
# "you usually buy", replenishment or low-stock kind (AGENTS.md non-goals).
PushKind = Literal[
    "order_placed",
    "out_for_delivery",
    "delivered",
    "delayed",
    "cancelled",
    "order_check",
    "payment",
    "checkout_expiring",
    "rider_called",
]
PUSH_KINDS: tuple[PushKind, ...] = (
    "order_placed",
    "out_for_delivery",
    "delivered",
    "delayed",
    "cancelled",
    "order_check",
    "payment",
    "checkout_expiring",
    "rider_called",
)
# "test" is the Settings button; it is never user-toggleable and never deduped.
SendKind = PushKind | Literal["test"]

KIND_LABELS: dict[PushKind, str] = {
    "order_placed": "Order placed",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "delayed": "Running late",
    "cancelled": "Cancelled by the store",
    "order_check": "Unconfirmed order checks",
    "payment": "Payment needs attention",
    "checkout_expiring": "Order card about to expire",
    # Rider assist (beta): the AI answered a delivery partner's forwarded call.
    "rider_called": "A delivery partner called",
}

TITLE_MAX = 48
BODY_MAX = 160
URL_MAX = 200


class PushPayload(BaseModel):
    """What the service worker receives. Short and content-light by construction:
    no address, no amount but the order total, at most two product names."""

    model_config = ConfigDict(extra="forbid")

    kind: SendKind
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    body: str = Field(min_length=1, max_length=BODY_MAX)
    # Same-origin path the notification opens, e.g. "/?order=123".
    url: str = Field(default="/", max_length=URL_MAX)
    # Replaces an earlier notification with the same tag (one per order).
    tag: str | None = Field(default=None, max_length=80)
    # The service worker skips the system notification while a GharSaathi tab
    # is visible and focused; the page already shows the update in-app.
    suppress_when_visible: bool = True

    @field_validator("title", "body")
    @classmethod
    def single_line(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("url")
    @classmethod
    def same_origin_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "\\" in value:
            raise ValueError("url must be a same-origin path")
        return value


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="ignore")

    p256dh: str = Field(min_length=16, max_length=200, pattern=r"^[A-Za-z0-9_\-=]+$")
    auth: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_\-=]+$")


class SubscriptionIn(BaseModel):
    """The browser's ``PushSubscription.toJSON()``."""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=10, max_length=2048)
    keys: PushKeys
    expirationTime: int | None = None


class UnsubscribeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=10, max_length=2048)


class KindSetting(BaseModel):
    id: PushKind
    label: str
    enabled: bool


class NotificationSettings(BaseModel):
    # False when VAPID keys are missing: the UI hides the feature.
    available: bool
    public_key: str | None = None
    kinds: list[KindSetting]
    devices: int = 0
    order_prompt_answered: bool = False


class NotificationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: dict[PushKind, bool] | None = None
    order_prompt_answered: bool | None = None
