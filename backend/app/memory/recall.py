"""Read-only, consent-gated recall of one person's remembered choices for an item.

Used by the voice ``recall_household_preferences`` tool. It reads only the
verified person's own confirmed explicit memories and, when purchase-history
consent is granted, their own delivered purchase history. It never writes,
never infers stock or need, and never calls a commerce provider.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import MemoryItem, MemorySubject
from app.memory.purchase_graph import PurchaseGraphService
from app.memory.repository import MemoryRepository
from app.memory.schemas import MemoryRetrieveRequest
from app.memory.service import MemoryService

PREFERENCE_PURPOSE = "personalize_search"
PREFERENCE_TYPES = (
    "brand_preference",
    "pack_size_preference",
    "quantity_preference",
    "product_avoidance",
)
STORE_LABELS = {"swiggy": "Swiggy Instamart", "zepto": "Zepto"}
MAX_PER_SOURCE = 3
MAX_CANDIDATES = 4

# Words that describe the request rather than the item ("deo order karna hai").
_STOPWORDS = {
    "and", "for", "the", "some", "any", "pack", "packet", "wala", "wali", "wale",
    "order", "karna", "karni", "karo", "kar", "hai", "hain", "chahiye", "lena",
    "lana", "mangwana", "mangao", "buy", "get", "need", "want", "please", "mujhe",
    "humko", "hamein", "ghar", "liye", "mera", "meri", "usual", "wahi", "same",
}
# Small, explicit synonym groups so common Hinglish item words find English names.
_ALIASES = (
    {"deo", "deodorant", "body spray"},
    {"tel", "oil"},
    {"atta", "flour"},
    {"doodh", "milk"},
    {"chawal", "rice"},
    {"chai", "tea"},
    {"sabun", "soap"},
    {"anda", "ande", "egg"},
    {"namak", "salt"},
    {"cheeni", "sugar"},
)


class PreferenceCandidate(BaseModel):
    source: Literal["confirmed_preference", "purchase_history"]
    preference: str | None = None
    brand: str | None = None
    product: str | None = None
    product_category: str | None = None
    pack_size: str | None = None
    summary: str | None = None
    product_name: str | None = None
    store: str | None = None
    last_purchased_on: str | None = None
    purchase_count: int | None = None


class RecallConsent(BaseModel):
    saved_preferences: Literal["on", "off"]
    purchase_history: Literal["on", "off"]


class HouseholdRecall(BaseModel):
    consent: RecallConsent
    candidates: list[PreferenceCandidate]


class PreferenceRecaller(Protocol):
    async def recall(self, household_id: str, item: str) -> HouseholdRecall: ...


def item_terms(item: str) -> set[str]:
    """Searchable terms for a free-text item, with small Hinglish/English aliases."""
    words = [word for word in re.split(r"[^a-z0-9]+", item.casefold()) if word]
    terms = {
        word[:-1] if len(word) > 4 and word.endswith("s") else word
        for word in words
        if len(word) >= 3 and word not in _STOPWORDS and not word.isdigit()
    }
    expanded = set(terms)
    for group in _ALIASES:
        if terms & group:
            expanded |= group
    return expanded


def term_matcher(terms: set[str]) -> Callable[[str], bool]:
    patterns = [re.compile(rf"\b{re.escape(term)}") for term in terms]

    def matches(text: str) -> bool:
        folded = text.casefold()
        return any(pattern.search(folded) for pattern in patterns)

    return matches


def _preference_candidate(memory: MemoryItem) -> PreferenceCandidate:
    value = {
        str(key): str(raw).strip()
        for key, raw in memory.value.items()
        if isinstance(raw, (str, int, float)) and str(raw).strip()
    }
    return PreferenceCandidate(
        source="confirmed_preference",
        preference=memory.memory_type.removesuffix("_preference"),
        brand=value.get("brand"),
        product=value.get("product"),
        product_category=value.get("product_category"),
        pack_size=value.get("pack_size"),
        summary=memory.text,
    )


def _memory_haystack(memory: MemoryItem) -> str:
    parts = [
        str(memory.value.get(key, ""))
        for key in ("product_category", "product", "brand", "alternative_brand")
    ]
    return " ".join([*parts, memory.text])


class DatabasePreferenceRecaller:
    """Opens a short read-only session per call; scoped to one external identity."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def recall(self, household_id: str, item: str) -> HouseholdRecall:
        async with self.session_factory() as session:
            return await recall_for_person(session, household_id, item)


async def recall_for_person(
    session: AsyncSession, external_id: str, item: str, now: datetime | None = None
) -> HouseholdRecall:
    repository = MemoryRepository(session)
    actor = await repository.find_person(external_id)
    if actor is None:
        return HouseholdRecall(
            consent=RecallConsent(saved_preferences="off", purchase_history="off"),
            candidates=[],
        )
    matches = term_matcher(item_terms(item))
    preferences_on = await _preferences_allowed(repository, actor)
    preferences: list[PreferenceCandidate] = []
    if preferences_on:
        memories = await MemoryService(session).retrieve(
            actor,
            MemoryRetrieveRequest(
                purpose=PREFERENCE_PURPOSE, memory_types=list(PREFERENCE_TYPES), limit=100
            ),
        )
        preferences = [
            _preference_candidate(memory)
            for memory in memories
            if matches(_memory_haystack(memory))
        ][:MAX_PER_SOURCE]

    graph = PurchaseGraphService(session)
    history_on = await graph.has_history_permission(actor)
    history: list[PreferenceCandidate] = []
    if history_on:
        purchases = await graph.recent_purchases(
            actor, matches, limit=MAX_PER_SOURCE, now=now or datetime.now(UTC)
        )
        history = [
            PreferenceCandidate(
                source="purchase_history",
                product_name=purchase.product_name,
                store=STORE_LABELS.get(purchase.provider, purchase.provider),
                last_purchased_on=purchase.last_purchased_at.date().isoformat(),
                purchase_count=purchase.purchase_count,
            )
            for purchase in purchases
        ]
    return HouseholdRecall(
        consent=RecallConsent(
            saved_preferences="on" if preferences_on else "off",
            purchase_history="on" if history_on else "off",
        ),
        candidates=[*preferences, *history][:MAX_CANDIDATES],
    )


async def _preferences_allowed(repository: MemoryRepository, actor: MemorySubject) -> bool:
    for memory_type in PREFERENCE_TYPES:
        permission = await repository.get_permission(actor.id, memory_type, PREFERENCE_PURPOSE)
        if permission is not None and permission.allowed and permission.revoked_at is None:
            return True
    return False
