"""The text of every browser notification. Code-authored, short and private.

Rules (tested in tests/unit/test_push_messages.py): no delivery address, no
amount other than the order total, at most two product names, no provider
free text, and nothing about stock or replenishment.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.notifications.models import TITLE_MAX, PushPayload
from app.shopping.money import format_rupees

INDIA = ZoneInfo("Asia/Kolkata")
NAME_MAX = 28


def clock_time(value: datetime) -> str:
    """``8:26 am`` in India time."""

    local = value.astimezone(INDIA)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def item_phrase(names: Sequence[str]) -> str:
    """``A``, ``A and B``, ``A, B and 3 more``: never more than two names."""

    cleaned = [_short(name) for name in names if name and name.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{cleaned[0]}, {cleaned[1]} and {len(cleaned) - 2} more"


def _short(name: str) -> str:
    text = " ".join(name.split())
    return text if len(text) <= NAME_MAX else text[: NAME_MAX - 1].rstrip() + "…"


def order_url(order_id: str | None) -> str:
    return f"/?order={quote(order_id, safe='')}" if order_id else "/"


def _tag(order_id: str | None) -> str | None:
    return f"order:{order_id}"[:80] if order_id else None


def order_placed(
    store: str, order_id: str, payable_paise: int, item_names: Sequence[str] = (), *, earlier: bool = False
) -> PushPayload:
    items = item_phrase(item_names)
    if earlier:
        body = f"{store} confirmed your earlier order. Total {format_rupees(payable_paise)}."
    elif items:
        body = f"{store} confirmed your order: {items}. Total {format_rupees(payable_paise)}."
    else:
        body = f"{store} confirmed your order. Total {format_rupees(payable_paise)}."
    return PushPayload(
        kind="order_placed",
        title="Order confirmed" if earlier else "Order placed",
        body=body,
        url=order_url(order_id),
        tag=_tag(order_id),
    )


def out_for_delivery(store: str, order_id: str, eta_at: datetime | None) -> PushPayload:
    arriving = f" Arriving by {clock_time(eta_at)}." if eta_at else ""
    return PushPayload(
        kind="out_for_delivery",
        title="Out for delivery",
        body=f"Your {store} order is on the way.{arriving}",
        url=order_url(order_id),
        tag=_tag(order_id),
    )


def delivered(store: str, order_id: str, delivered_at: datetime | None) -> PushPayload:
    when = f" at {clock_time(delivered_at)}" if delivered_at else ""
    return PushPayload(
        kind="delivered",
        title="Delivered",
        body=f"Your {store} order was delivered{when}.",
        url=order_url(order_id),
        tag=_tag(order_id),
    )


def delayed(store: str, order_id: str, eta_at: datetime | None) -> PushPayload:
    new_time = f" to about {clock_time(eta_at)}" if eta_at else ""
    return PushPayload(
        kind="delayed",
        title="Running late",
        body=f"{store} moved your delivery{new_time}.",
        url=order_url(order_id),
        tag=_tag(order_id),
    )


def cancelled(store: str, order_id: str) -> PushPayload:
    return PushPayload(
        kind="cancelled",
        title="Order cancelled",
        body=f"Your {store} order was cancelled. Open the {store} app for details.",
        url=order_url(order_id),
        tag=_tag(order_id),
    )


def order_still_unknown(store: str, payable_paise: int) -> PushPayload:
    return PushPayload(
        kind="order_check",
        title="Order not confirmed yet",
        body=(
            f"{store} hasn't confirmed your order ({format_rupees(payable_paise)}). "
            f"Check the {store} app before ordering again."
        ),
        url="/",
    )


def payment_needed(store: str, payable_paise: int) -> PushPayload:
    return PushPayload(
        kind="payment",
        title="Finish your payment",
        body=f"Approve {format_rupees(payable_paise)} for your {store} order, then check the status in GharSaathi.",
        url="/",
    )


def payment_unclear(store: str) -> PushPayload:
    return PushPayload(
        kind="payment",
        title="Check your payment",
        body=f"{store} hasn't confirmed your payment yet. Open GharSaathi to check.",
        url="/",
    )


def payment_failed(store: str) -> PushPayload:
    return PushPayload(
        kind="payment",
        title="Payment failed",
        body=f"Your {store} payment didn't go through, so the order was not placed.",
        url="/",
    )


def paid_order_failed(store: str) -> PushPayload:
    return PushPayload(
        kind="payment",
        title="Order not created",
        body=f"{store} took the payment but didn't create the order. Open GharSaathi for next steps.",
        url="/",
    )


def checkout_expiring(store: str) -> PushPayload:
    return PushPayload(
        kind="checkout_expiring",
        title="Your order card expires soon",
        body=f"Your {store} order isn't placed yet. Open GharSaathi and tap Place order within a minute.",
        url="/",
    )


RIDER_QUOTE_MAX = 60


def rider_called(
    started_at: datetime, rider_said: str | None, outcome_text: str, *, urgent: bool = False, call_id: str | None = None
) -> PushPayload:
    """Rider assist: one line, the time, at most a short quote of what the rider asked.

    Never an OTP-like code (hidden), never the delivery address or notes: only the
    rider's own words, cut to one short line.
    """

    from app.rider.safety import redact_store_text

    quote = ""
    if rider_said:
        said = redact_store_text(" ".join(rider_said.split()))
        if len(said) > RIDER_QUOTE_MAX:
            said = said[: RIDER_QUOTE_MAX - 1].rstrip() + "…"
        quote = f" · ‘{said}’"
    return PushPayload(
        kind="rider_called",
        title="A rider is waiting" if urgent else "Rider called",
        body=f"{clock_time(started_at)}{quote} — {outcome_text}."[:160],
        url="/?rider_call=" + quote_url(call_id) if call_id else "/",
        tag=f"rider:{call_id}"[:80] if call_id else None,
        suppress_when_visible=True,
    )


def quote_url(value: str) -> str:
    return quote(value, safe="")


def inbound_rider_called(started_at: datetime, headline: str, outcome_text: str, *, call_id: str) -> PushPayload:
    """Sarvam inbound line: who called, when, and the outcome. Code-authored copy only:
    never the agent's summary, the delivery address or the caller's number."""

    return PushPayload(
        kind="rider_called",
        title=headline[:TITLE_MAX],
        body=f"{clock_time(started_at)} — {outcome_text}."[:160],
        url="/?inbound_call=" + quote_url(call_id),
        tag=f"inbound:{call_id}"[:80],
        suppress_when_visible=True,
    )



def test_message() -> PushPayload:
    return PushPayload(
        kind="test",
        title="GharSaathi notifications are on",
        body="This is a test. You'll get order updates like this.",
        url="/",
        suppress_when_visible=False,
    )
