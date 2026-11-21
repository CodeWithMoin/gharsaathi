"""Read-only Swiggy Instamart order history. This router cannot place orders."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.swiggy import _raise_http
from app.commerce.swiggy.models import OrderList
from app.dependencies import SwiggyServiceDependency


router = APIRouter()


@router.get("", response_model=OrderList)
async def list_orders(
    service: SwiggyServiceDependency,
    count: Annotated[int, Query(ge=1, le=20)] = 10,
    active_only: Annotated[bool, Query(alias="activeOnly")] = False,
) -> OrderList:
    try:
        return await service.orders(count, active_only)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")
