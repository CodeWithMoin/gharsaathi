"""Narrow read-only adapters for official provider order-status tools."""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyError,
    SwiggyProviderError,
    SwiggyRateLimited,
)
from app.commerce.zepto.errors import (
    ZeptoAuthenticationRequired,
    ZeptoError,
    ZeptoProviderError,
    ZeptoRateLimited,
)
from app.commerce.zepto.history import parse_order_detail
from app.tracking.cancellation import order_cancel_tool
from app.tracking.errors import TrackingProviderError
from app.tracking.models import TrackingProvider, TrackingSnapshot, TrackingStatus


class _McpReader(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any], **kwargs) -> dict[str, Any]: ...


class _ToolCatalogueReader(_McpReader, Protocol):
    async def list_tools(self): ...


class DemoOrderStatusAdapter:
    """Network-free delivery snapshot used by the default safe demo mode."""

    def __init__(self, provider: TrackingProvider) -> None:
        self.provider = provider

    async def fetch_delivery_status(
        self, *, order_id: str, address_id: str | None
    ) -> TrackingSnapshot:
        del order_id, address_id
        return TrackingSnapshot(
            status=TrackingStatus.OUT_FOR_DELIVERY,
            provider_status="Demo · out for delivery",
            message="Your demo order is on the way.",
            eta_at=datetime.now(UTC) + timedelta(minutes=18),
            poll_interval_seconds=60,
        )


class SwiggyDeliveryStatusAdapter:
    """Poll Swiggy's documented address-scoped read-only delivery-status tool.

    ``track_order`` deliberately does not appear here: it needs coordinates and
    must only be used after a separate user-requested rich-status flow exists.
    """

    provider = TrackingProvider.SWIGGY
    TOOL_NAME = "get_delivery_status"
    LOCAL_RATE_LIMIT_FALLBACK_SECONDS = 300

    def __init__(self, client: _McpReader) -> None:
        self.client = client

    async def fetch_delivery_status(
        self, *, order_id: str, address_id: str | None
    ) -> TrackingSnapshot:
        if address_id is None:
            raise TrackingProviderError(
                "A saved delivery address is required to track this Swiggy order.",
                transient=False,
            )
        try:
            payload = await self.client.call_tool(
                self.TOOL_NAME,
                {"orderId": order_id, "addressId": address_id},
                retry_on_auth=True,
            )
        except SwiggyRateLimited as exc:
            raise _rate_limited_error(
                exc, fallback_seconds=self.LOCAL_RATE_LIMIT_FALLBACK_SECONDS
            ) from exc
        except SwiggyAuthenticationRequired as exc:
            raise TrackingProviderError(
                "Reconnect Swiggy to resume tracking this order.", reconnect_required=True
            ) from exc
        except SwiggyProviderError as exc:
            raise TrackingProviderError() from exc
        except SwiggyError as exc:
            raise TrackingProviderError(
                "Swiggy returned an invalid delivery update.", transient=False
            ) from exc
        return _parse_snapshot(payload)


class ZeptoOrderStatusAdapter:
    """Read Zepto order status only through documented read-only tools.

    ``get_order_status`` is used when the live catalogue exposes it; otherwise
    ``get_order_detail`` (the documented read-only order-detail tool) supplies
    the shipment status and Zepto's committed ETA. ``list_order_history`` is
    never repurposed as a polling source, and no provider tool name can be
    accepted from an API request. The live catalogue is also checked for an
    order-cancellation tool; it is reported (``cancel_supported``), never called.
    """

    provider = TrackingProvider.ZEPTO
    READ_ONLY_TOOL_NAMES = frozenset({"get_order_status"})
    DETAIL_TOOL_NAME = "get_order_detail"
    # get_order_detail carries no polling hint; stay gentle with Zepto's quota.
    DETAIL_POLL_INTERVAL_SECONDS = 90
    LOCAL_RATE_LIMIT_FALLBACK_SECONDS = 300

    def __init__(self, client: _ToolCatalogueReader) -> None:
        self.client = client

    async def fetch_delivery_status(
        self, *, order_id: str, address_id: str | None
    ) -> TrackingSnapshot:
        del address_id
        try:
            tools = await self.client.list_tools()
        except ZeptoRateLimited as exc:
            raise _rate_limited_error(
                exc, fallback_seconds=self.LOCAL_RATE_LIMIT_FALLBACK_SECONDS
            ) from exc
        except ZeptoAuthenticationRequired as exc:
            raise TrackingProviderError(
                "Reconnect Zepto to resume tracking this order.", reconnect_required=True
            ) from exc
        except ZeptoProviderError as exc:
            raise TrackingProviderError() from exc
        except ZeptoError as exc:
            raise TrackingProviderError(
                "Zepto returned an invalid tracking capability response.", transient=False
            ) from exc
        names = {tool.name for tool in tools}
        cancel_supported = order_cancel_tool(tools) is not None
        name = next((tool for tool in self.READ_ONLY_TOOL_NAMES if tool in names), None)
        if name is None and self.DETAIL_TOOL_NAME in names:
            name = self.DETAIL_TOOL_NAME
        if name is None:
            raise TrackingProviderError(
                "Zepto's connected catalogue does not expose an authorised live order-status tool.",
                transient=False,
            )
        try:
            payload = await self.client.call_tool(name, {"orderId": order_id}, retry_on_auth=True)
        except ZeptoRateLimited as exc:
            raise _rate_limited_error(
                exc, fallback_seconds=self.LOCAL_RATE_LIMIT_FALLBACK_SECONDS
            ) from exc
        except ZeptoAuthenticationRequired as exc:
            raise TrackingProviderError(
                "Reconnect Zepto to resume tracking this order.", reconnect_required=True
            ) from exc
        except ZeptoProviderError as exc:
            raise TrackingProviderError() from exc
        except ZeptoError as exc:
            raise TrackingProviderError(
                "Zepto returned an invalid delivery update.", transient=False
            ) from exc
        if name == self.DETAIL_TOOL_NAME:
            snapshot = _snapshot_from_zepto_detail(payload, self.DETAIL_POLL_INTERVAL_SECONDS)
        else:
            snapshot = _parse_snapshot(payload)
        return snapshot.model_copy(update={"cancel_supported": cancel_supported})


def _snapshot_from_zepto_detail(
    payload: dict[str, Any], poll_interval_seconds: int
) -> TrackingSnapshot:
    """Status from the live ``get_order_detail`` shape (strictly validated).

    The latest shipment's machine status decides the step; its formatted
    status is the display text. The ETA is Zepto's own committed ETA
    (placed time + ``committedEtaInMins``); none is invented when either is
    missing, and none is shown once the order is delivered or cancelled.
    Placed, arrived and delivered times are Zepto's own; a missing or
    unreadable one stays empty. The delivered time is kept only for a
    delivered shipment.
    """

    try:
        detail = parse_order_detail(payload)
    except ZeptoError as exc:
        raise TrackingProviderError(
            "Zepto returned an invalid delivery update.", transient=False
        ) from exc
    shipment = detail.shipments[-1] if detail.shipments else None
    raw_status = shipment.status if shipment else None
    status = _normalise_status(raw_status)
    if status == TrackingStatus.UNKNOWN:
        status = _normalise_status(
            shipment.formatted_status if shipment else detail.formatted_status
        )
    display = (shipment.formatted_status if shipment else None) or detail.formatted_status
    final = status in {TrackingStatus.DELIVERED, TrackingStatus.CANCELLED}
    placed = _store_time(shipment.placed_time) if shipment else None
    committed = (
        shipment.committed_eta_minutes
        if shipment and shipment.committed_eta_minutes > 0
        else None
    )
    eta_at = None
    if placed is not None and committed is not None and not final:
        eta_at = placed + timedelta(minutes=committed)
    delivered_at = (
        _store_time(shipment.delivery_time)
        if shipment and status == TrackingStatus.DELIVERED
        else None
    )
    return TrackingSnapshot(
        status=status,
        provider_status=display[:160] if display else None,
        eta_at=eta_at,
        placed_at=placed,
        arrived_at=_store_time(shipment.arrived_time) if shipment else None,
        delivered_at=delivered_at,
        committed_eta_minutes=committed,
        total_arrival_minutes=shipment.total_arrival_time_minutes if shipment else None,
        poll_interval_seconds=poll_interval_seconds,
        delivered=status == TrackingStatus.DELIVERED,
        cancelled=status == TrackingStatus.CANCELLED,
        order_code=detail.code[:64] if detail.code else None,
    )


def _rate_limited_error(
    error: Exception, *, fallback_seconds: int
) -> TrackingProviderError:
    supplied = getattr(error, "retry_after_seconds", None)
    # Future clients should pass a provider Retry-After value; while the
    # current clients only expose their documented 300s cooldown, mirror that
    # cooldown so tracking does not keep waking up during the provider block.
    retry_after = (
        supplied if isinstance(supplied, int) and supplied > 0 else fallback_seconds
    )
    return TrackingProviderError(
        "The provider is temporarily rate-limiting delivery updates.",
        retry_after_seconds=retry_after,
    )


def _parse_snapshot(payload: dict[str, Any]) -> TrackingSnapshot:
    """Conservative normalisation of a provider response envelope.

    Provider strings remain display data.  Only documented boolean terminal
    fields decide whether the loop stops; string matching is a conservative
    fallback for providers that encode terminal status in a single field.
    """

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise TrackingProviderError("The provider returned an invalid delivery update.")
    raw_status = _string(
        data, "status", "orderStatus", "deliveryStatus", "statusText"
    )
    status = _normalise_status(raw_status)
    delivered = _boolean(data, "delivered", "isDelivered") or status == TrackingStatus.DELIVERED
    cancelled = _boolean(data, "cancelled", "isCancelled") or status == TrackingStatus.CANCELLED
    return TrackingSnapshot(
        status=status,
        provider_status=raw_status,
        message=_string(
            data, "message", "statusMessage", "description", "etaText"
        ),
        eta_at=_datetime(
            data.get("etaAt")
            or data.get("estimatedDeliveryAt")
            or data.get("deliveryBy")
        ),
        poll_interval_seconds=_integer(
            data.get("pollIntervalSec") or data.get("pollingIntervalSeconds")
        ),
        delivered=delivered,
        cancelled=cancelled,
        disabled=_boolean(data, "disabled", "trackingDisabled"),
    )


def _string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return None


def _boolean(data: dict[str, Any], *keys: str) -> bool:
    return any(data.get(key) is True for key in keys)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _datetime(value: object) -> datetime | None:
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _store_time(value: object) -> datetime | None:
    """A store timestamp with an explicit zone, else none (a zone is never assumed)."""

    parsed = _datetime(value)
    return parsed if parsed is not None and parsed.utcoffset() is not None else None


def _normalise_status(value: str | None) -> TrackingStatus:
    """Exact known status words only; anything else stays unknown (never guessed)."""

    text = (value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if text in {"delivered", "complete", "completed", "order_delivered"}:
        return TrackingStatus.DELIVERED
    if text in {"cancelled", "canceled", "order_cancelled", "order_canceled"}:
        return TrackingStatus.CANCELLED
    if text in {"out_for_delivery", "on_the_way", "dispatched", "arriving", "arrived"}:
        return TrackingStatus.OUT_FOR_DELIVERY
    if text in {"preparing", "packed", "packing", "order_packed", "ready_for_dispatch"}:
        return TrackingStatus.PREPARING
    if text in {"confirmed", "placed", "accepted", "order_placed", "order_confirmed", "created"}:
        return TrackingStatus.CONFIRMED
    return TrackingStatus.UNKNOWN


def normalise_status(value: str | None) -> TrackingStatus:
    """Public form of the conservative status-word mapping."""

    return _normalise_status(value)
