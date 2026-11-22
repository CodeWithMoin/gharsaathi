"""Tier 2 memory: repeated basket choices become a pending suggestion, never a rule.

Deterministic, no LLM. ``record_choice`` stores one basket add while preference
memory is on. ``propose_from_choices`` runs after a conversation is saved or
archived and creates at most a PROPOSED memory when the same choice repeats in
``MIN_OCCASIONS`` separate conversations:

- the same brand and pack for the same item -> prefer_brand (with pack size);
- the same store every time both stores offered the item -> prefer_store.

A suggestion that was rejected, deleted or already exists is never proposed
again. Only the person's explicit yes makes it active.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import MemoryChoiceSignal, MemoryItem, MemorySubject
from app.memory.errors import MemoryDomainError
from app.memory.household import PreferenceSpec
from app.memory.household_service import HouseholdPreferenceService, _fingerprint
from app.memory.recall import item_terms
from app.memory.remembered import name_tokens, pack_key
from app.memory.repository import MemoryRepository

logger = logging.getLogger(__name__)

MIN_OCCASIONS = 3
MAX_SIGNALS_PER_PERSON = 300
_UNIT_OR_ORDER = re.compile(
    r"^\d+(\.\d+)?$|^(ml|l|ltr|litre|litres|liter|g|gm|gms|gram|grams|kg|kgs|pc|pcs|pack|packet|x)$"
)


def generic_item(query: str, product_name: str, brand: str | None) -> str:
    """The item noun of a search ("Amul Taaza milk 1 litre" -> "milk").

    The last non-brand, non-size word, preferring words the product name shares;
    item nouns come last in both Hinglish and English product phrases.
    """
    brand_words = name_tokens(brand or "")
    product_words = name_tokens(product_name)

    def candidates(source: str) -> list[str]:
        return [
            word
            for word in re.split(r"[^\w]+", source.casefold())
            if word
            and word not in brand_words
            and not _UNIT_OR_ORDER.match(word)
            and item_terms(word)
        ]

    query_words = candidates(query)
    shared = [word for word in query_words if word in product_words]
    for words in (shared, query_words, candidates(product_name)):
        if words:
            return words[-1][:60]
    return product_name.casefold()[:60]


async def record_choice(
    session: AsyncSession,
    actor: MemorySubject,
    *,
    occasion_key: str,
    provider: str,
    item: str,
    product_name: str,
    brand: str | None,
    pack_size: str | None,
    competing_providers: list[str],
) -> None:
    session.add(
        MemoryChoiceSignal(
            subject_id=actor.id,
            occasion_key=occasion_key[:128],
            provider=provider[:32],
            item=item[:120],
            product_name=product_name[:500],
            brand=(brand or None) and brand[:200],
            pack_size=(pack_size or None) and pack_size[:120],
            competing_providers=sorted(set(competing_providers) - {provider}),
        )
    )
    await session.flush()
    stale = (
        select(MemoryChoiceSignal.id)
        .where(MemoryChoiceSignal.subject_id == actor.id)
        .order_by(MemoryChoiceSignal.created_at.desc())
        .offset(MAX_SIGNALS_PER_PERSON)
    )
    stale_ids = list((await session.execute(stale)).scalars())
    if stale_ids:
        await session.execute(delete(MemoryChoiceSignal).where(MemoryChoiceSignal.id.in_(stale_ids)))
    await session.commit()


async def delete_choices(session: AsyncSession, subject_id: UUID) -> None:
    await session.execute(delete(MemoryChoiceSignal).where(MemoryChoiceSignal.subject_id == subject_id))
    await session.commit()


async def propose_from_choices(session: AsyncSession, actor: MemorySubject) -> list[MemoryItem]:
    service = HouseholdPreferenceService(session)
    if not await service.consent_on(actor):
        return []
    signals = list(
        (
            await session.execute(
                select(MemoryChoiceSignal).where(MemoryChoiceSignal.subject_id == actor.id)
            )
        ).scalars()
    )
    if not signals:
        return []
    known = {
        _fingerprint(memory.memory_type, memory.value)
        for memory in await MemoryRepository(session).list_memories(actor.id, None, include_deleted=True)
    }
    created: list[MemoryItem] = []

    by_product: dict[tuple[str, str, tuple | None], list[MemoryChoiceSignal]] = defaultdict(list)
    for signal in signals:
        if signal.brand:
            by_product[(signal.item, signal.brand.casefold(), pack_key(signal.pack_size))].append(signal)
    for (item, _brand, _pack), group in by_product.items():
        if len({signal.occasion_key for signal in group}) < MIN_OCCASIONS:
            continue
        latest = max(group, key=lambda signal: signal.created_at)
        spec = _spec("prefer_brand", item, latest.brand or "")
        if spec is None or _fingerprint(spec.memory_type, spec.to_value("inferred")) in known:
            continue
        extra = {"pack_size": latest.pack_size} if latest.pack_size else {}
        memory = await _propose(service, actor, spec, extra)
        if memory is not None:
            known.add(_fingerprint(spec.memory_type, spec.to_value("inferred")))
            created.append(memory)

    by_item: dict[str, list[MemoryChoiceSignal]] = defaultdict(list)
    for signal in signals:
        if signal.competing_providers:
            by_item[signal.item].append(signal)
    for item, group in by_item.items():
        occasions = {signal.occasion_key for signal in group}
        chosen = {signal.provider for signal in group}
        if len(occasions) < MIN_OCCASIONS or len(chosen) != 1:
            continue
        spec = _spec("prefer_store", item, next(iter(chosen)))
        if spec is None or _fingerprint(spec.memory_type, spec.to_value("inferred")) in known:
            continue
        memory = await _propose(service, actor, spec, {})
        if memory is not None:
            known.add(_fingerprint(spec.memory_type, spec.to_value("inferred")))
            created.append(memory)
    return created


def _spec(kind: str, item: str, value: str) -> PreferenceSpec | None:
    try:
        return PreferenceSpec(kind=kind, item=item, value=value)  # type: ignore[arg-type]
    except ValueError:
        return None


async def _propose(
    service: HouseholdPreferenceService,
    actor: MemorySubject,
    spec: PreferenceSpec,
    extra: dict[str, str],
) -> MemoryItem | None:
    try:
        memory, created = await service.propose(actor, spec, origin="inferred", extra_value=extra)
    except MemoryDomainError:
        return None
    return memory if created else None


async def run_choice_check(
    session_factory: async_sessionmaker[AsyncSession], subject_id: UUID
) -> None:
    """Background trigger after a conversation save/archive; never raises."""
    try:
        async with session_factory() as session:
            actor = await session.get(MemorySubject, subject_id)
            if actor is not None:
                await propose_from_choices(session, actor)
    except Exception as exc:  # noqa: BLE001 - a suggestion must never break chat saving
        logger.warning("event=memory_choice_check_failed error=%s", type(exc).__name__)
