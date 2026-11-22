"""The user's one tap on the order card: approve the exact bill and place the order.

Voice and typed chat can only prepare the card (``prepare_order``). These routes
are the only path from the assistant to an order, and they act only for the
signed-in user who owns the confirmation.
"""

import logging
from typing import Literal, NoReturn

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth import UserIdDependency
from app.dependencies import CheckoutServiceDependency, OrderConfirmationsDependency
from app.legal_consent import ConsentedUserIdDependency
from app.shopping.money import format_rupees
from app.shopping.errors import (
    ApprovalRefused,
    NotFound,
    ProviderNotConnected,
    ProviderUnavailable,
    ShoppingError,
)
from app.shopping.order_confirmation import OrderResult
from app.tracking.models import StartTrackingRequest, TrackingProvider, TrackingRead
from app.tracking.service import OrderTrackingService


router = APIRouter()
logger = logging.getLogger(__name__)


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=8, max_length=64, pattern="^[A-Za-z0-9_-]+$")


class OrderMoney(BaseModel):
    paise: int
    display: str


class OrderConfirmOut(BaseModel):
    status: Literal["confirmed", "outcome_unknown"]
    store: str
    provider_order_id: str | None
    payable: OrderMoney
    # Read-only status monitor started for a provider-confirmed order; null when
    # the order is unconfirmed or tracking could not start (the order stands).
    tracking: TrackingRead | None = None


class OrderCancelOut(BaseModel):
    status: Literal["cancelled"]


def _raise_http(exc: ShoppingError) -> NoReturn:
    if isinstance(exc, NotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, ApprovalRefused):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, ProviderNotConnected):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if isinstance(exc, ProviderUnavailable):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/confirm", response_model=OrderConfirmOut)
async def confirm_order(
    body: ConfirmationRequest,
    request: Request,
    response: Response,
    confirmations: OrderConfirmationsDependency,
    checkout: CheckoutServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> OrderConfirmOut:
    try:
        result = await confirmations.place(user_id, body.confirmation_id, checkout)
    except ShoppingError as exc:
        _raise_http(exc)
    if result.status != "confirmed":
        response.status_code = status.HTTP_202_ACCEPTED
    return OrderConfirmOut(
        status="confirmed" if result.status == "confirmed" else "outcome_unknown",
        store=result.store,
        provider_order_id=result.provider_order_id,
        payable=OrderMoney(paise=result.payable_paise, display=format_rupees(result.payable_paise)),
        tracking=await _start_tracking(request, user_id, result),
    )


async def _start_tracking(request: Request, user_id: str, result: OrderResult) -> TrackingRead | None:
    """Watch a provider-confirmed Zepto order (idempotent per order).

    Only reads order status. A tracking failure never changes the order outcome.
    """

    # Only Zepto orders can be confirmed here (ORDERABLE_PROVIDERS).
    if result.status != "confirmed" or not result.provider_order_id:
        return None
    service = getattr(request.app.state, "order_tracking_service", None)
    if not isinstance(service, OrderTrackingService):
        return None
    try:
        return await service.ensure(
            user_id,
            StartTrackingRequest(provider=TrackingProvider.ZEPTO, order_id=result.provider_order_id),
        )
    except Exception as exc:  # noqa: BLE001 - the order is placed; tracking is best effort
        logger.warning("event=order_auto_track_failed error=%s", type(exc).__name__)
        return None


@router.post("/cancel", response_model=OrderCancelOut)
async def cancel_order(
    body: ConfirmationRequest,
    confirmations: OrderConfirmationsDependency,
    user_id: UserIdDependency,
) -> OrderCancelOut:
    try:
        await confirmations.cancel(user_id, body.confirmation_id)
    except ShoppingError as exc:
        _raise_http(exc)
    return OrderCancelOut(status="cancelled")
