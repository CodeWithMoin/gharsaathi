"""Read-only order status tools for voice and typed chat.

``get_order_status`` answers "mera order kahan hai / kitna time aur / order
aaya?" from this household's own tracking monitor (the one started when the
user tapped Place order, or one started in Order tracking). The monitor is
refreshed only when the provider's polling interval allows, so asking again
never adds provider calls. When the household has no monitor, the latest Zepto
order-history status is used and marked as such. ``list_recent_orders`` lists
up to five recent Zepto orders from Zepto's order history.

Nothing here can cancel, change or place an order. Cancellation is not in
Zepto's documented tools; ``cancel_supported`` only reports whether the live
catalogue exposes such a tool, and ``cancel_here`` is always false.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.commerce.zepto.errors import ZeptoAuthenticationRequired, ZeptoConfigurationError
from app.shopping.money import format_rupees, money_fields
from app.shopping.reconciliation import CheckoutReconciler, ReconcileOutcome
from app.tracking.adapters import normalise_status
from app.tracking.cancellation import order_cancel_tool
from app.tracking.errors import TrackingError
from app.tracking.models import OrderWatch, TrackingProvider, TrackingStatus
from app.tracking.service import OrderTrackingService
from app.voice.tools import ToolOutcome

logger = logging.getLogger(__name__)

GET_ORDER_STATUS = "get_order_status"
LIST_RECENT_ORDERS = "list_recent_orders"
MAX_RECENT_ORDERS = 5
# A non-final status older than this is reported with when it was last updated.
STALE_AFTER = timedelta(minutes=5)

STORE_LABELS = {TrackingProvider.ZEPTO: "Zepto", TrackingProvider.SWIGGY: "Swiggy Instamart"}
STATUS_LABELS = {
    TrackingStatus.CONFIRMED: "Order placed",
    TrackingStatus.PREPARING: "Being packed",
    TrackingStatus.OUT_FOR_DELIVERY: "Out for delivery",
    TrackingStatus.DELIVERED: "Delivered",
    TrackingStatus.CANCELLED: "Cancelled",
    TrackingStatus.PAUSED: "Tracking paused",
    TrackingStatus.UNAVAILABLE: "Live status unavailable",
}
FINAL = {TrackingStatus.DELIVERED, TrackingStatus.CANCELLED}

CANCEL_NOTE = (
    "Cancelling is not possible from GharSaathi. If the user asks, say it cannot be done "
    "here and to open the order in the Zepto app and tap Cancel quickly, before it is packed."
)
STATUS_NOTE = (
    "Answer in one line from these fields only: order_status_label (or provider_status) and, "
    "when eta_minutes is present, that the store estimates about that many minutes. Never "
    "guess a status or a time. If stale is true, say when it was last updated "
    "(last_updated_minutes_ago). " + CANCEL_NOTE
)
HISTORY_NOTE = (
    "No live tracking for this order: this is the status in the store's order history. "
    "Say it in one line; there is no time estimate. " + CANCEL_NOTE
)

ORDER_STATUS_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": GET_ORDER_STATUS,
        "description": (
            "Read the latest store-reported status of one of this household's orders: where it "
            "is, the store's delivery estimate and when it was last updated. Use for 'mera order "
            "kahan hai', 'kitna time aur', 'order aaya?', and before answering a cancel request. "
            "Read-only: it never cancels, changes or places an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_ref": {
                    "type": "string",
                    "description": (
                        "'latest' for the most recent order, or an order id or store order code "
                        "the user or an earlier tool result gave (for example 'Z123')."
                    ),
                }
            },
            "required": ["order_ref"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": LIST_RECENT_ORDERS,
        "description": (
            "List this household's recent Zepto orders (newest first) from Zepto's order "
            "history: code, status, items and total. Use for 'pichla order kya tha?'. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many orders, 1 to 5. Use 3 unless the user asked for more.",
                }
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

ORDER_STATUS_TOOL_NAMES = frozenset(tool["name"] for tool in ORDER_STATUS_TOOL_DEFINITIONS)


class OrderHistoryReader(Protocol):
    """Zepto's read-only order history and live tool catalogue (ZeptoService)."""

    async def order_history(self, *, limit: int = 10, page_number: int = 1) -> Any: ...

    async def list_tools(self) -> list[Any]: ...


class StatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_ref: str = Field(min_length=1, max_length=160)

    @field_validator("order_ref")
    @classmethod
    def strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class RecentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=1)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


class OrderStatusToolExecutor:
    def __init__(
        self,
        tracking: OrderTrackingService | None,
        history: OrderHistoryReader | None,
        household_id: str,
        *,
        now: Callable[[], datetime] = utc_now,
        checkouts: CheckoutReconciler | None = None,
        on_reconciled: Callable[[ReconcileOutcome], Awaitable[None]] | None = None,
    ) -> None:
        self.tracking = tracking
        self.history = history
        self.household_id = household_id
        self.now = now
        # Unknown-outcome checkouts are settled from read-only order history first.
        self.checkouts = checkouts
        self.on_reconciled = on_reconciled

    async def execute(self, name: str, raw_arguments: str) -> ToolOutcome:
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return _error("Invalid arguments.")
        try:
            if name == GET_ORDER_STATUS:
                return await self._status(StatusArguments.model_validate(arguments))
            if name == LIST_RECENT_ORDERS:
                return await self._recent(RecentArguments.model_validate(arguments))
        except ValidationError:
            return _error("Invalid arguments for this tool.")
        return _error(f"Unknown tool {name}.")

    async def _status(self, arguments: StatusArguments) -> ToolOutcome:
        reconciled: ReconcileOutcome | None = None
        if self.checkouts is not None and arguments.order_ref.strip().lstrip("#").strip().casefold() in {"", "latest"}:
            pending = await self.checkouts.pending(self.household_id)
            if pending:
                reconciled = await self.checkouts.reconcile(pending[0].id, household_id=self.household_id)
                if not reconciled.placed:
                    return ToolOutcome(model_output=_not_confirmed(reconciled))
                if reconciled.newly_reconciled and self.on_reconciled is not None:
                    try:
                        await self.on_reconciled(reconciled)
                    except Exception as exc:  # noqa: BLE001 - the answer still stands
                        logger.info("event=order_reconciled_notify_failed error=%s", type(exc).__name__)
        reference = (
            reconciled.order_id
            if reconciled is not None and reconciled.order_id
            else arguments.order_ref
        )
        watch = self.tracking.find(self.household_id, reference) if self.tracking else None
        if watch is not None:
            output = await self._from_watch(watch)
            if reconciled is not None:
                output["confirmed_via"] = "order_history"
                if reconciled.order_code and "order_code" not in output:
                    output["order_code"] = reconciled.order_code
                code = output.get("order_code") or watch.order_id
                output["note"] = (
                    "Zepto's order history now confirms this order (earlier it was unconfirmed). "
                    f"First say it is placed, with order code #{code}; then the status. "
                    + output["note"]
                )
            return ToolOutcome(model_output=output)
        return await self._from_history(reference)

    async def _from_watch(self, watch: OrderWatch) -> dict[str, Any]:
        assert self.tracking is not None
        try:
            # Cadence-protected: inside the provider's interval this reads the saved status.
            watch = (await self.tracking.refresh(self.household_id, watch.id)).order
        except TrackingError as exc:
            logger.info("event=order_status_refresh_failed error=%s", type(exc).__name__)
        now = _utc(self.now())
        snapshot = watch.snapshot
        status = watch.status
        if snapshot is not None and status in {TrackingStatus.PAUSED, TrackingStatus.UNAVAILABLE}:
            # Tracking stopped on a provider error: report the last store-reported status.
            status = (
                TrackingStatus.DELIVERED
                if snapshot.delivered
                else TrackingStatus.CANCELLED
                if snapshot.cancelled
                else snapshot.status
            )
        output: dict[str, Any] = {
            "status": "ok",
            "source": "live_tracking",
            "store": STORE_LABELS[watch.provider],
            "order_ref": watch.order_id,
            # Only provider-confirmed orders are ever tracked.
            "order_confirmed": True,
        }
        if snapshot and snapshot.order_code:
            output["order_code"] = snapshot.order_code
        if snapshot is None or (status == TrackingStatus.UNKNOWN and not snapshot.provider_status):
            output["order_status"] = "unknown"
            output["message"] = "The store has not reported a status for this order yet."
        else:
            output["order_status"] = status.value
            label = STATUS_LABELS.get(status) or snapshot.provider_status
            if label:
                output["order_status_label"] = label
            if snapshot.provider_status:
                output["provider_status"] = snapshot.provider_status
            if snapshot.message:
                output["provider_message"] = snapshot.message
            if snapshot.eta_at is not None and status not in FINAL:
                seconds = (_utc(snapshot.eta_at) - now).total_seconds()
                if seconds > 0:
                    output["eta_minutes"] = max(1, math.ceil(seconds / 60))
                else:
                    output["eta_passed"] = True
        reported_at = watch.status_at or (watch.checked_at if watch.last_error is None else None)
        if reported_at is not None:
            checked = _utc(reported_at)
            output["last_updated_at"] = checked.isoformat()
            output["last_updated_minutes_ago"] = max(0, int((now - checked).total_seconds() // 60))
            output["stale"] = status not in FINAL and now - checked > STALE_AFTER
        else:
            output["stale"] = True
        if not watch.active and status not in FINAL:
            output["tracking"] = "stopped"
            if watch.last_error:
                output["tracking_message"] = watch.last_error
        else:
            output["tracking"] = "active" if watch.active else "finished"
        output["cancel_here"] = False
        output["cancel_supported"] = bool(snapshot and snapshot.cancel_supported)
        output["note"] = STATUS_NOTE
        return output

    async def _from_history(self, order_ref: str) -> ToolOutcome:
        if self.history is None:
            return _no_order()
        try:
            history = await self.history.order_history(limit=MAX_RECENT_ORDERS, page_number=1)
        except ZeptoAuthenticationRequired:
            return _not_connected()
        except ZeptoConfigurationError:
            return _no_order()
        except Exception as exc:  # noqa: BLE001 - a provider read failure is reported, not guessed
            logger.info("event=order_history_read_failed error=%s", type(exc).__name__)
            return _error("Zepto's order status could not be read right now. Nothing was changed.")
        orders = list(history.orders)
        reference = order_ref.strip().lstrip("#").strip().casefold()
        if reference in {"", "latest"}:
            order = orders[0] if orders else None
        else:
            order = next(
                (item for item in orders if reference in {item.id.casefold(), item.code.casefold()}),
                None,
            )
        if order is None:
            return _no_order()
        status = normalise_status(order.formatted_status)
        output: dict[str, Any] = {
            "status": "ok",
            "source": "order_history",
            "store": "Zepto",
            "order_ref": order.id,
            "order_code": order.code,
            "order_status": status.value,
            "order_status_label": STATUS_LABELS.get(status) or order.formatted_status,
            "provider_status": order.formatted_status,
            "tracking": "none",
            "cancel_here": False,
            "cancel_supported": await self._cancel_supported(),
            "note": HISTORY_NOTE,
        }
        return ToolOutcome(model_output=output)

    async def _cancel_supported(self) -> bool:
        if self.history is None:
            return False
        try:
            return order_cancel_tool(await self.history.list_tools()) is not None
        except Exception:  # noqa: BLE001 - unknown means not supported
            return False

    async def _recent(self, arguments: RecentArguments) -> ToolOutcome:
        if self.history is None:
            return _no_order()
        limit = min(arguments.limit, MAX_RECENT_ORDERS)
        try:
            history = await self.history.order_history(limit=limit, page_number=1)
        except ZeptoAuthenticationRequired:
            return _not_connected()
        except ZeptoConfigurationError:
            history = None
        except Exception as exc:  # noqa: BLE001
            logger.info("event=order_history_read_failed error=%s", type(exc).__name__)
            return _error("Zepto's order history could not be read right now.")
        orders = list(history.orders)[:limit] if history is not None else []
        if not orders:
            return ToolOutcome(
                model_output={
                    "status": "no_orders",
                    "store": "Zepto",
                    "orders": [],
                    "message": "No Zepto orders were found in the order history.",
                }
            )
        return ToolOutcome(
            model_output={
                "status": "ok",
                "store": "Zepto",
                "orders": [
                    {
                        "order_ref": order.id,
                        "order_code": order.code,
                        "order_status": order.formatted_status,
                        "item_count": order.item_quantity_count,
                        "items": [
                            {"name": product.name, "quantity": product.count}
                            for product in order.products[:5]
                        ],
                        "total_paise": order.grand_total_paise,
                        "total": format_rupees(order.grand_total_paise),
                    }
                    for order in orders
                ],
                "note": (
                    "Newest first. Name the newest order's main items and total in one short "
                    "line; list more only if the user asked."
                ),
            }
        )


NOT_CONFIRMED_NOTE = (
    "This order is NOT confirmed. Say exactly that in one line: the store has not confirmed "
    "it yet, and offer 'Check with Zepto' on the order card or the Zepto app. Never say it "
    "was placed, confirmed, paid or on its way, and do not prepare it again."
)


def _not_confirmed(outcome: ReconcileOutcome) -> dict[str, Any]:
    attempt = outcome.attempt
    output: dict[str, Any] = {
        "status": "not_confirmed",
        "store": "Zepto",
        "order_confirmed": False,
        "check": outcome.check,
        "note": NOT_CONFIRMED_NOTE,
    }
    if attempt is not None:
        output.update(money_fields("payable", attempt.payable_paise))
        if attempt.status.value == "awaiting_payment":
            output["message"] = (
                "Zepto's order history shows this order, but its payment is not confirmed; "
                "ask the user to tap \"I've paid, check status\" on the card."
            )
    if outcome.check == "not_found":
        output["message"] = "Zepto's order history shows no matching order yet."
    elif outcome.check == "ambiguous":
        output["candidates"] = [f"#{code}" for code in outcome.candidates[:3]]
        output["message"] = "Several Zepto orders could match; GharSaathi will not guess which."
    elif outcome.check == "unavailable":
        output["message"] = "Zepto's order history could not be read right now."
    return output


def _no_order() -> ToolOutcome:
    return ToolOutcome(
        model_output={
            "status": "no_order",
            "message": (
                "No order is being tracked for this household and none was found in Zepto's "
                "order history. Do not guess a status."
            ),
        }
    )


def _not_connected() -> ToolOutcome:
    return ToolOutcome(
        model_output={
            "status": "not_connected",
            "store": "Zepto",
            "message": "Zepto is not connected, so its orders cannot be read.",
        }
    )


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(model_output={"status": "error", "message": message})
