"""Saved shopping lists the user creates, and adding one to the basket in one tap.

User-created only: a list is made by name or from the current basket, edited in
the list popup, and never filled in by a prediction. Adding re-checks every
item at its store (app/shopping/saved_lists.py) and never quotes or orders.
"""

from datetime import datetime
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from app.api.basket import BasketOut, Money, basket_out, money
from app.auth import UserIdDependency
from app.dependencies import (
    BasketServiceDependency,
    SavedListStoreDependency,
    SwiggyServiceDependency,
    ZeptoServiceDependency,
)
from app.legal_consent import ConsentedUserIdDependency
from app.shopping.basket import BasketService
from app.shopping.errors import InvalidBasketChange, NotFound, ShoppingError
from app.shopping.models import ProductRef, ProviderName
from app.shopping.saved_lists import (
    MAX_LIST_ITEMS,
    AddReport,
    ListItem,
    ListItemInput,
    ListLimitReached,
    ListNameTaken,
    SavedList,
    StoreTarget,
    add_items,
    clean_name,
    edited_items,
    merge_items,
)


router = APIRouter()


class StoreAddresses(BaseModel):
    """The delivery address the page has selected per store; null when not ready."""

    swiggy_address_id: str | None = Field(default=None, max_length=256)
    zepto_address_id: str | None = Field(default=None, max_length=256)


class ListItemOut(BaseModel):
    ref: ProductRef
    name: str
    brand: str | None
    pack_size: str | None
    quantity: int
    store: str
    last_price: Money | None


class ListOut(BaseModel):
    id: str
    name: str
    items: list[ListItemOut]
    item_count: int
    created_at: datetime
    updated_at: datetime


class ListsOut(BaseModel):
    lists: list[ListOut]


class CreateListRequest(BaseModel):
    name: str
    # Stores whose open basket becomes the list ("Save basket as list"); empty = an empty list.
    from_baskets: list[ProviderName] = Field(default_factory=list, max_length=2)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return clean_name(value)


class UpdateListRequest(BaseModel):
    name: str | None = None
    # Every item to keep, with its quantity; an item left out is removed.
    items: list[ListItemInput] | None = Field(default=None, max_length=MAX_LIST_ITEMS)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return None if value is None else clean_name(value)


class AddBasketRequest(BaseModel):
    providers: list[ProviderName] = Field(min_length=1, max_length=2)


class ItemResultOut(BaseModel):
    name: str
    pack_size: str | None
    store: str
    provider: ProviderName
    quantity: int
    status: str
    unit_price: Money | None


class AddReportOut(BaseModel):
    added_count: int
    items: list[ItemResultOut]
    baskets: list[BasketOut]


STORE_LABELS = {"swiggy_instamart": "Swiggy Instamart", "zepto": "Zepto"}


def item_out(item: ListItem) -> ListItemOut:
    return ListItemOut(
        ref=item.ref,
        name=item.name,
        brand=item.brand,
        pack_size=item.pack_size,
        quantity=item.quantity,
        store=STORE_LABELS[item.ref.provider],
        last_price=money(item.unit_price_paise) if item.unit_price_paise is not None else None,
    )


def list_out(saved: SavedList) -> ListOut:
    return ListOut(
        id=saved.id,
        name=saved.name,
        items=[item_out(item) for item in saved.items],
        item_count=len(saved.items),
        created_at=saved.created_at,
        updated_at=saved.updated_at,
    )


def report_out(report: AddReport) -> AddReportOut:
    return AddReportOut(
        added_count=len(report.added),
        items=[
            ItemResultOut(
                name=item.name,
                pack_size=item.pack_size,
                store=item.store,
                provider=item.provider,
                quantity=item.quantity,
                status=item.status,
                unit_price=money(item.unit_price_paise) if item.unit_price_paise is not None else None,
            )
            for item in report.items
        ],
        baskets=[basket_out(basket) for basket in report.baskets.values()],
    )


def store_targets(
    addresses: StoreAddresses, swiggy: object, zepto: object
) -> dict[ProviderName, StoreTarget]:
    return {
        "swiggy_instamart": StoreTarget("swiggy_instamart", swiggy, addresses.swiggy_address_id),  # type: ignore[arg-type]
        "zepto": StoreTarget("zepto", zepto, addresses.zepto_address_id),  # type: ignore[arg-type]
    }


async def basket_items(baskets: BasketService, user_id: str, providers: list[ProviderName]) -> list[ListItem]:
    items: list[ListItem] = []
    for provider in dict.fromkeys(providers):
        basket = await baskets.active(user_id, provider)
        items.extend(ListItem.from_line(line) for line in basket.lines)
    return items


def raise_http(exc: ShoppingError) -> NoReturn:
    if isinstance(exc, NotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, ListNameTaken):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, (ListLimitReached, InvalidBasketChange)):
        raise HTTPException(422, str(exc)) from exc
    raise exc


@router.get("", response_model=ListsOut)
async def get_lists(store: SavedListStoreDependency, user_id: UserIdDependency) -> ListsOut:
    return ListsOut(lists=[list_out(item) for item in await store.all(user_id)])


@router.post("", response_model=ListOut, status_code=status.HTTP_201_CREATED)
async def create_list(
    body: CreateListRequest,
    store: SavedListStoreDependency,
    baskets: BasketServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> ListOut:
    try:
        items = await basket_items(baskets, user_id, body.from_baskets)
        if body.from_baskets and not items:
            raise HTTPException(422, "Your basket is empty, so there is nothing to save.")
        return list_out(await store.create(user_id, body.name, items))
    except ShoppingError as exc:
        raise_http(exc)


@router.patch("/{list_id}", response_model=ListOut)
async def update_list(
    list_id: str, body: UpdateListRequest, store: SavedListStoreDependency, user_id: ConsentedUserIdDependency
) -> ListOut:
    try:
        items = None
        if body.items is not None:
            current = await store.get(user_id, list_id)
            items = edited_items(current.items, [(item.ref, item.quantity) for item in body.items])
        return list_out(await store.update(user_id, list_id, name=body.name, items=items))
    except ShoppingError as exc:
        raise_http(exc)


@router.post("/{list_id}/add-basket", response_model=ListOut)
async def add_basket_to_list(
    list_id: str,
    body: AddBasketRequest,
    store: SavedListStoreDependency,
    baskets: BasketServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> ListOut:
    try:
        current = await store.get(user_id, list_id)
        extra = await basket_items(baskets, user_id, body.providers)
        if not extra:
            raise HTTPException(422, "Your basket is empty, so there is nothing to add.")
        return list_out(await store.update(user_id, list_id, items=merge_items(current.items, extra)))
    except ShoppingError as exc:
        raise_http(exc)


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(list_id: str, store: SavedListStoreDependency, user_id: ConsentedUserIdDependency) -> Response:
    try:
        await store.delete(user_id, list_id)
    except ShoppingError as exc:
        raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/add-to-basket", response_model=AddReportOut)
async def add_list_to_basket(
    list_id: str,
    body: StoreAddresses,
    store: SavedListStoreDependency,
    baskets: BasketServiceDependency,
    swiggy: SwiggyServiceDependency,
    zepto: ZeptoServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> AddReportOut:
    try:
        saved = await store.get(user_id, list_id)
    except ShoppingError as exc:
        raise_http(exc)
    if not saved.items:
        raise HTTPException(422, f"“{saved.name}” is empty. Add items to it first.")
    return report_out(await add_items(baskets, user_id, saved.items, store_targets(body, swiggy, zepto)))
