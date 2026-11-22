"""'What do you know about me?': one person's memory, grouped and labelled by source.

Read-only. Lists confirmed memories (and pending suggestions separately) even
while preference memory is off, so a person can always see and remove what is
saved; order history appears only with purchase-history consent. Every line is
built from stored structured fields, never from model text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MemoryItem, MemorySourceType, MemoryStatus, MemorySubject
from app.memory.household import (
    STORE_LABELS,
    display_value_of,
    item_of,
    kind_of,
    origin_of,
)
from app.memory.household_service import HouseholdPreferenceService
from app.memory.purchase_graph import PurchaseGraphService
from app.memory.repository import MemoryRepository
from app.user_profile import get_profile_record

GroupId = Literal["home", "food", "usual_buys", "avoid", "shopping_style", "from_orders"]
Source = Literal["told", "in_passing", "repeated_choice", "order_history"]

GROUP_TITLES: dict[str, str] = {
    "home": "Home",
    "food": "Food",
    "usual_buys": "Usual buys",
    "avoid": "Avoid",
    "shopping_style": "Shopping style",
    "from_orders": "From your orders",
}
SOURCE_LABELS: dict[str, str] = {
    "told": "You told us",
    "in_passing": "Said in passing",
    "repeated_choice": "From your repeated choices",
    "order_history": "From your orders",
}
# What the voice answer reads first: food rules, then nevers, then usual buys.
SPOKEN_ORDER: tuple[str, ...] = ("food", "avoid", "usual_buys", "home", "shopping_style", "from_orders")
MAX_ORDER_ITEMS = 10
LANGUAGE_LABELS = {"hinglish": "Hinglish", "hindi": "Hindi", "english": "English"}
ADDRESS_FORM_LABELS = {
    "feminine": "Feminine word forms",
    "masculine": "Masculine word forms",
    "neutral": "Neutral word forms",
}


class SummaryItem(BaseModel):
    id: str
    group: GroupId
    kind: str | None = None
    label: str
    source: Source
    source_label: str
    updated_at: datetime | None = None
    # memory: Edit/Delete through /api/memory; profile: through /api/profile;
    # order: read-only (remove all order history instead).
    managed_by: Literal["memory", "profile", "order"] = "memory"
    item: str | None = None
    value: str | None = None


class SummaryGroup(BaseModel):
    id: GroupId
    title: str
    items: list[SummaryItem]


class SummaryConsent(BaseModel):
    saved_preferences: Literal["on", "off"]
    purchase_history: Literal["on", "off"]


class MemorySummary(BaseModel):
    consent: SummaryConsent
    groups: list[SummaryGroup]
    suggestions: list[SummaryItem]
    total: int


def group_for(memory: MemoryItem) -> GroupId:
    kind = kind_of(memory)
    if kind in {"household", "delivery_note", "routine"}:
        return "home"
    if kind in {"diet", "avoid_ingredient"} or memory.memory_type == "dietary_preference":
        return "food"
    if kind in {"avoid_product", "avoid_brand", "avoid_store"} or "avoid" in memory.memory_type:
        return "avoid"
    if kind in {"prefer_brand", "prefer_pack_size", "usual_quantity", "usual_item"}:
        return "usual_buys"
    if kind == "prefer_store" and item_of(memory):
        return "usual_buys"
    return "shopping_style"


def source_of(memory: MemoryItem) -> Source:
    if memory.source_type in {MemorySourceType.OBSERVED, MemorySourceType.INFERRED}:
        return "repeated_choice"
    value = memory.value if isinstance(memory.value, dict) else {}
    if value.get("basis") == "order_history":
        return "order_history"
    return "in_passing" if origin_of(memory) == "stated" else "told"


def _label(memory: MemoryItem) -> str:
    text = " ".join(memory.text.split())[:160]
    return text[:1].upper() + text[1:] if text else "Saved preference"


def summary_item(memory: MemoryItem) -> SummaryItem:
    source = source_of(memory)
    return SummaryItem(
        id=str(memory.id),
        group=group_for(memory),
        kind=kind_of(memory),
        label=_label(memory),
        source=source,
        source_label=SOURCE_LABELS[source],
        updated_at=memory.confirmed_at or memory.updated_at or memory.created_at,
        item=item_of(memory),
        value=display_value_of(memory),
    )


async def _profile_items(session: AsyncSession, external_id: str) -> list[SummaryItem]:
    record = await get_profile_record(session, external_id)
    if record is None:
        return []
    items: list[SummaryItem] = []
    common = {
        "group": "home",
        "source": "told",
        "source_label": SOURCE_LABELS["told"],
        "updated_at": record.updated_at,
        "managed_by": "profile",
    }
    if record.first_name:
        items.append(SummaryItem(id="profile:first_name", kind="first_name", label=f"Call you: {record.first_name}", value=record.first_name, **common))  # type: ignore[arg-type]
    items.append(SummaryItem(id="profile:language", kind="language", label=f"Reply language: {LANGUAGE_LABELS.get(record.language, record.language)}", value=record.language, **common))  # type: ignore[arg-type]
    items.append(SummaryItem(id="profile:address_form", kind="address_form", label=f"How to address you: {ADDRESS_FORM_LABELS.get(record.address_form, record.address_form).lower()}", value=record.address_form, **common))  # type: ignore[arg-type]
    return items


async def _order_items(session: AsyncSession, actor: MemorySubject) -> tuple[bool, list[SummaryItem]]:
    graph = PurchaseGraphService(session)
    if not await graph.has_history_permission(actor):
        return False, []
    purchases = await graph.recent_purchases(
        actor, lambda _name: True, limit=200, now=datetime.now(UTC)
    )
    purchases.sort(key=lambda item: (item.purchase_count, item.last_purchased_at), reverse=True)
    stores = {"swiggy": "swiggy_instamart", "zepto": "zepto"}
    items = []
    for index, purchase in enumerate(purchases[:MAX_ORDER_ITEMS], 1):
        store = STORE_LABELS.get(stores.get(purchase.provider, purchase.provider), purchase.provider)
        times = f"{purchase.purchase_count} {'time' if purchase.purchase_count == 1 else 'times'}"
        items.append(
            SummaryItem(
                id=f"order:{index}",
                group="from_orders",
                label=f"{purchase.product_name} — bought {times} on {store}",
                source="order_history",
                source_label=SOURCE_LABELS["order_history"],
                updated_at=purchase.last_purchased_at,
                managed_by="order",
            )
        )
    return True, items


async def summarize_memory(session: AsyncSession, external_id: str) -> MemorySummary:
    actor = await MemoryRepository(session).find_person(external_id)
    if actor is None:
        return MemorySummary(
            consent=SummaryConsent(saved_preferences="off", purchase_history="off"),
            groups=[],
            suggestions=[],
            total=0,
        )
    service = HouseholdPreferenceService(session)
    memories = await service.memories(actor, {MemoryStatus.CONFIRMED, MemoryStatus.PROPOSED})
    confirmed = [summary_item(memory) for memory in memories if memory.status == MemoryStatus.CONFIRMED]
    suggestions = [summary_item(memory) for memory in memories if memory.status == MemoryStatus.PROPOSED]
    history_on, orders = await _order_items(session, actor)
    everything = [*await _profile_items(session, external_id), *confirmed, *orders]
    newest = datetime.min.replace(tzinfo=UTC)

    def recency(item: SummaryItem) -> datetime:
        when = item.updated_at or newest
        return when if when.tzinfo else when.replace(tzinfo=UTC)

    groups = []
    for group_id, title in GROUP_TITLES.items():
        rows = [item for item in everything if item.group == group_id]
        if group_id == "usual_buys":
            rows.sort(key=lambda item: ((item.item or "").casefold(), item.label))
        elif group_id != "from_orders":
            rows.sort(key=lambda item: (item.managed_by != "profile", -recency(item).timestamp()))
        if rows:
            groups.append(SummaryGroup(id=group_id, title=title, items=rows))  # type: ignore[arg-type]
    return MemorySummary(
        consent=SummaryConsent(
            saved_preferences="on" if await service.consent_on(actor) else "off",
            purchase_history="on" if history_on else "off",
        ),
        groups=groups,
        suggestions=suggestions,
        total=sum(len(group.items) for group in groups),
    )


def spoken_top(summary: MemorySummary, limit: int = 5) -> tuple[list[SummaryItem], int]:
    """The few items a voice answer reads, in SPOKEN_ORDER, and how many are left."""
    by_group = {group.id: group.items for group in summary.groups}
    ordered = [
        item
        for group_id in SPOKEN_ORDER
        for item in by_group.get(group_id, [])  # type: ignore[call-overload]
        if item.managed_by != "profile"
    ]
    return ordered[:limit], max(0, summary.total - min(limit, len(ordered)))
