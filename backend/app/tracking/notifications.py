"""Which delivery changes are worth an in-app update.

Only four changes count: out for delivery, delivered, cancelled, and a delay
(the provider's ETA moved later by at least ``DELAY_THRESHOLD``). Everything
else (a first reading, packing, small ETA drift, a reworded message) stays
silent. Updates are shown in the app (a toast and the chat status card) and,
for people who turned them on, sent as browser notifications by
``app/notifications`` (``OrderTrackingService.push``). Never SMS or calls.
"""

from __future__ import annotations

from datetime import timedelta

from app.tracking.models import NotificationKind, TrackingSnapshot, TrackingStatus

DELAY_THRESHOLD = timedelta(minutes=10)

MESSAGES: dict[NotificationKind, str] = {
    "out_for_delivery": "Your {store} order is out for delivery.",
    "delivered": "Your {store} order was delivered.",
    "delayed": "Your {store} order is running late. {store} moved the delivery time.",
    "cancelled": "Your {store} order was cancelled.",
}


def _effective(snapshot: TrackingSnapshot) -> TrackingStatus:
    if snapshot.delivered:
        return TrackingStatus.DELIVERED
    if snapshot.cancelled:
        return TrackingStatus.CANCELLED
    return snapshot.status


def meaningful_update(
    previous: TrackingSnapshot | None, current: TrackingSnapshot
) -> NotificationKind | None:
    """The one household-relevant change between two provider readings, if any."""

    if previous is None:
        return None
    before, after = _effective(previous), _effective(current)
    if after != before:
        if after == TrackingStatus.DELIVERED:
            return "delivered"
        if after == TrackingStatus.CANCELLED:
            return "cancelled"
        if after == TrackingStatus.OUT_FOR_DELIVERY:
            return "out_for_delivery"
    if after in {TrackingStatus.DELIVERED, TrackingStatus.CANCELLED}:
        return None
    if (
        previous.eta_at is not None
        and current.eta_at is not None
        and current.eta_at - previous.eta_at >= DELAY_THRESHOLD
    ):
        return "delayed"
    return None


def notification_message(kind: NotificationKind, store: str) -> str:
    return MESSAGES[kind].format(store=store)
