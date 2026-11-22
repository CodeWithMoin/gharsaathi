"""Live delivery context for the Sarvam inbound agent's API tool ``get_delivery_context``.

Real delivery partners call from masked numbers, so a cohort record keyed on one
caller number cannot match them. Instead the agent asks this backend during the
call: is an order on its way, and what are the directions? The answer comes only
from GharSaathi's own records — the owner's tracked orders (last provider
reading), the order's saved store address, the directions saved for that
address and the profile's first name. No store is called.

What is never returned: a phone number, an OTP, payment details or whether
anyone is home. Logs carry ``active`` and the requested store only.
"""

from __future__ import annotations

import hmac
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.commerce.swiggy.models import AddressList
from app.core.config import Settings
from app.inbound_delivery.models import ADDRESS_BOOK_KEYS
from app.inbound_delivery.sarvam import NOT_AVAILABLE
from app.rider.safety import redact_secrets
from app.tracking.models import OrderWatch, TrackingStatus

logger = logging.getLogger(__name__)

TOOL_PATH = "/api/sarvam/delivery-context"
TOKEN_HEADER = "X-GharSaathi-Tool-Token"
# Fallback for tool builders that cannot set headers; the access log redacts it.
TOKEN_QUERY = "tool_token"
MIN_TOKEN_LENGTH = 32
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 60.0
# A delivered order still counts for this long: the rider may call from the gate.
DELIVERED_GRACE = timedelta(minutes=15)
# An order that has been "on its way" longer than this is a stale watch, not a delivery.
IN_FLIGHT_WINDOW = timedelta(hours=12)
IN_FLIGHT = frozenset({TrackingStatus.CONFIRMED, TrackingStatus.PREPARING, TrackingStatus.OUT_FOR_DELIVERY})
STATUS_TEXT = {
    TrackingStatus.CONFIRMED: "order placed",
    TrackingStatus.PREPARING: "being packed",
    TrackingStatus.OUT_FOR_DELIVERY: "out for delivery",
    TrackingStatus.DELIVERED: "delivered",
}
IST = timezone(timedelta(hours=5, minutes=30))
MAX_FIELD = 300

ToolProvider = Literal["zepto", "swiggy", "unknown"]
STORE_NAMES = {"zepto": "Zepto", "swiggy": "Swiggy"}

# Any run of 8+ digits (separators allowed) is a phone number here, never sent;
# a 6-digit PIN code stays.
_PHONE = re.compile(r"\+?\d(?:[\s().-]{0,2}\d){7,}")

NO_ORDER_MESSAGE = (
    "No active delivery right now. Say no delivery is expected and offer to connect Moin."
)
UNAVAILABLE_MESSAGE = "Delivery details are unavailable. Offer to connect Moin."


class DeliveryContext(BaseModel):
    """What the agent may use. Strings only (Sarvam tool results feed the prompt)."""

    active: Literal["yes", "no"]
    provider: str
    provider_mismatch: Literal["yes", "no"] = "no"
    order_status: str = NOT_AVAILABLE
    recipient_name: str = NOT_AVAILABLE
    delivery_address: str = NOT_AVAILABLE
    landmark: str = NOT_AVAILABLE
    route_directions: str = NOT_AVAILABLE
    handover_instructions: str = NOT_AVAILABLE
    eta: str = NOT_AVAILABLE
    message_for_agent: str


class WatchList(Protocol):
    def list(self, user_id: str, *, active_only: bool = False) -> list[OrderWatch]: ...


class AddressBook(Protocol):
    async def saved(self, household_id: str, provider: str) -> AddressList | None: ...


class DirectionsReader(Protocol):
    async def directions(self, user_id: str, provider: str, address_id: str) -> Any: ...


class NameReader(Protocol):
    async def read(self, user_id: str) -> Any: ...


class OrderAddresses(Protocol):
    async def address_id(self, user_id: str, provider: str, order_id: str) -> str | None: ...


class LiveContextError(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def normalise_provider(value: object) -> ToolProvider:
    if not isinstance(value, str):
        return "unknown"
    text = value.casefold()
    if "zepto" in text:
        return "zepto"
    if "swiggy" in text or "instamart" in text:
        return "swiggy"
    return "unknown"


def format_time(value: datetime) -> str:
    """``8:26 pm`` in India time."""

    local = (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(IST)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def safe_text(value: object, limit: int = MAX_FIELD) -> str:
    """One line, no phone numbers or OTP-like codes; ``NOT_AVAILABLE`` when empty."""

    if not isinstance(value, str):
        return NOT_AVAILABLE
    text = " ".join(value.split())
    text = _PHONE.sub("", text)
    text = redact_secrets(text)
    text = " ".join(text.split()).strip(" ,;:-")
    if not text:
        return NOT_AVAILABLE
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,;:-") + "…"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _delivered(watch: OrderWatch) -> bool:
    snapshot = watch.snapshot
    return watch.status == TrackingStatus.DELIVERED or bool(snapshot and snapshot.delivered)


def _cancelled(watch: OrderWatch) -> bool:
    snapshot = watch.snapshot
    return watch.status == TrackingStatus.CANCELLED or bool(snapshot and snapshot.cancelled)


def _delivered_at(watch: OrderWatch) -> datetime | None:
    snapshot = watch.snapshot
    for value in (snapshot.delivered_at if snapshot else None, watch.stopped_at, watch.status_at, watch.updated_at):
        if value is not None:
            return _aware(value)
    return None


def active_watches(watches: list[OrderWatch], now: datetime) -> list[OrderWatch]:
    """Orders a rider may be calling about, on-the-way ones first, newest first.

    On its way: placed, packed or out for delivery (the last provider reading),
    still tracked and started within 12 h. Delivered: within the last 15 min.
    Cancelled and anything else (unknown, paused, unavailable) is not active.
    """

    moving: list[OrderWatch] = []
    delivered: list[OrderWatch] = []
    for watch in watches:
        if _cancelled(watch):
            continue
        if _delivered(watch):
            at = _delivered_at(watch)
            if at is not None and timedelta(0) <= now - at <= DELIVERED_GRACE:
                delivered.append(watch)
            continue
        created = _aware(watch.created_at)
        if watch.active and watch.status in IN_FLIGHT and created is not None and now - created <= IN_FLIGHT_WINDOW:
            moving.append(watch)

    def newest(watch: OrderWatch) -> datetime:
        return _aware(watch.updated_at) or now

    return sorted(moving, key=newest, reverse=True) + sorted(delivered, key=newest, reverse=True)


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True


class LiveDeliveryContextService:
    def __init__(
        self,
        settings: Settings,
        watches: WatchList,
        address_book: AddressBook,
        directions: DirectionsReader,
        profiles: NameReader,
        *,
        order_addresses: OrderAddresses | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        limiter: RateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.watches = watches
        self.address_book = address_book
        self.directions = directions
        self.profiles = profiles
        self.order_addresses = order_addresses
        self.clock = clock
        self.limiter = limiter or RateLimiter()

    # Guard -------------------------------------------------------------------

    def _token(self) -> str:
        return self.settings.sarvam_tool_token.get_secret_value().strip()

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.sarvam_live_context_enabled
            and len(self._token()) >= MIN_TOKEN_LENGTH
            and self.settings.sarvam_inbound_owner_user_id.strip()
        )

    def authorise(self, presented: str | None) -> None:
        """404 when off or not set up, 401 on a wrong token, 429 over the rate limit."""

        if not self.enabled:
            raise LiveContextError(404, "not_found")
        expected = self._token().encode()
        if not presented or not hmac.compare_digest(presented.strip().encode(), expected):
            logger.info("event=sarvam_live_context result=unauthorized")
            raise LiveContextError(401, "unauthorized")
        if not self.limiter.allow():
            logger.info("event=sarvam_live_context result=rate_limited")
            raise LiveContextError(429, "rate_limited")

    # Context -----------------------------------------------------------------

    async def context(self, provider: object) -> DeliveryContext:
        requested = normalise_provider(provider)
        owner = self.settings.sarvam_inbound_owner_user_id.strip()
        now = self.clock()
        try:
            candidates = active_watches(self.watches.list(owner), now)
        except Exception as exc:  # noqa: BLE001 - a broken cache must not leak details
            logger.warning("event=sarvam_live_context result=tracking_unreadable error=%s", type(exc).__name__)
            candidates = []
        if not candidates:
            self._log("no", requested, "no")
            return DeliveryContext(active="no", provider=requested, message_for_agent=NO_ORDER_MESSAGE)

        watch = next((item for item in candidates if item.provider.value == requested), candidates[0])
        store = watch.provider.value
        mismatch = "yes" if requested != "unknown" and requested != store else "no"
        context = await self._details(owner, watch, now)
        context.provider = store
        context.provider_mismatch = mismatch  # type: ignore[assignment]
        context.message_for_agent = _message(store, mismatch == "yes", _delivered(watch), context)
        self._log("yes", requested, mismatch)
        return context

    async def _details(self, owner: str, watch: OrderWatch, now: datetime) -> DeliveryContext:
        store = watch.provider.value
        delivered = _delivered(watch)
        address_id, address_text = await self._address(owner, store, watch)
        saved = None
        if address_id is not None:
            try:
                saved = await self.directions.directions(owner, store, address_id)
            except Exception as exc:  # noqa: BLE001 - fall back to the address alone
                logger.warning("event=sarvam_live_context result=directions_unreadable error=%s", type(exc).__name__)
        landmark = route = handover = NOT_AVAILABLE
        if saved is not None:
            landmark = safe_text(getattr(saved, "landmark", None))
            steps = [safe_text(step, 80) for step in getattr(saved, "route_directions", None) or [] if isinstance(step, str)]
            steps = [step for step in steps if step != NOT_AVAILABLE]
            gate = getattr(saved, "gate_note", None)
            if isinstance(gate, str) and safe_text(gate, 120) != NOT_AVAILABLE:
                steps.append(f"Gate: {safe_text(gate, 120)}")
            if steps:
                route = safe_text("; ".join(f"{index}) {step}" for index, step in enumerate(steps, 1)), 700)
            handover = safe_text(getattr(saved, "handover_instructions", None), 100)
        name = NOT_AVAILABLE
        try:
            profile = await self.profiles.read(owner)
            name = safe_text(getattr(profile, "first_name", None), 40)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=sarvam_live_context result=profile_unreadable error=%s", type(exc).__name__)
        eta = NOT_AVAILABLE
        snapshot = watch.snapshot
        if not delivered and snapshot is not None and snapshot.eta_at is not None:
            eta = format_time(snapshot.eta_at)
        status = TrackingStatus.DELIVERED if delivered else watch.status
        return DeliveryContext(
            active="yes",
            provider=store,
            order_status=STATUS_TEXT.get(status, NOT_AVAILABLE),
            recipient_name=name,
            delivery_address=safe_text(address_text),
            landmark=landmark,
            route_directions=route,
            handover_instructions=handover,
            eta=eta,
            message_for_agent="",
        )

    async def _address(self, owner: str, store: str, watch: OrderWatch) -> tuple[str | None, str | None]:
        """(id, text) of the order's address; the selected store address when the order has none on record."""

        try:
            addresses = await self.address_book.saved(owner, ADDRESS_BOOK_KEYS[store])
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=sarvam_live_context result=addresses_unreadable error=%s", type(exc).__name__)
            addresses = None
        address_id = watch.address_id
        if address_id is None and self.order_addresses is not None:
            try:
                address_id = await self.order_addresses.address_id(owner, store, watch.order_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("event=sarvam_live_context result=order_address_unreadable error=%s", type(exc).__name__)
        if address_id is None and addresses is not None:
            address_id = addresses.resolution.default_address_id
        if address_id is None:
            return None, None
        match = next((item for item in (addresses.addresses if addresses else []) if item.id == address_id), None)
        return address_id, (match.address_line if match is not None else None)

    @staticmethod
    def _log(active: str, provider: str, mismatch: str) -> None:
        logger.info("event=sarvam_live_context active=%s provider=%s mismatch=%s", active, provider, mismatch)


def _message(store: str, mismatch: bool, delivered: bool, context: DeliveryContext) -> str:
    name = STORE_NAMES[store]
    if delivered:
        lead = f"A {name} order was delivered a few minutes ago; help the caller find the address if they are still nearby."
    else:
        lead = f"A {name} order is on its way; guide the caller with these details only."
    if mismatch:
        lead += f" The caller named another store; the active order is from {name}, so confirm the store before guiding."
    if context.delivery_address == NOT_AVAILABLE:
        lead += " The address is not on record; ask the caller to follow the address in their app, or offer Moin."
    elif context.route_directions == NOT_AVAILABLE and context.landmark == NOT_AVAILABLE:
        lead += " No saved directions; share the address, or offer Moin."
    return lead + " Never share phone numbers, OTPs or payment details."


class DatabaseOrderAddresses:
    """The delivery address id of a GharSaathi-placed Zepto order (its basket's address)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def address_id(self, user_id: str, provider: str, order_id: str) -> str | None:
        if provider != "zepto" or not order_id:
            return None
        from app.database.models import ShoppingBasketRecord, ZeptoCheckoutAttemptRecord

        async with self.sessions() as session:
            value = await session.scalar(
                select(ShoppingBasketRecord.address_id)
                .join(ZeptoCheckoutAttemptRecord, ZeptoCheckoutAttemptRecord.basket_id == ShoppingBasketRecord.id)
                .where(
                    ZeptoCheckoutAttemptRecord.household_id == user_id,
                    ZeptoCheckoutAttemptRecord.provider_order_id == order_id,
                )
                .limit(1)
            )
        return value if isinstance(value, str) and value else None


def tool_url(base_url: str) -> str | None:
    base = base_url.strip().rstrip("/")
    if not base.startswith("https://"):
        return None
    return f"{base}{TOOL_PATH}"


def tool_spec(base_url: str) -> dict[str, Any]:
    """The Sarvam API tool definition (the token is a placeholder, never the value)."""

    return {
        "name": "get_delivery_context",
        "tool_type": "http",
        "description": (
            "Call this first when the caller says they are from Zepto, Swiggy or a delivery. "
            "Returns whether an order is on its way to Moin and the delivery directions."
        ),
        "lifecycle": "run",
        "method": "POST",
        "url": tool_url(base_url) or f"<public origin>{TOOL_PATH}",
        "headers": [
            {"name": TOKEN_HEADER, "type": "static", "value": "<SARVAM_TOOL_TOKEN>"},
            {"name": "Content-Type", "type": "static", "value": "application/json"},
        ],
        "body_params": [
            {
                "name": "provider",
                "type": "llm_prompt",
                "data_type": "Text",
                "required": False,
                "description": "The store the caller named: one of zepto, swiggy or unknown.",
            },
            {
                "name": "rider_location_hint",
                "type": "llm_prompt",
                "data_type": "Text",
                "required": False,
                "description": "Optional: where the caller says they are now, in a few words.",
            },
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m app.inbound_delivery.live_context [tool-url|tool-spec] [base_url]``."""

    import json
    import sys

    from app.core.config import get_settings

    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "tool-url"
    base = args[1] if len(args) > 1 else get_settings().rider_public_base_url
    if command == "tool-spec":
        print(json.dumps(tool_spec(base), indent=2))
        return 0
    url = tool_url(base)
    if url is None:
        print("Set RIDER_PUBLIC_BASE_URL to the public https origin (a tunnel or the AWS stack).", file=sys.stderr)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
