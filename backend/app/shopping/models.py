"""Shared shopping domain contract.

Money is always integer paise. Provider prices in rupees are converted once at
the adapter boundary; business logic must never perform money arithmetic with
floats.
"""

import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


ProviderName = Literal["swiggy_instamart", "zepto"]


def rupees_to_paise(value: float | int | str | Decimal) -> int:
    amount = Decimal(str(value)) * 100
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class ProductRef(BaseModel):
    """Identifies one purchasable product variant at one provider."""

    provider: ProviderName
    product_id: str
    variant_id: str
    sku_id: str | None = None


class BasketLine(BaseModel):
    ref: ProductRef
    name: str
    brand: str | None = None
    pack_size: str | None = None
    unit_price_paise: int = Field(ge=0)
    mrp_paise: int | None = Field(default=None, ge=0)
    quantity: int = Field(ge=1, le=50)
    available: bool = True


class BasketStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ABANDONED = "abandoned"


class Basket(BaseModel):
    id: str
    household_id: str
    provider: ProviderName
    address_id: str | None = None
    lines: list[BasketLine] = Field(default_factory=list)
    status: BasketStatus = BasketStatus.DRAFT
    revision: int = 0
    created_at: datetime
    updated_at: datetime


class Charge(BaseModel):
    """A provider fee, tax or discount. Discounts have a negative amount."""

    label: str
    amount_paise: int


class Quote(BaseModel):
    """The provider bill for one exact basket revision."""

    provider: ProviderName
    basket_id: str
    basket_revision: int
    item_total_paise: int
    charges: list[Charge] = Field(default_factory=list)
    payable_paise: int
    unavailable_refs: list[ProductRef] = Field(default_factory=list)
    eta_text: str | None = None
    quoted_at: datetime


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class Approval(BaseModel):
    """Explicit approval bound to the exact basket, address and payable bill."""

    id: str
    basket_id: str
    basket_revision: int
    fingerprint: str
    payable_paise: int
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None


def basket_fingerprint(basket: Basket, payable_paise: int) -> str:
    """Hash the exact commercial state covered by an approval."""

    lines = sorted(
        (
            line.ref.provider,
            line.ref.product_id,
            line.ref.variant_id,
            line.ref.sku_id or "",
            line.quantity,
            line.unit_price_paise,
        )
        for line in basket.lines
    )
    payload = {
        "basket_id": basket.id,
        "provider": basket.provider,
        "address_id": basket.address_id,
        "lines": lines,
        "payable_paise": payable_paise,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
