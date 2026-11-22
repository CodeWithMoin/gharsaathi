"""One-tap shortcuts on the home screen: reorder the last order, recent items, lists.

Reads only this household's own ordered baskets and saved lists. The actions put
re-checked items in the basket (app/shopping/saved_lists.py); none of them
requests a bill, approves or places an order.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.lists import (
    AddReportOut,
    ListItemOut,
    StoreAddresses,
    item_out,
    report_out,
    store_targets,
)
from app.auth import UserIdDependency
from app.dependencies import (
    BasketServiceDependency,
    OrderedBasketsDependency,
    SavedListStoreDependency,
    SwiggyServiceDependency,
    ZeptoServiceDependency,
)
from app.legal_consent import ConsentedUserIdDependency
from app.shopping.basket import same_variant
from app.shopping.models import ProductRef, ProviderName
from app.shopping.saved_lists import STORE_LABELS, ListItem, add_items, recent_items


router = APIRouter()


class LastOrderOut(BaseModel):
    provider: ProviderName
    store: str
    item_count: int
    item_names: list[str]
    ordered_at: datetime


class ListSummaryOut(BaseModel):
    id: str
    name: str
    item_count: int


class QuickActionsOut(BaseModel):
    last_order: LastOrderOut | None
    recent_items: list[ListItemOut]
    lists: list[ListSummaryOut]


class RecentItemRequest(StoreAddresses):
    ref: ProductRef


@router.get("", response_model=QuickActionsOut)
async def quick_actions(
    orders: OrderedBasketsDependency, lists: SavedListStoreDependency, user_id: UserIdDependency
) -> QuickActionsOut:
    recent = await orders.recent(user_id)
    last = next((basket for basket in recent if basket.lines), None)
    return QuickActionsOut(
        last_order=(
            LastOrderOut(
                provider=last.provider,
                store=STORE_LABELS[last.provider],
                item_count=sum(line.quantity for line in last.lines),
                item_names=[line.name for line in last.lines][:5],
                ordered_at=last.updated_at,
            )
            if last
            else None
        ),
        recent_items=[item_out(item) for item in recent_items(recent)],
        lists=[
            ListSummaryOut(id=item.id, name=item.name, item_count=len(item.items))
            for item in await lists.all(user_id)
        ],
    )


@router.post("/repeat-last", response_model=AddReportOut)
async def repeat_last_order(
    body: StoreAddresses,
    orders: OrderedBasketsDependency,
    baskets: BasketServiceDependency,
    swiggy: SwiggyServiceDependency,
    zepto: ZeptoServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> AddReportOut:
    last = next((basket for basket in await orders.recent(user_id, 1) if basket.lines), None)
    if last is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no earlier order to repeat yet.")
    items = [ListItem.from_line(line) for line in last.lines]
    return report_out(await add_items(baskets, user_id, items, store_targets(body, swiggy, zepto)))


@router.post("/recent-item", response_model=AddReportOut)
async def add_recent_item(
    body: RecentItemRequest,
    orders: OrderedBasketsDependency,
    baskets: BasketServiceDependency,
    swiggy: SwiggyServiceDependency,
    zepto: ZeptoServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> AddReportOut:
    # Only a product this household actually ordered; the browser can't inject one.
    item = next(
        (item for item in recent_items(await orders.recent(user_id)) if same_variant(item.ref, body.ref)),
        None,
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That item is not in your recent orders.")
    return report_out(await add_items(baskets, user_id, [item], store_targets(body, swiggy, zepto)))
