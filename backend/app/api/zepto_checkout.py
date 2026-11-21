from datetime import datetime
from typing import Annotated, NoReturn

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.auth import UserIdDependency
from app.dependencies import CheckoutServiceDependency
from app.shopping.basket import format_rupees
from app.shopping.errors import (
    ApprovalRefused,
    NotFound,
    ProviderNotConnected,
    ProviderUnavailable,
    ShoppingError,
)
from app.shopping.models import CheckoutAttempt, CheckoutStatus


router = APIRouter()


class CheckoutRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=64)
    fingerprint: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


class CheckoutMoney(BaseModel):
    paise: int
    display: str


class CheckoutAttemptOut(BaseModel):
    id: str
    approval_id: str
    basket_id: str
    status: CheckoutStatus
    payable: CheckoutMoney
    provider_order_id: str | None
    provider_status: str | None
    created_at: datetime
    completed_at: datetime | None


def _out(attempt: CheckoutAttempt) -> CheckoutAttemptOut:
    return CheckoutAttemptOut(
        id=attempt.id,
        approval_id=attempt.approval_id,
        basket_id=attempt.basket_id,
        status=attempt.status,
        payable=CheckoutMoney(
            paise=attempt.payable_paise,
            display=format_rupees(attempt.payable_paise),
        ),
        provider_order_id=attempt.provider_order_id,
        provider_status=attempt.provider_status,
        created_at=attempt.created_at,
        completed_at=attempt.completed_at,
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
    raise exc


@router.post("/cod", response_model=CheckoutAttemptOut)
async def checkout_cod(
    body: CheckoutRequest,
    response: Response,
    service: CheckoutServiceDependency,
    user_id: UserIdDependency,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=200)
    ] = None,
) -> CheckoutAttemptOut:
    if idempotency_key is None or not idempotency_key.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Idempotency-Key header is required.",
        )
    try:
        attempt = await service.checkout_cod(
            body.approval_id,
            user_id,
            idempotency_key,
            expected_fingerprint=body.fingerprint,
        )
    except ShoppingError as exc:
        _raise_http(exc)
    if attempt.status != CheckoutStatus.CONFIRMED:
        response.status_code = status.HTTP_202_ACCEPTED
    return _out(attempt)
