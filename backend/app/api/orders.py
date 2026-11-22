"""The user's one tap on the order card: approve the exact bill and place the order.

Voice and typed chat can only prepare the card (``prepare_order``). These routes
are the only path from the assistant to an order, and they act only for the
signed-in user who owns the confirmation. Choosing another payment method
returns a fresh card; checking a payment makes one status read and never
repeats the order call. No route accepts a PIN, card number or OTP.
"""

import logging
from typing import Literal, NoReturn

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth import UserIdDependency
from app.dependencies import CheckoutServiceDependency, OrderConfirmationsDependency
from app.legal_consent import ConsentedUserIdDependency
from app.shopping.models import PaymentMethod
from app.shopping.money import format_rupees
from app.shopping.order_confirmation import result_from_reconciliation
from app.shopping.order_confirmation import OrderConfirmation, OrderResult, OrderStatus
from app.shopping.payments import payment_label
from app.shopping.errors import (
    ApprovalRefused,
    NotFound,
    ProviderNotConnected,
    ProviderUnavailable,
    ShoppingError,
)
from app.tracking.models import StartTrackingRequest, TrackingProvider, TrackingRead
from app.tracking.service import OrderTrackingService


router = APIRouter()
logger = logging.getLogger(__name__)


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=8, max_length=64, pattern="^[A-Za-z0-9_-]+$")


class PaymentMethodRequest(ConfirmationRequest):
    payment_method: PaymentMethod


class PaymentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkout_id: str = Field(min_length=8, max_length=64, pattern="^[A-Za-z0-9_-]+$")


class OrderMoney(BaseModel):
    paise: int
    display: str


class PaymentOut(BaseModel):
    method: PaymentMethod
    label: str
    # Provider-issued https/upi link to finish payment; only while awaiting payment.
    url: str | None = None


class OrderConfirmOut(BaseModel):
    status: OrderStatus
    store: str
    provider_order_id: str | None
    # The store's order code, when known (shown as "#CODE").
    provider_order_code: str | None = None
    # For an unknown outcome: the last read-only order-history check.
    check: Literal["matched", "not_found", "ambiguous", "unavailable", "not_unknown"] | None = None
    candidates: list[str] = []
    payable: OrderMoney
    checkout_id: str | None = None
    payment: PaymentOut
    # Read-only status monitor started for a provider-confirmed order; null when
    # the order is unconfirmed or tracking could not start (the order stands).
    tracking: TrackingRead | None = None


class PaymentMethodOption(BaseModel):
    id: PaymentMethod
    label: str


class OrderCardOut(BaseModel):
    """A fresh card for the same bill, bound to the chosen payment method."""

    id: str
    expires_at: str
    payment_method: PaymentMethod
    payment_methods: list[PaymentMethodOption]


class OrderCancelOut(BaseModel):
    status: Literal["cancelled"]


def _result_out(result: OrderResult, *, tracking: TrackingRead | None = None) -> OrderConfirmOut:
    return OrderConfirmOut(
        status=result.status,
        store=result.store,
        provider_order_id=result.provider_order_id,
        payable=OrderMoney(paise=result.payable_paise, display=format_rupees(result.payable_paise)),
        checkout_id=result.checkout_id,
        provider_order_code=result.order_code,
        check=result.check,
        candidates=list(result.candidates),
        payment=PaymentOut(
            method=result.payment_method,
            label=payment_label(result.payment_method),
            url=result.payment_url,
        ),
        tracking=tracking,
    )


def card_out(confirmation: OrderConfirmation) -> OrderCardOut:
    return OrderCardOut(
        id=confirmation.id,
        expires_at=confirmation.expires_at.isoformat(),
        payment_method=confirmation.payment_method,
        payment_methods=[
            PaymentMethodOption(id=method, label=payment_label(method))
            for method in confirmation.payment_methods
        ],
    )


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
    return _result_out(result, tracking=await _start_tracking(request, user_id, result))


@router.post("/payment-method", response_model=OrderCardOut)
async def change_payment_method(
    body: PaymentMethodRequest,
    confirmations: OrderConfirmationsDependency,
    user_id: ConsentedUserIdDependency,
) -> OrderCardOut:
    try:
        confirmation = await confirmations.change_payment_method(
            user_id, body.confirmation_id, body.payment_method
        )
    except ShoppingError as exc:
        _raise_http(exc)
    return card_out(confirmation)


@router.post("/payment-status", response_model=OrderConfirmOut)
async def check_payment_status(
    body: PaymentStatusRequest,
    request: Request,
    response: Response,
    confirmations: OrderConfirmationsDependency,
    checkout: CheckoutServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> OrderConfirmOut:
    try:
        result = await confirmations.check_payment(user_id, body.checkout_id, checkout)
    except ShoppingError as exc:
        _raise_http(exc)
    if result.status != "confirmed":
        response.status_code = status.HTTP_202_ACCEPTED
    # A prepaid order confirmed by a status check is tracked like any other.
    return _result_out(result, tracking=await _start_tracking(request, user_id, result))


@router.post("/check", response_model=OrderConfirmOut)
async def check_order(
    body: PaymentStatusRequest,
    request: Request,
    response: Response,
    confirmations: OrderConfirmationsDependency,
    user_id: ConsentedUserIdDependency,
) -> OrderConfirmOut:
    """The card's "Check with Zepto": one read-only order-history check.

    Never calls an order tool; repeated taps reuse the last answer for a while.
    """

    try:
        result = await confirmations.check_order(user_id, body.checkout_id)
    except ShoppingError as exc:
        _raise_http(exc)
    if result.status != "confirmed":
        response.status_code = status.HTTP_202_ACCEPTED
    return _result_out(result, tracking=await _start_tracking(request, user_id, result))


class PendingOrdersOut(BaseModel):
    orders: list[OrderConfirmOut]


@router.post("/reconcile-pending", response_model=PendingOrdersOut)
async def reconcile_pending(
    request: Request,
    confirmations: OrderConfirmationsDependency,
    user_id: ConsentedUserIdDependency,
) -> PendingOrdersOut:
    """On app load: one read-only check for each recent unknown checkout.

    Returns every still-unknown checkout and each one this call settled.
    """

    reconciler = confirmations.reconciler
    if reconciler is None:
        return PendingOrdersOut(orders=[])
    out: list[OrderConfirmOut] = []
    for outcome in await reconciler.reconcile_household(user_id):
        if outcome.attempt is None:
            continue
        if outcome.newly_reconciled:
            await confirmations.notify_reconciled(outcome)
        elif outcome.check == "not_unknown":
            continue
        result = result_from_reconciliation("", "Zepto", outcome)
        out.append(_result_out(result, tracking=await _start_tracking(request, user_id, result)))
    return PendingOrdersOut(orders=out)


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
