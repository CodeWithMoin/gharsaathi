"""Read (and a few owner-initiated writes) over the local app's own data for ``gs`` and ``/api/dev``.

Everything here is scoped to one person: the Clerk user id is the household id
for baskets and orders, and resolves to a ``MemorySubject`` for memory and chats.
Amounts come from stored integer paise through the app's one formatter, or are
shown exactly as stored (order card ``payable`` strings). Nothing calls a store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Conversation,
    MemoryEvent,
    MemoryItem,
    MemoryStatus,
    MemorySubject,
    ProviderAddressState,
    ShoppingBasketRecord,
    SubjectType,
    WebPushEventRecord,
    ZeptoCheckoutAttemptRecord,
)
from app.memory.household import group_of, kind_of, origin_of
from app.memory.service import MemoryService
from app.shopping.basket import BasketService
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.money import format_rupees

PROVIDERS = ("swiggy_instamart", "zepto")
OPEN_STATUSES = ("draft", "awaiting_approval", "approved", "checkout_pending")
EXPORT_VERSION = 1


class DevDataError(Exception):
    """A message safe to print to the owner's terminal."""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="seconds")


def _age_minutes(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0, int((now - value).total_seconds() // 60))


def memory_row(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "memory_type": item.memory_type,
        "kind": kind_of(item),
        "group": group_of(item),
        "status": item.status.value,
        "origin": origin_of(item),
        "text": item.text,
        "created_at": _iso(item.created_at),
        "confirmed_at": _iso(item.confirmed_at),
        "deleted_at": _iso(item.deleted_at),
    }


def basket_row(record: ShoppingBasketRecord) -> dict[str, Any]:
    lines = record.lines if isinstance(record.lines, list) else []
    total = 0
    shown: list[dict[str, Any]] = []
    for line in lines:
        price = int(line.get("unit_price_paise") or 0)
        quantity = int(line.get("quantity") or 0)
        total += price * quantity
        shown.append(
            {
                "name": str(line.get("name", ""))[:120],
                "pack_size": line.get("pack_size"),
                "quantity": quantity,
                "unit_price": format_rupees(price),
                "line_total": format_rupees(price * quantity),
            }
        )
    return {
        "id": record.id,
        "provider": record.provider,
        "status": record.status,
        "revision": record.revision,
        "locked": record.status == "checkout_pending",
        "address_selected": record.address_id is not None,
        "item_count": sum(line["quantity"] for line in shown),
        "item_total": format_rupees(total),
        "lines": shown,
        "updated_at": _iso(record.updated_at),
    }


def attempt_row(record: ZeptoCheckoutAttemptRecord) -> dict[str, Any]:
    return {
        "attempt": record.id[:8],
        "status": record.status,
        "payment_method": record.payment_method,
        "payable": format_rupees(record.payable_paise),
        "order_code": record.provider_order_id,
        "provider_status": record.provider_status,
        "created_at": _iso(record.created_at),
        "completed_at": _iso(record.completed_at),
    }


def conversation_row(conversation: Conversation) -> dict[str, Any]:
    state = conversation.state if isinstance(conversation.state, dict) else {}
    messages = [m for m in state.get("messages", []) if isinstance(m, dict)]
    first = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    return {
        "id": str(conversation.id),
        "active": conversation.archived_at is None,
        "messages": len(messages),
        "title": " ".join(str(first).split())[:60],
        "updated_at": _iso(conversation.updated_at),
    }


def conversation_transcript(conversation: Conversation) -> dict[str, Any]:
    """Messages in order with the timeline cards placed after their message.

    Card amounts are shown as stored: an order card's ``payable`` string, a
    summary's stored paise through the app formatter.
    """

    state = conversation.state if isinstance(conversation.state, dict) else {}
    timeline = state.get("timeline") if isinstance(state.get("timeline"), dict) else {}
    cards_after: dict[str | None, list[dict[str, Any]]] = {}

    def place(after: Any, card: dict[str, Any], seq: Any) -> None:
        card["seq"] = seq if isinstance(seq, int) else 0
        cards_after.setdefault(after if isinstance(after, str) else None, []).append(card)

    for summary in timeline.get("summaries", []) or []:
        if not isinstance(summary, dict):
            continue
        paise = summary.get("item_total_paise")
        place(
            summary.get("after_message_id"),
            {
                "card": "basket",
                "provider": summary.get("provider"),
                "stage": summary.get("stage"),
                "items": summary.get("item_count"),
                "item_total": format_rupees(paise) if isinstance(paise, int) else None,
                "order_id": summary.get("order_id"),
            },
            summary.get("seq"),
        )
    for order in timeline.get("orders", []) or []:
        if not isinstance(order, dict):
            continue
        place(
            order.get("after_message_id"),
            {
                "card": "order",
                "provider": order.get("provider"),
                "order_id": order.get("order_id"),
                "status": order.get("status"),
                "items": order.get("item_count"),
                "payable": order.get("payable"),
            },
            order.get("seq"),
        )
    for group in state.get("result_groups", []) or []:
        if not isinstance(group, dict):
            continue
        place(
            group.get("after_message_id"),
            {
                "card": "results",
                "item": group.get("item_label"),
                "query": group.get("query"),
                "options": len(group.get("products") or []),
            },
            group.get("seq"),
        )
    for cards in cards_after.values():
        cards.sort(key=lambda card: card["seq"])
    entries: list[dict[str, Any]] = []
    for message in state.get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        entries.append(
            {
                "role": message.get("role"),
                "modality": message.get("modality", "text"),
                "at": message.get("created_at"),
                "text": message.get("content", ""),
                "photo": bool(message.get("photo_thumbnail")),
                "cards": cards_after.pop(message.get("id"), []),
            }
        )
    leftover = [card for cards in cards_after.values() for card in cards]
    return {**conversation_row(conversation), "entries": entries, "unplaced_cards": leftover}


class DevData:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tracking_path: Path | None = None) -> None:
        self.session_factory = session_factory
        self.tracking_path = tracking_path

    # People -------------------------------------------------------------------
    async def person(self, user_id: str | None) -> MemorySubject:
        async with self.session_factory() as session:
            query = select(MemorySubject).where(MemorySubject.subject_type == SubjectType.PERSON)
            if user_id:
                query = query.where(MemorySubject.external_id == user_id)
            people = list((await session.execute(query)).scalars())
        if user_id and not people:
            raise DevDataError("That user has no records yet. Sign in to the app once first.")
        if not user_id and len(people) > 1:
            # Local test identities aside, the owner is the one real Clerk user.
            clerk = [person for person in people if person.external_id.startswith("user_")]
            if len(clerk) == 1:
                return clerk[0]
        if len(people) != 1:
            listed = ", ".join(person.external_id for person in people) or "none"
            raise DevDataError(f"Pass --user-id; signed-in users found: {listed}.")
        return people[0]

    # Memory -------------------------------------------------------------------
    async def memories(self, actor: MemorySubject, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            query = select(MemoryItem).where(MemoryItem.subject_id == actor.id)
            if not include_deleted:
                query = query.where(MemoryItem.status != MemoryStatus.DELETED)
            items = (await session.execute(query.order_by(MemoryItem.created_at))).scalars()
            return [memory_row(item) for item in items]

    async def _memory(self, session: AsyncSession, actor: MemorySubject, ref: str) -> MemoryItem:
        ref = ref.strip().lower()
        if len(ref) < 4:
            raise DevDataError("Give at least 4 characters of the memory id.")
        items = list(
            (
                await session.execute(
                    select(MemoryItem).where(
                        MemoryItem.subject_id == actor.id,
                        func.lower(cast(MemoryItem.id, String)).like(f"{ref}%"),
                    )
                )
            ).scalars()
        )
        if not items:
            # SQLite stores UUIDs as hex without dashes.
            items = [
                item
                for item in (
                    await session.execute(select(MemoryItem).where(MemoryItem.subject_id == actor.id))
                ).scalars()
                if str(item.id).startswith(ref) or item.id.hex.startswith(ref.replace("-", ""))
            ]
        if not items:
            raise DevDataError(f"No memory starting with {ref}.")
        if len(items) > 1:
            raise DevDataError(f"{len(items)} memories start with {ref}; give more characters.")
        return items[0]

    async def memory(self, actor: MemorySubject, ref: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            item = await self._memory(session, actor, ref)
            events = list(
                (
                    await session.execute(
                        select(MemoryEvent).where(MemoryEvent.memory_id == item.id).order_by(MemoryEvent.created_at)
                    )
                ).scalars()
            )
            return {
                **memory_row(item),
                "value": item.value,
                "source_type": item.source_type.value,
                "sensitivity": item.sensitivity.value,
                "allowed_purposes": item.allowed_purposes,
                "expires_at": _iso(item.expires_at),
                "supersedes": str(item.supersedes_memory_id) if item.supersedes_memory_id else None,
                "events": [{"type": event.event_type.value, "at": _iso(event.created_at)} for event in events],
            }

    async def delete_memory(self, actor: MemorySubject, ref: str) -> dict[str, Any]:
        """Forget one memory exactly as Settings does (soft delete + audit event)."""

        async with self.session_factory() as session:
            item = await self._memory(session, actor, ref)
            if item.status == MemoryStatus.DELETED:
                raise DevDataError("That memory is already deleted.")
            await MemoryService(session).forget(actor, item.id)
            await session.refresh(item)
            return memory_row(item)

    async def export_memories(self, actor: MemorySubject) -> dict[str, Any]:
        async with self.session_factory() as session:
            items = (
                await session.execute(
                    select(MemoryItem)
                    .where(MemoryItem.subject_id == actor.id, MemoryItem.status != MemoryStatus.DELETED)
                    .order_by(MemoryItem.created_at)
                )
            ).scalars()
            return {
                "version": EXPORT_VERSION,
                "exported_at": _iso(datetime.now(UTC)),
                "user_id": actor.external_id,
                "items": [
                    {
                        "memory_type": item.memory_type,
                        "value": item.value,
                        "text": item.text,
                        "status": item.status.value,
                        "origin": origin_of(item),
                        "created_at": _iso(item.created_at),
                        "confirmed_at": _iso(item.confirmed_at),
                    }
                    for item in items
                ],
            }

    # Baskets ------------------------------------------------------------------
    async def baskets(self, household_id: str) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            records = (
                await session.execute(
                    select(ShoppingBasketRecord)
                    .where(
                        ShoppingBasketRecord.household_id == household_id,
                        ShoppingBasketRecord.status.in_(OPEN_STATUSES),
                    )
                    .order_by(ShoppingBasketRecord.provider)
                )
            ).scalars()
            return [basket_row(record) for record in records]

    async def clear_basket(self, household_id: str, provider: str) -> dict[str, Any]:
        if provider not in PROVIDERS:
            raise DevDataError(f"Unknown store {provider}; use one of {', '.join(PROVIDERS)}.")
        baskets = DatabaseBasketRepository(self.session_factory)
        current = await baskets.active_for(household_id, provider)  # type: ignore[arg-type]
        if current is None or not current.lines:
            return {"provider": provider, "cleared": False, "reason": "already empty"}
        if current.status.value == "checkout_pending":
            raise DevDataError(
                "This basket is locked: a checkout is in progress or its outcome is unknown. "
                "Run `gs orders reconcile --dry-run` first; it is never cleared here."
            )
        service = BasketService(baskets, DatabaseApprovalRepository(self.session_factory))
        basket = await service.clear(household_id, provider)  # type: ignore[arg-type]
        return {"provider": provider, "cleared": True, "removed_lines": len(current.lines), "revision": basket.revision}

    # Orders -------------------------------------------------------------------
    async def recent_orders(self, household_id: str, limit: int = 10) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            records = (
                await session.execute(
                    select(ZeptoCheckoutAttemptRecord)
                    .where(ZeptoCheckoutAttemptRecord.household_id == household_id)
                    .order_by(ZeptoCheckoutAttemptRecord.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
            return [attempt_row(record) for record in records]

    async def order_status(self, household_id: str, code: str) -> dict[str, Any]:
        code = code.strip()
        if not code:
            raise DevDataError("Give an order code or attempt prefix.")
        async with self.session_factory() as session:
            records = list(
                (
                    await session.execute(
                        select(ZeptoCheckoutAttemptRecord).where(
                            ZeptoCheckoutAttemptRecord.household_id == household_id
                        )
                    )
                ).scalars()
            )
        matches = [r for r in records if r.provider_order_id == code or r.id.startswith(code)]
        watches = [w for w in self.watches(household_id) if w.get("order_id") == code]
        if not matches and not watches:
            raise DevDataError(f"No order or attempt matches {code}.")
        if len(matches) > 1:
            raise DevDataError(f"{len(matches)} attempts match {code}; give more characters.")
        attempt = attempt_row(matches[0]) if matches else None
        if attempt and attempt["order_code"] and not watches:
            watches = [w for w in self.watches(household_id) if w.get("order_id") == attempt["order_code"]]
        return {"attempt": attempt, "tracking": watches[0] if watches else None}

    def watches(self, user_id: str) -> list[dict[str, Any]]:
        if self.tracking_path is None:
            return []
        from app.tracking.store import OrderTrackingStore

        rows: list[dict[str, Any]] = []
        for watch in OrderTrackingStore(self.tracking_path).list(user_id):
            snapshot = watch.snapshot
            rows.append(
                {
                    "monitor": watch.id[:8],
                    "provider": watch.provider.value,
                    "order_id": watch.order_id,
                    "active": watch.active,
                    "status": watch.status.value,
                    "provider_status": getattr(snapshot, "provider_status", None) if snapshot else None,
                    "eta_at": _iso(getattr(snapshot, "eta_at", None)) if snapshot else None,
                    "checked_at": _iso(watch.checked_at),
                    "next_check_at": _iso(watch.next_check_at),
                    "failures": watch.consecutive_failures,
                    "notifications": len(watch.notifications),
                    "stop_reason": watch.stop_reason,
                }
            )
        return rows

    # Conversations ------------------------------------------------------------
    async def conversations(self, actor: MemorySubject, limit: int = 15) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(Conversation)
                    .where(Conversation.subject_id == actor.id)
                    .order_by(Conversation.archived_at.is_(None).desc(), Conversation.updated_at.desc())
                    .limit(limit)
                )
            ).scalars()
            return [conversation_row(row) for row in rows]

    async def conversation(self, actor: MemorySubject, ref: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(Conversation)
                        .where(Conversation.subject_id == actor.id)
                        .order_by(Conversation.updated_at.desc())
                    )
                ).scalars()
            )
        if ref == "last":
            chosen = next((row for row in rows if row.archived_at is None), rows[0] if rows else None)
            if chosen is None:
                raise DevDataError("No conversations yet.")
            return conversation_transcript(chosen)
        ref = ref.strip().lower()
        matches = [row for row in rows if str(row.id).startswith(ref) or row.id.hex.startswith(ref.replace("-", ""))]
        if not matches:
            raise DevDataError(f"No conversation starting with {ref}.")
        if len(matches) > 1:
            raise DevDataError(f"{len(matches)} conversations start with {ref}; give more characters.")
        return conversation_transcript(matches[0])

    # Notifications and addresses ----------------------------------------------
    async def notifications(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(WebPushEventRecord)
                    .where(WebPushEventRecord.user_id == user_id)
                    .order_by(WebPushEventRecord.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
            return [
                {
                    "kind": row.kind,
                    "outcome": row.outcome,
                    # The dedupe key's first part names the event; ids after it stay out.
                    "event": row.dedupe_key.split(":", 1)[0][:40],
                    "at": _iso(row.created_at),
                }
                for row in rows
            ]

    async def zepto_address(self, actor: MemorySubject, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        async with self.session_factory() as session:
            state = (
                await session.execute(
                    select(ProviderAddressState).where(
                        ProviderAddressState.subject_id == actor.id, ProviderAddressState.provider == "zepto"
                    )
                )
            ).scalar_one_or_none()
        if state is None:
            return {"selected": False, "snapshot_age_min": None}
        return {
            "selected": state.selected_address_id is not None,
            "snapshot_age_min": _age_minutes(state.refreshed_at, now),
        }

