"""Write and manage one person's structured household preferences.

Used by the voice/typed memory tools and the Settings REST routes. Every write
is consent-gated (the "remember my preferences" switch) and passes the
sensitive-data policy in ``app.memory.household``. Forget, reject and undo never
need consent: a person can always remove what was remembered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    MemoryChoiceSignal,
    MemoryEventType,
    MemoryItem,
    MemorySourceType,
    MemoryStatus,
    MemorySubject,
)
from app.memory.errors import (
    MemoryDomainError,
    MemoryNotFoundError,
    MemoryPermissionError,
    MemoryTransitionError,
)
from app.memory.household import (
    CONSENT_TYPES,
    HOUSEHOLD_PREFERENCE_TYPES,
    PREFERENCE_PURPOSE,
    MemoryOrigin,
    PreferenceSpec,
    origin_of,
    sensitive_category,
    spec_of,
    text_for_value,
)
from app.memory.repository import MemoryRepository
from app.memory.schemas import MemoryCorrection, MemoryPermissionUpsert, MemoryProposal
from app.memory.service import MemoryService

FORGET_ALL_TTL_SECONDS = 300
UNDO_WINDOW = timedelta(hours=24)
# Process-local signing key: a forget-all confirmation token is valid only on the
# server process that issued it, for five minutes.
_TOKEN_KEY = secrets.token_bytes(32)


class MemoryPolicyError(MemoryDomainError):
    """The preference falls under a category GharSaathi never remembers."""

    def __init__(self, category: str) -> None:
        super().__init__(f"GharSaathi does not remember {category.replace('_', ' ')} details.")
        self.category = category


class MemoryConsentError(MemoryPermissionError):
    pass


class ForgetAllTokenError(MemoryDomainError):
    pass


@dataclass(frozen=True)
class ForgetAllConfirmation:
    token: str
    count: int
    expires_at: datetime


def _fingerprint(memory_type: str, value: dict[str, object]) -> tuple[str, str, str]:
    item = str(value.get("product_category", "")).casefold().strip()
    main = next(
        (
            str(value[key]).casefold().strip()
            for key in ("brand", "pack_size", "store", "product", "rule")
            if key in value
        ),
        "",
    )
    return memory_type, item, main


def check_policy(spec: PreferenceSpec) -> None:
    category = sensitive_category(*spec.policy_texts())
    if category is not None:
        raise MemoryPolicyError(category)


class HouseholdPreferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = MemoryRepository(session)
        self.memory = MemoryService(session)

    # Consent -----------------------------------------------------------------
    async def consent_on(self, actor: MemorySubject) -> bool:
        """The Settings switch: any of the saved-preference categories allowed."""
        for memory_type in CONSENT_TYPES:
            permission = await self.repository.get_permission(
                actor.id, memory_type, PREFERENCE_PURPOSE
            )
            if permission is not None and permission.allowed and permission.revoked_at is None:
                return True
        return False

    async def set_consent(self, actor: MemorySubject, allowed: bool) -> bool:
        for memory_type in HOUSEHOLD_PREFERENCE_TYPES:
            await self.memory.set_permission(
                actor,
                memory_type,
                PREFERENCE_PURPOSE,
                MemoryPermissionUpsert(
                    allowed=allowed,
                    granularity="explicit_preferences" if allowed else "disabled",
                ),
            )
        return allowed

    async def _require_consent(self, actor: MemorySubject, memory_type: str) -> None:
        if not await self.consent_on(actor):
            raise MemoryConsentError("Preference memory is off for this person.")
        # The single switch covers every household type; keep the per-type
        # permission that retrieval checks in step with it.
        permission = await self.repository.get_permission(actor.id, memory_type, PREFERENCE_PURPOSE)
        if permission is None or not permission.allowed or permission.revoked_at is not None:
            await self.memory.set_permission(
                actor,
                memory_type,
                PREFERENCE_PURPOSE,
                MemoryPermissionUpsert(allowed=True, granularity="explicit_preferences"),
            )

    # Reads -------------------------------------------------------------------
    async def memories(
        self, actor: MemorySubject, statuses: set[MemoryStatus]
    ) -> list[MemoryItem]:
        items = await self.repository.list_memories(actor.id, None, include_deleted=False)
        now = datetime.now(UTC)
        return [
            item
            for item in items
            if item.status in statuses and not MemoryService._is_expired(item, now)
        ]

    async def active(self, actor: MemorySubject) -> list[MemoryItem]:
        """Confirmed preferences that may shape search, only while consent is on."""
        if not await self.consent_on(actor):
            return []
        return [
            item
            for item in await self.memories(actor, {MemoryStatus.CONFIRMED})
            if PREFERENCE_PURPOSE in item.allowed_purposes
        ]

    async def find_active(
        self, actor: MemorySubject, spec: PreferenceSpec
    ) -> MemoryItem | None:
        """An active memory of the same kind for the same item (or no item)."""
        item = (spec.item or "").casefold()
        for memory in await self.memories(actor, {MemoryStatus.CONFIRMED}):
            existing = spec_of(memory)
            if existing is None or existing.kind != spec.kind:
                continue
            if (existing.item or "").casefold() == item:
                return memory
        return None

    async def _duplicate(
        self, actor: MemorySubject, spec: PreferenceSpec, statuses: set[MemoryStatus]
    ) -> MemoryItem | None:
        wanted = _fingerprint(spec.memory_type, spec.to_value("explicit"))
        for memory in await self.memories(actor, statuses):
            if _fingerprint(memory.memory_type, memory.value) == wanted:
                return memory
        return None

    # Writes ------------------------------------------------------------------
    async def _proposal(
        self,
        spec: PreferenceSpec,
        origin: MemoryOrigin,
        conversation_id: str | None,
        source: MemorySourceType,
        extra_value: dict[str, str] | None = None,
    ) -> MemoryProposal:
        extra = {key: value for key, value in (extra_value or {}).items() if value}
        value = {**spec.to_value(origin), **extra}
        return MemoryProposal(
            memory_type=spec.memory_type,
            value=value,
            text=text_for_value(spec.kind, value),
            source_type=source,
            source_conversation_id=conversation_id,
            sensitivity="low",
            confidence=1.0 if origin != "inferred" else 0.7,
            allowed_purposes=[PREFERENCE_PURPOSE],
        )

    async def propose(
        self,
        actor: MemorySubject,
        spec: PreferenceSpec,
        *,
        origin: MemoryOrigin = "explicit",
        conversation_id: str | None = None,
        supersedes: MemoryItem | None = None,
        extra_value: dict[str, str] | None = None,
    ) -> tuple[MemoryItem, bool]:
        """A pending proposal; returns (memory, created). Never active until confirmed."""
        check_policy(spec)
        await self._require_consent(actor, spec.memory_type)
        existing = await self._duplicate(
            actor, spec, {MemoryStatus.CONFIRMED, MemoryStatus.PROPOSED}
        )
        if existing is not None:
            return existing, False
        source = (
            MemorySourceType.OBSERVED
            if origin == "inferred"
            else MemorySourceType.CORRECTION if supersedes is not None
            else MemorySourceType.EXPLICIT_STATEMENT
        )
        memory = await self.memory.propose(
            actor, await self._proposal(spec, origin, conversation_id, source, extra_value)
        )
        if supersedes is not None:
            memory.supersedes_memory_id = supersedes.id
            await self.session.commit()
            await self.session.refresh(memory)
        return memory, True

    async def save_stated(
        self,
        actor: MemorySubject,
        spec: PreferenceSpec,
        *,
        conversation_id: str | None = None,
    ) -> tuple[MemoryItem, bool]:
        """Tier 1: a habit stated in ordinary speech is active at once (undo-able)."""
        check_policy(spec)
        await self._require_consent(actor, spec.memory_type)
        existing = await self._duplicate(actor, spec, {MemoryStatus.CONFIRMED})
        if existing is not None:
            return existing, False
        previous = await self.find_active(actor, spec)
        if previous is not None:
            return await self.replace(actor, previous, spec, origin="stated"), True
        pending = await self._duplicate(actor, spec, {MemoryStatus.PROPOSED})
        if pending is not None:
            return await self.memory.confirm(actor, pending.id), True
        memory = await self.memory.propose(
            actor,
            await self._proposal(spec, "stated", conversation_id, MemorySourceType.EXPLICIT_STATEMENT),
        )
        return await self.memory.confirm(actor, memory.id), True

    async def confirm(self, actor: MemorySubject, memory_id: UUID) -> MemoryItem:
        memory = await self._owned(actor, memory_id)
        await self._require_consent(actor, memory.memory_type)
        return await self.memory.confirm(actor, memory_id)

    async def reject(
        self, actor: MemorySubject, memory_id: UUID, reason: str | None = None
    ) -> MemoryItem:
        return await self.memory.reject(actor, memory_id, reason)

    async def replace(
        self,
        actor: MemorySubject,
        previous: MemoryItem,
        spec: PreferenceSpec,
        *,
        origin: MemoryOrigin,
        reason: str = "User corrected this preference.",
        conversation_id: str | None = None,
        extra_value: dict[str, str] | None = None,
        source: MemorySourceType = MemorySourceType.CORRECTION,
    ) -> MemoryItem:
        """Supersede an active memory now; the old one is kept as superseded."""
        check_policy(spec)
        await self._require_consent(actor, spec.memory_type)
        base = await self._proposal(spec, origin, conversation_id, source, extra_value)
        return await self.memory.correct(
            actor,
            previous.id,
            MemoryCorrection(**base.model_dump(), reason=reason),
        )

    async def undo(self, actor: MemorySubject, memory_id: UUID) -> MemoryItem | None:
        """Remove a just-learned memory; restore the one it replaced, if any."""
        memory = await self._owned(actor, memory_id)
        if not self.undoable(memory):
            raise MemoryTransitionError(
                "Only a preference learned in passing in the last 24 hours can be undone."
            )
        restored: MemoryItem | None = None
        if memory.supersedes_memory_id is not None:
            previous = await self.repository.get_memory(memory.supersedes_memory_id, actor.id)
            if previous is not None and previous.status == MemoryStatus.SUPERSEDED:
                previous.status = MemoryStatus.CONFIRMED
                self.repository.add_event(
                    previous, MemoryEventType.CORRECTED, actor.id, "Restored by undo."
                )
                restored = previous
        await self.memory.forget(actor, memory.id)
        if restored is not None:
            await self.session.refresh(restored)
        return restored

    @staticmethod
    def undoable(memory: MemoryItem, now: datetime | None = None) -> bool:
        if memory.status != MemoryStatus.CONFIRMED or origin_of(memory) != "stated":
            return False
        learned = memory.confirmed_at or memory.created_at
        if learned.tzinfo is None:
            learned = learned.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) - learned < UNDO_WINDOW

    async def forget(self, actor: MemorySubject, memory_id: UUID) -> None:
        await self.memory.forget(actor, memory_id)

    async def forget_all(self, actor: MemorySubject) -> int:
        items = await self.repository.list_memories(actor.id, None, include_deleted=False)
        for item in items:
            await self.memory.forget(actor, item.id)
        # Repeated-choice signals go too, so nothing is re-suggested from before.
        await self.session.execute(
            delete(MemoryChoiceSignal).where(MemoryChoiceSignal.subject_id == actor.id)
        )
        await self.session.commit()
        return len(items)

    async def _owned(self, actor: MemorySubject, memory_id: UUID) -> MemoryItem:
        memory = await self.repository.get_memory(memory_id, actor.id)
        if memory is None:
            raise MemoryNotFoundError("Memory was not found for this person.")
        return memory

    # Forget-all confirmation -------------------------------------------------
    async def forget_all_confirmation(self, actor: MemorySubject) -> ForgetAllConfirmation:
        count = len(await self.repository.list_memories(actor.id, None, include_deleted=False))
        expires = int(time.time()) + FORGET_ALL_TTL_SECONDS
        return ForgetAllConfirmation(
            token=issue_forget_all_token(actor.id, expires),
            count=count,
            expires_at=datetime.fromtimestamp(expires, UTC),
        )

    async def forget_all_confirmed(self, actor: MemorySubject, token: str) -> int:
        verify_forget_all_token(actor.id, token)
        return await self.forget_all(actor)


def _signature(subject_id: UUID, expires: int) -> str:
    message = f"forget_all:{subject_id}:{expires}".encode()
    digest = hmac.new(_TOKEN_KEY, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:18]).decode().rstrip("=")


def issue_forget_all_token(subject_id: UUID, expires: int) -> str:
    return f"{expires}.{_signature(subject_id, expires)}"


_USED_TOKENS: dict[str, int] = {}


def verify_forget_all_token(subject_id: UUID, token: str, now: float | None = None) -> None:
    """Check signature, owner and expiry, then burn the token (single use)."""
    try:
        raw_expires, signature = token.split(".", 1)
        expires = int(raw_expires)
    except (ValueError, AttributeError) as exc:
        raise ForgetAllTokenError("Invalid confirmation token.") from exc
    if not hmac.compare_digest(signature, _signature(subject_id, expires)):
        raise ForgetAllTokenError("Invalid confirmation token.")
    current = now if now is not None else time.time()
    if expires < current:
        raise ForgetAllTokenError("Confirmation expired; ask again.")
    for used, used_expires in list(_USED_TOKENS.items()):
        if used_expires < current:
            del _USED_TOKENS[used]
    if token in _USED_TOKENS:
        raise ForgetAllTokenError("Confirmation already used; ask again.")
    _USED_TOKENS[token] = expires
