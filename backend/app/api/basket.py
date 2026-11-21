from datetime import datetime
from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.dependencies import ApprovalServiceDependency, BasketServiceDependency
from app.shopping.approval import ApprovalRequest
from app.shopping.basket import (
    MAX_QUANTITY,
    format_rupees,
    item_total_paise,
    line_savings_paise,
    line_total_paise,
    savings_vs_mrp_paise,
)
from app.shopping.errors import (
    ApprovalRefused,
    InvalidBasketChange,
    NotFound,
    ProviderNotConnected,
    ProviderUnavailable,
    ShoppingError,
)
from app.shopping.models import (
    Approval,
    ApprovalStatus,
    Basket,
    BasketLine,
    BasketStatus,
    ProductRef,
    ProviderName,
)


router = APIRouter()
LOCAL_HOUSEHOLD_ID = "local-household"
ProviderQuery = Annotated[ProviderName, Query()]


def _raise_http(exc: ShoppingError) -> NoReturn:
    if isinstance(exc, NotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, InvalidBasketChange):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if isinstance(exc, ApprovalRefused):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, ProviderNotConnected):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if isinstance(exc, ProviderUnavailable):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raise exc


def _stripped(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class Money(BaseModel):
    paise: int
    display: str


def money(paise: int) -> Money:
    return Money(paise=paise, display=format_rupees(paise))


class AddLineRequest(BaseModel):
    ref: ProductRef
    name: str = Field(min_length=1, max_length=300)
    brand: str | None = Field(default=None, max_length=120)
    pack_size: str | None = Field(default=None, max_length=120)
    unit_price_paise: int = Field(ge=0)
    mrp_paise: int | None = Field(default=None, ge=0)
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    available: bool = True

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _stripped(value)


class QuantityRequest(BaseModel):
    quantity: int = Field(ge=0, le=MAX_QUANTITY)


class AddressRequest(BaseModel):
    address_id: str = Field(min_length=1, max_length=256)

    @field_validator("address_id")
    @classmethod
    def strip_address(cls, value: str) -> str:
        return _stripped(value)


class ApproveRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


class LineOut(BaseModel):
    ref: ProductRef
    name: str
    brand: str | None
    pack_size: str | None
    quantity: int
    available: bool
    unit_price: Money
    mrp: Money | None
    line_total: Money
    savings: Money


class BasketOut(BaseModel):
    id: str
    provider: ProviderName
    address_id: str | None
    status: BasketStatus
    revision: int
    lines: list[LineOut]
    item_total: Money
    savings_vs_mrp: Money
    updated_at: datetime


class ChargeOut(BaseModel):
    label: str
    amount: Money


class QuoteOut(BaseModel):
    item_total: Money
    charges: list[ChargeOut]
    payable: Money
    eta_text: str | None
    quoted_at: datetime


class ApprovalOut(BaseModel):
    id: str
    basket_id: str
    basket_revision: int
    status: ApprovalStatus
    fingerprint: str
    payable: Money
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class ApprovalRequestOut(BaseModel):
    approval: ApprovalOut
    quote: QuoteOut
    basket: BasketOut
    item_total_matches: bool


def line_out(line: BasketLine) -> LineOut:
    return LineOut(
        ref=line.ref,
        name=line.name,
        brand=line.brand,
        pack_size=line.pack_size,
        quantity=line.quantity,
        available=line.available,
        unit_price=money(line.unit_price_paise),
        mrp=money(line.mrp_paise) if line.mrp_paise is not None else None,
        line_total=money(line_total_paise(line)),
        savings=money(line_savings_paise(line)),
    )


def basket_out(basket: Basket) -> BasketOut:
    return BasketOut(
        id=basket.id,
        provider=basket.provider,
        address_id=basket.address_id,
        status=basket.status,
        revision=basket.revision,
        lines=[line_out(line) for line in basket.lines],
        item_total=money(item_total_paise(basket)),
        savings_vs_mrp=money(savings_vs_mrp_paise(basket)),
        updated_at=basket.updated_at,
    )


def approval_out(approval: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        basket_id=approval.basket_id,
        basket_revision=approval.basket_revision,
        status=approval.status,
        fingerprint=approval.fingerprint,
        payable=money(approval.payable_paise),
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        decided_at=approval.decided_at,
    )


def approval_request_out(result: ApprovalRequest) -> ApprovalRequestOut:
    quote = result.quote
    return ApprovalRequestOut(
        approval=approval_out(result.approval),
        quote=QuoteOut(
            item_total=money(quote.item_total_paise),
            charges=[
                ChargeOut(label=charge.label, amount=money(charge.amount_paise))
                for charge in quote.charges
            ],
            payable=money(quote.payable_paise),
            eta_text=quote.eta_text,
            quoted_at=quote.quoted_at,
        ),
        basket=basket_out(result.basket),
        item_total_matches=quote.item_total_paise == item_total_paise(result.basket),
    )


@router.get("", response_model=BasketOut)
async def get_basket(service: BasketServiceDependency, provider: ProviderQuery) -> BasketOut:
    return basket_out(await service.active(LOCAL_HOUSEHOLD_ID, provider))


@router.post("/lines", response_model=BasketOut)
async def add_line(
    body: AddLineRequest, service: BasketServiceDependency, provider: ProviderQuery
) -> BasketOut:
    try:
        return basket_out(
            await service.add_line(
                LOCAL_HOUSEHOLD_ID,
                provider,
                BasketLine.model_validate(body.model_dump()),
            )
        )
    except ShoppingError as exc:
        _raise_http(exc)


@router.patch("/lines/{variant_id}", response_model=BasketOut)
async def set_quantity(
    variant_id: str,
    body: QuantityRequest,
    service: BasketServiceDependency,
    provider: ProviderQuery,
) -> BasketOut:
    try:
        return basket_out(
            await service.set_quantity(LOCAL_HOUSEHOLD_ID, provider, variant_id, body.quantity)
        )
    except ShoppingError as exc:
        _raise_http(exc)


@router.delete("/lines/{variant_id}", response_model=BasketOut)
async def remove_line(
    variant_id: str, service: BasketServiceDependency, provider: ProviderQuery
) -> BasketOut:
    try:
        return basket_out(await service.remove_line(LOCAL_HOUSEHOLD_ID, provider, variant_id))
    except ShoppingError as exc:
        _raise_http(exc)


@router.post("/address", response_model=BasketOut)
async def set_address(
    body: AddressRequest, service: BasketServiceDependency, provider: ProviderQuery
) -> BasketOut:
    try:
        return basket_out(await service.set_address(LOCAL_HOUSEHOLD_ID, provider, body.address_id))
    except ShoppingError as exc:
        _raise_http(exc)


@router.post("/approval", response_model=ApprovalRequestOut)
async def request_approval(
    baskets: BasketServiceDependency,
    approvals: ApprovalServiceDependency,
    provider: ProviderQuery,
) -> ApprovalRequestOut:
    try:
        basket = await baskets.active(LOCAL_HOUSEHOLD_ID, provider)
        return approval_request_out(await approvals.request(basket.id))
    except ShoppingError as exc:
        _raise_http(exc)


@router.post("/approval/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: str, body: ApproveRequest, approvals: ApprovalServiceDependency
) -> ApprovalOut:
    try:
        return approval_out(await approvals.approve(approval_id, body.fingerprint))
    except ShoppingError as exc:
        _raise_http(exc)


@router.post("/approval/{approval_id}/reject", response_model=ApprovalOut)
async def reject(approval_id: str, approvals: ApprovalServiceDependency) -> ApprovalOut:
    try:
        return approval_out(await approvals.reject(approval_id))
    except ShoppingError as exc:
        _raise_http(exc)
