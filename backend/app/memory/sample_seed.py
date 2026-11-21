"""Demo-only seed of one fictional sample household into one signed-in user's records.

Usage (from backend/, with COMMERCE_MODE=demo):
    uv run --with-requirements requirements.txt python -m app.memory.sample_seed \
        [--user-id CLERK_USER_ID] [--household USR-001] [--remove]

It refuses to run in live mode and never calls a commerce provider. Sample
orders are marked with a ``sample-`` order id and sample memories with the
``sample-household-seed`` source conversation, so ``--remove`` deletes only them.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.swiggy.models import OrderItem, OrderList, OrderSummary
from app.core.config import get_settings
from app.database.models import (
    MemoryItem,
    MemoryStatus,
    MemorySubject,
    PurchaseOrderNode,
    SubjectType,
)
from app.memory.purchase_graph import DATA_CATEGORY, PURPOSE, PurchaseGraphService
from app.memory.recall import PREFERENCE_PURPOSE, PREFERENCE_TYPES
from app.memory.repository import MemoryRepository
from app.memory.schemas import MemoryPermissionUpsert, MemoryProposal
from app.memory.service import MemoryService

SAMPLE_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sample_households.json"
SEED_MARKER = "sample-household-seed"
ORDER_PREFIX = "sample-"
SAMPLE_RETENTION_DAYS = 90


class SeedRefused(Exception):
    pass


def load_sample_households(path: Path = SAMPLE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sample_household(data: dict[str, Any], sample_id: str) -> dict[str, Any]:
    for household in data["households"]:
        if household["sample_id"] == sample_id:
            return household
    raise SeedRefused(f"Unknown sample household {sample_id}.")


def sample_order_list(data: dict[str, Any], household: dict[str, Any]) -> OrderList:
    hours, minutes = (int(part) for part in data["timezone"].lstrip("+").split(":"))
    zone = timezone(timedelta(hours=hours, minutes=minutes))
    orders = []
    for order in household["orders"]:
        when = datetime.fromisoformat(order["ordered_at_local"]).replace(tzinfo=zone)
        orders.append(
            OrderSummary(
                order_id=f"{ORDER_PREFIX}{household['sample_id']}-{order['source_id']}",
                status=order["status"],
                current_status=order["status"],
                history_status=order["status"],
                created_at=when,
                updated_at=when,
                item_count=len(order["items"]),
                total_paise=0,
                order_type="INSTAMART",
                is_active=False,
                items=[OrderItem(name=name, quantity=1) for name in order["items"]],
            )
        )
    return OrderList(orders=orders)


def require_demo_mode() -> None:
    if get_settings().commerce_mode != "demo":
        raise SeedRefused("Refusing to seed sample data: COMMERCE_MODE is not demo.")


async def seed_sample_household(
    session: AsyncSession, actor: MemorySubject, data: dict[str, Any], sample_id: str
) -> dict[str, int]:
    household = sample_household(data, sample_id)
    memory = MemoryService(session)
    repository = MemoryRepository(session)

    history = await repository.get_permission(actor.id, DATA_CATEGORY, PURPOSE)
    if history is None or not history.allowed or not history.retention_days:
        await memory.set_permission(
            actor,
            DATA_CATEGORY,
            PURPOSE,
            MemoryPermissionUpsert(
                allowed=True,
                granularity="sample_household_seed",
                retention_days=SAMPLE_RETENTION_DAYS,
            ),
        )
    for memory_type in PREFERENCE_TYPES:
        permission = await repository.get_permission(actor.id, memory_type, PREFERENCE_PURPOSE)
        if permission is None or not permission.allowed:
            await memory.set_permission(
                actor,
                memory_type,
                PREFERENCE_PURPOSE,
                MemoryPermissionUpsert(allowed=True, granularity="explicit_preferences"),
            )

    existing = {
        (item.memory_type, json.dumps(item.value, sort_keys=True))
        for item in await repository.list_memories(actor.id, MemoryStatus.CONFIRMED, False)
    }
    added_memories = 0
    for entry in household["memories"]:
        key = (entry["memory_type"], json.dumps(entry["value"], sort_keys=True))
        if key in existing:
            continue
        proposal = await memory.propose(
            actor,
            MemoryProposal(
                memory_type=entry["memory_type"],
                value=entry["value"],
                text=entry["text"],
                source_type="explicit_statement",
                source_conversation_id=SEED_MARKER,
                allowed_purposes=[PREFERENCE_PURPOSE],
            ),
        )
        await memory.confirm(actor, proposal.id)
        added_memories += 1

    imported = await PurchaseGraphService(session).import_orders(
        actor, sample_order_list(data, household), provider="swiggy"
    )
    return {"memories": added_memories, "orders": imported.imported_orders}


async def remove_sample_data(session: AsyncSession, actor: MemorySubject) -> dict[str, int]:
    orders = await session.execute(
        delete(PurchaseOrderNode)
        .where(
            PurchaseOrderNode.subject_id == actor.id,
            PurchaseOrderNode.provider_order_id.startswith(ORDER_PREFIX),
        )
        .execution_options(synchronize_session=False)
    )
    memories = (
        await session.execute(
            select(MemoryItem).where(
                MemoryItem.subject_id == actor.id,
                MemoryItem.source_conversation_id == SEED_MARKER,
                MemoryItem.status != MemoryStatus.DELETED,
            )
        )
    ).scalars().all()
    service = MemoryService(session)
    for item in memories:
        await service.forget(actor, item.id)
    await PurchaseGraphService(session)._prune_expired(actor.id, datetime.now(UTC))
    await session.commit()
    return {"memories": len(memories), "orders": orders.rowcount or 0}  # type: ignore[attr-defined]


async def resolve_actor(session: AsyncSession, user_id: str | None) -> MemorySubject:
    if user_id:
        actor = await MemoryRepository(session).find_person(user_id)
        if actor is None:
            raise SeedRefused("That user has no records yet. Sign in to the app once first.")
        return actor
    people = (
        await session.execute(
            select(MemorySubject).where(MemorySubject.subject_type == SubjectType.PERSON)
        )
    ).scalars().all()
    if len(people) != 1:
        listed = ", ".join(person.external_id for person in people) or "none"
        raise SeedRefused(f"Pass --user-id; signed-in users found: {listed}.")
    return people[0]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", help="Clerk user id; defaults to the only signed-in user.")
    parser.add_argument("--household", default="USR-001")
    parser.add_argument("--remove", action="store_true", help="Delete previously seeded sample data.")
    args = parser.parse_args()
    try:
        require_demo_mode()
        from app.database.session import get_session_factory

        async with get_session_factory()() as session:
            actor = await resolve_actor(session, args.user_id)
            if args.remove:
                result = await remove_sample_data(session, actor)
                print(f"Removed {result['orders']} sample orders and {result['memories']} sample memories.")
            else:
                result = await seed_sample_household(
                    session, actor, load_sample_households(), args.household
                )
                print(
                    f"Seeded {args.household}: {result['orders']} new orders, "
                    f"{result['memories']} new confirmed memories."
                )
    except SeedRefused as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    asyncio.run(main())
