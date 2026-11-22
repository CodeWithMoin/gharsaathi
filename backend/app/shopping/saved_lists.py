"""Saved shopping lists, reorder and recent items: one tap puts known items in the basket.

Lists are only what the user saved and named; nothing here predicts a need.
Every item is re-checked at its store before it is added: the stored name is
searched at the selected address and only the *same* provider variant, while
available, becomes a basket line at today's price. Anything else is reported
back (unavailable, not found, store not ready, store error) and never guessed.
Adding is one revision-checked basket edit per store, which supersedes any open
bill and order card like every other edit. Nothing here quotes, approves or
orders.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.commerce.swiggy.models import Product, ProductSearchResult
from app.database.models import (
    SavedShoppingListRecord,
    ShoppingBasketRecord,
    ZeptoCheckoutAttemptRecord,
)
from app.shopping.basket import MAX_QUANTITY, BasketService, same_variant
from app.shopping.database import basket_from_record
from app.shopping.errors import InvalidBasketChange, NotFound, ShoppingError
from app.shopping.models import (
    Basket,
    BasketLine,
    BasketStatus,
    CheckoutStatus,
    ProductRef,
    ProviderName,
    rupees_to_paise,
)


logger = logging.getLogger(__name__)

MAX_LISTS = 20
MAX_LIST_ITEMS = 40
MAX_NAME_LENGTH = 60
RECENT_ITEMS = 3
# Ordered baskets read for "recent items".
RECENT_ORDERS_SCANNED = 10
# Concurrent searches per store while re-checking a list.
SEARCH_CONCURRENCY = 4
STORE_LABELS: dict[str, str] = {"swiggy_instamart": "Swiggy Instamart", "zepto": "Zepto"}

ItemStatus = Literal[
    "added", "unavailable", "not_found", "store_not_ready", "store_error", "basket_locked"
]


class ListNameTaken(ShoppingError):
    pass


class ListLimitReached(ShoppingError):
    pass


def name_key(name: str) -> str:
    return " ".join(name.split()).casefold()[:MAX_NAME_LENGTH]


def clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("must not be blank")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(f"must be at most {MAX_NAME_LENGTH} characters")
    return cleaned


class ListItem(BaseModel):
    """One remembered product variant at one store, with the quantity to add."""

    ref: ProductRef
    name: str = Field(min_length=1, max_length=300)
    brand: str | None = Field(default=None, max_length=120)
    pack_size: str | None = Field(default=None, max_length=120)
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    # Price when it was saved; shown as "last seen" only, never used for a bill.
    unit_price_paise: int | None = Field(default=None, ge=0)

    @classmethod
    def from_line(cls, line: BasketLine) -> "ListItem":
        return cls(
            ref=line.ref,
            name=line.name,
            brand=line.brand,
            pack_size=line.pack_size,
            quantity=line.quantity,
            unit_price_paise=line.unit_price_paise,
        )


class SavedList(BaseModel):
    id: str
    name: str
    items: list[ListItem]
    created_at: datetime
    updated_at: datetime


def merge_items(current: Sequence[ListItem], extra: Sequence[ListItem]) -> list[ListItem]:
    """``extra`` added to ``current``: the same variant adds its quantity (capped)."""

    merged = [item.model_copy(deep=True) for item in current]
    for item in extra:
        existing = next((m for m in merged if same_variant(m.ref, item.ref)), None)
        if existing is None:
            merged.append(item.model_copy(deep=True))
        else:
            existing.quantity = min(MAX_QUANTITY, existing.quantity + item.quantity)
    if len(merged) > MAX_LIST_ITEMS:
        raise ListLimitReached(f"A list can hold at most {MAX_LIST_ITEMS} items.")
    return merged


def edited_items(current: Sequence[ListItem], wanted: Sequence[tuple[ProductRef, int]]) -> list[ListItem]:
    """Quantity changes and removals only: an edit can't bring in an item the list never had.

    ``wanted`` lists the items to keep, in order, with their new quantity.
    """

    kept: list[ListItem] = []
    for ref, quantity in wanted:
        item = next((i for i in current if same_variant(i.ref, ref)), None)
        if item is None:
            raise InvalidBasketChange("That item is not in this list.")
        if any(same_variant(k.ref, ref) for k in kept):
            continue
        kept.append(item.model_copy(update={"quantity": quantity}, deep=True))
    return kept


def _list(record: SavedShoppingListRecord) -> SavedList:
    return SavedList(
        id=record.id,
        name=record.name,
        items=[ListItem.model_validate(item) for item in record.items or []],
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class SavedListStore:
    """User-scoped CRUD. Every query filters by the verified user id."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        self.clock = clock

    async def all(self, user_id: str) -> list[SavedList]:
        async with self.sessions() as session:
            records = (
                await session.scalars(
                    select(SavedShoppingListRecord)
                    .where(SavedShoppingListRecord.user_id == user_id)
                    .order_by(SavedShoppingListRecord.updated_at.desc())
                )
            ).all()
            return [_list(record) for record in records]

    async def get(self, user_id: str, list_id: str) -> SavedList:
        async with self.sessions() as session:
            record = await self._record(session, user_id, list_id)
            return _list(record)

    async def create(self, user_id: str, name: str, items: Sequence[ListItem] = ()) -> SavedList:
        name = clean_name(name)
        if len(items) > MAX_LIST_ITEMS:
            raise ListLimitReached(f"A list can hold at most {MAX_LIST_ITEMS} items.")
        now = self.clock()
        record = SavedShoppingListRecord(
            id=uuid.uuid4().hex,
            user_id=user_id,
            name=name,
            name_key=name_key(name),
            items=[item.model_dump(mode="json") for item in merge_items([], items)],
            created_at=now,
            updated_at=now,
        )
        try:
            async with self.sessions() as session, session.begin():
                count = await session.scalar(
                    select(func.count())
                    .select_from(SavedShoppingListRecord)
                    .where(SavedShoppingListRecord.user_id == user_id)
                )
                if (count or 0) >= MAX_LISTS:
                    raise ListLimitReached(f"You can keep up to {MAX_LISTS} lists. Delete one first.")
                session.add(record)
        except IntegrityError as exc:
            raise ListNameTaken(f"You already have a list called “{name}”.") from exc
        return _list(record)

    async def update(
        self,
        user_id: str,
        list_id: str,
        *,
        name: str | None = None,
        items: Sequence[ListItem] | None = None,
    ) -> SavedList:
        try:
            async with self.sessions() as session, session.begin():
                record = await self._record(session, user_id, list_id)
                if name is not None:
                    record.name = clean_name(name)
                    record.name_key = name_key(record.name)
                if items is not None:
                    if len(items) > MAX_LIST_ITEMS:
                        raise ListLimitReached(f"A list can hold at most {MAX_LIST_ITEMS} items.")
                    record.items = [item.model_dump(mode="json") for item in items]
                record.updated_at = self.clock()
                await session.flush()
                return _list(record)
        except IntegrityError as exc:
            raise ListNameTaken(f"You already have a list called “{name}”.") from exc

    async def delete(self, user_id: str, list_id: str) -> None:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                delete(SavedShoppingListRecord).where(
                    SavedShoppingListRecord.user_id == user_id,
                    SavedShoppingListRecord.id == list_id,
                )
            )
            if result.rowcount != 1:
                raise NotFound("That list no longer exists.")

    async def find(self, user_id: str, spoken_name: str) -> tuple[SavedList | None, list[SavedList]]:
        """The list the user named ("weekly list", "weekly"), and every list.

        Exact name first, then a single list whose name contains the words (or
        is contained in them). Several partial matches return no list.
        """

        lists = await self.all(user_id)
        key = name_key(spoken_name)
        exact = [item for item in lists if name_key(item.name) == key]
        if exact:
            return exact[0], lists
        stripped = key.removesuffix(" list").strip() or key
        partial = [
            item
            for item in lists
            if stripped in name_key(item.name) or name_key(item.name).removesuffix(" list") in key
        ]
        return (partial[0] if len(partial) == 1 else None), lists

    @staticmethod
    async def _record(session: AsyncSession, user_id: str, list_id: str) -> SavedShoppingListRecord:
        record = await session.scalar(
            select(SavedShoppingListRecord).where(
                SavedShoppingListRecord.user_id == user_id,
                SavedShoppingListRecord.id == list_id,
            )
        )
        if record is None:
            raise NotFound("That list no longer exists.")
        return record


# Demo/sample orders (codex/memory-setup) carry ids with this prefix; never repeat them.
SYNTHETIC_PREFIX = "synth-"


class OrderedBaskets:
    """This household's real orders, newest first.

    Real means the store confirmed it: the basket is ``ordered`` *and* a checkout
    attempt for it is ``confirmed`` with a provider order id. Synthetic demo or
    sample orders (``synth-`` basket or order ids) and baskets without a confirmed
    attempt are never returned, so "Reorder last order" and recent items only
    ever repeat what the household actually ordered.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def recent(self, household_id: str, limit: int = RECENT_ORDERS_SCANNED) -> list[Basket]:
        confirmed = (
            select(ZeptoCheckoutAttemptRecord.id)
            .where(
                ZeptoCheckoutAttemptRecord.basket_id == ShoppingBasketRecord.id,
                ZeptoCheckoutAttemptRecord.household_id == household_id,
                ZeptoCheckoutAttemptRecord.status == CheckoutStatus.CONFIRMED.value,
                ZeptoCheckoutAttemptRecord.provider_order_id.is_not(None),
                ZeptoCheckoutAttemptRecord.provider_order_id.not_like(f"{SYNTHETIC_PREFIX}%"),
            )
            .exists()
        )
        async with self.sessions() as session:
            records = (
                await session.scalars(
                    select(ShoppingBasketRecord)
                    .where(
                        ShoppingBasketRecord.household_id == household_id,
                        ShoppingBasketRecord.status == BasketStatus.ORDERED.value,
                        ShoppingBasketRecord.id.not_like(f"{SYNTHETIC_PREFIX}%"),
                        confirmed,
                    )
                    .order_by(ShoppingBasketRecord.updated_at.desc())
                    .limit(limit)
                )
            ).all()
            return [basket_from_record(record) for record in records]


class OrderedBasketReader(Protocol):
    async def recent(self, household_id: str, limit: int = RECENT_ORDERS_SCANNED) -> list[Basket]: ...


def recent_items(orders: Sequence[Basket], limit: int = RECENT_ITEMS) -> list[ListItem]:
    """The last ``limit`` distinct products this household ordered, newest order first."""

    seen: list[ListItem] = []
    for basket in orders:
        for line in basket.lines:
            if any(same_variant(item.ref, line.ref) for item in seen):
                continue
            seen.append(ListItem.from_line(line).model_copy(update={"quantity": 1}))
            if len(seen) == limit:
                return seen
    return seen


class ProductSearcher(Protocol):
    async def search(self, query: str, address_id: str, offset: int, /) -> ProductSearchResult: ...


@dataclass(frozen=True)
class StoreTarget:
    """A store items may be added to: its searcher and the user's selected address."""

    provider: ProviderName
    searcher: ProductSearcher | None
    address_id: str | None

    @property
    def label(self) -> str:
        return STORE_LABELS[self.provider]


class ItemResult(BaseModel):
    name: str
    pack_size: str | None = None
    provider: ProviderName
    store: str
    quantity: int
    status: ItemStatus
    # Today's price for an added item.
    unit_price_paise: int | None = None


@dataclass
class AddReport:
    items: list[ItemResult] = field(default_factory=list)
    baskets: dict[ProviderName, Basket] = field(default_factory=dict)

    @property
    def added(self) -> list[ItemResult]:
        return [item for item in self.items if item.status == "added"]

    @property
    def skipped(self) -> list[ItemResult]:
        return [item for item in self.items if item.status != "added"]


def _matches(product: Product, ref: ProductRef) -> bool:
    if product.spin_id == ref.variant_id:
        return True
    return bool(ref.sku_id) and product.product_id == ref.product_id and product.sku_id == ref.sku_id


def _live_line(item: ListItem, product: Product) -> BasketLine:
    unit = rupees_to_paise(product.price.offer_price)
    mrp = rupees_to_paise(product.price.mrp)
    return BasketLine(
        ref=ProductRef(
            provider=item.ref.provider,
            product_id=product.product_id,
            variant_id=product.spin_id,
            sku_id=product.sku_id,
        ),
        name=product.name,
        brand=product.brand,
        pack_size=product.pack_size,
        unit_price_paise=unit,
        mrp_paise=mrp if mrp > unit else None,
        quantity=item.quantity,
        available=True,
    )


def _search_query(item: ListItem) -> str:
    query = item.name
    if item.pack_size and item.pack_size.casefold() not in query.casefold():
        query = f"{query} {item.pack_size}"
    return " ".join(query.split())[:120]


def _not_connected(exc: Exception) -> bool:
    return type(exc).__name__.endswith(("AuthenticationRequired", "ConfigurationError"))


async def add_items(
    baskets: BasketService,
    household_id: str,
    items: Sequence[ListItem],
    targets: Mapping[ProviderName, StoreTarget],
) -> AddReport:
    """Re-check each item at its store and add the ones that are the same and available.

    One failing store stops further searches at that store (its remaining items
    are ``store_error``) and never affects the other store. Each store's lines
    are added in one revision-checked edit.
    """

    report = AddReport()
    results: list[ItemResult | None] = [None] * len(items)
    lines: dict[ProviderName, list[tuple[int, BasketLine]]] = {}
    # A store that failed once is not searched again in this run.
    failed: dict[ProviderName, ItemStatus] = {}
    searches: dict[tuple[ProviderName, str], asyncio.Task[ProductSearchResult]] = {}
    limits = {provider: asyncio.Semaphore(SEARCH_CONCURRENCY) for provider in STORE_LABELS}

    def result(item: ListItem, status: ItemStatus, price: int | None = None) -> ItemResult:
        return ItemResult(
            name=item.name,
            pack_size=item.pack_size,
            provider=item.ref.provider,
            store=STORE_LABELS[item.ref.provider],
            quantity=item.quantity,
            status=status,
            unit_price_paise=price,
        )

    async def search(target: StoreTarget, query: str) -> ProductSearchResult:
        assert target.searcher is not None and target.address_id
        async with limits[target.provider]:
            if target.provider in failed:
                raise RuntimeError("store already failed")
            try:
                return await target.searcher.search(query, target.address_id, 0)
            except Exception as exc:
                if target.provider not in failed:
                    logger.warning(
                        "event=saved_list_search_failed provider=%s error=%s",
                        target.provider,
                        type(exc).__name__,
                    )
                    failed[target.provider] = "store_not_ready" if _not_connected(exc) else "store_error"
                raise

    async def check(index: int, item: ListItem) -> None:
        target = targets.get(item.ref.provider)
        if target is None or target.searcher is None or not target.address_id:
            results[index] = result(item, "store_not_ready")
            return
        key = (target.provider, _search_query(item).casefold())
        if key not in searches:
            searches[key] = asyncio.ensure_future(search(target, _search_query(item)))
        try:
            found = await searches[key]
        except Exception:  # noqa: BLE001 - one store's failure is reported, never raised
            results[index] = result(item, failed.get(target.provider, "store_error"))
            return
        product = next((p for p in [*found.products, *found.similar_products] if _matches(p, item.ref)), None)
        if product is None:
            results[index] = result(item, "not_found")
        elif not product.available:
            results[index] = result(item, "unavailable")
        else:
            line = _live_line(item, product)
            lines.setdefault(target.provider, []).append((index, line))
            results[index] = result(item, "added", line.unit_price_paise)

    await asyncio.gather(*(check(index, item) for index, item in enumerate(items)))

    for provider, entries in lines.items():
        target = targets[provider]
        try:
            if target.address_id:
                await baskets.set_address(household_id, provider, target.address_id)
            report.baskets[provider] = await baskets.add_lines(
                household_id, provider, [line for _, line in entries]
            )
        except ShoppingError:
            for index, _ in entries:
                current = results[index]
                assert current is not None
                results[index] = current.model_copy(update={"status": "basket_locked", "unit_price_paise": None})
    report.items = [item for item in results if item is not None]
    return report


class ListItemInput(BaseModel):
    """A quantity change in the edit popup (by product reference)."""

    ref: ProductRef
    quantity: int = Field(ge=1, le=MAX_QUANTITY)


class ListNameInput(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return clean_name(value)
