"""Rider assist data: the owner's switch, delivery notes and call summaries."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import RiderAssistSettingsRecord, RiderCallRecord
from app.database.upsert import insert_if_absent
from app.rider.safety import mentions_secret, redact_secrets

Handoff = Literal["call_again", "leave_at_door", "leave_with_guard", "hand_to_me"]
NotesLanguage = Literal["hinglish", "hindi", "english"]
Urgency = Literal["low", "normal", "high"]
CallOutcome = Literal["in_progress", "answered", "message_taken", "not_delivery", "assistant_unavailable", "ended_early"]

HANDOFF_TEXT: dict[str, str] = {
    "call_again": "If nobody answers the door, call the owner again.",
    "leave_at_door": "The owner is fine with the order being left at the door.",
    "leave_with_guard": "The owner is fine with the order being left with the security guard.",
    "hand_to_me": "Hand the order to the owner in person only.",
}
MAX_CALLS_LISTED = 20
_URL = re.compile(r"(?i)\bhttps?://|\bwww\.")


def _clean_note(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(unicodedata.normalize("NFC", value).split())
    if not cleaned:
        return None
    if any(unicodedata.category(char) == "Cc" for char in cleaned):
        raise ValueError("Remove special characters.")
    if mentions_secret(cleaned):
        raise ValueError("Delivery notes can't contain OTPs, PINs, passwords or payment details.")
    if _URL.search(cleaned):
        raise ValueError("Delivery notes can't contain links.")
    return cleaned


class DeliveryNotes(BaseModel):
    """What the owner allows the assistant to tell a delivery partner."""

    model_config = ConfigDict(extra="forbid")

    landmark: str | None = Field(default=None, max_length=160)
    directions: str | None = Field(default=None, max_length=300)
    handoff: Handoff = "call_again"
    preferred_language: NotesLanguage = "hinglish"

    @field_validator("landmark", "directions", mode="before")
    @classmethod
    def clean(cls, value: object) -> object:
        return _clean_note(value) if isinstance(value, str) or value is None else value


class RiderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    notes: DeliveryNotes | None = None


class RiderSettings(BaseModel):
    feature_enabled: bool
    owner_configured: bool
    stream_auth_configured: bool
    enabled: bool
    notes: DeliveryNotes

    @property
    def active(self) -> bool:
        return self.feature_enabled and self.owner_configured and self.enabled


class RiderCallSummary(BaseModel):
    """What one call left behind for the owner (no audio, no full transcript)."""

    started_at: datetime
    ended_at: datetime | None = None
    caller: str = Field(max_length=16)
    delivery_call: bool | None = None
    outcome: CallOutcome
    urgency: Urgency = "normal"
    rider_said: str | None = Field(default=None, max_length=160)
    summary: str = Field(max_length=600)
    answered_from: list[Literal["notes", "order"]] = Field(default_factory=list)


class RiderCall(RiderCallSummary):
    id: str
    seen: bool


def _notes(record: RiderAssistSettingsRecord | None) -> DeliveryNotes:
    if record is None:
        return DeliveryNotes()
    return DeliveryNotes.model_construct(
        landmark=record.landmark,
        directions=record.directions,
        handoff=record.handoff if record.handoff in HANDOFF_TEXT else "call_again",
        preferred_language=record.preferred_language
        if record.preferred_language in {"hinglish", "hindi", "english"}
        else "hinglish",
    )


def _call(record: RiderCallRecord) -> RiderCall:
    return RiderCall(
        id=str(record.id),
        seen=record.seen_at is not None,
        started_at=record.started_at,
        ended_at=record.ended_at,
        caller=record.caller,
        delivery_call=record.delivery_call,
        outcome=record.outcome,  # type: ignore[arg-type]
        urgency=record.urgency,  # type: ignore[arg-type]
        rider_said=record.rider_said,
        summary=record.summary,
        answered_from=[item for item in record.answered_from or [] if item in {"notes", "order"}],
    )


class RiderStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @staticmethod
    async def _record(session: AsyncSession, user_id: str) -> RiderAssistSettingsRecord | None:
        return await session.scalar(
            select(RiderAssistSettingsRecord).where(RiderAssistSettingsRecord.user_id == user_id)
        )

    async def enabled_and_notes(self, user_id: str) -> tuple[bool, DeliveryNotes]:
        async with self.sessions() as session:
            record = await self._record(session, user_id)
        return (bool(record and record.enabled), _notes(record))

    async def update(self, user_id: str, change: RiderSettingsUpdate) -> tuple[bool, DeliveryNotes]:
        async with self.sessions() as session:
            await insert_if_absent(
                session,
                RiderAssistSettingsRecord,
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "enabled": False,
                    "handoff": "call_again",
                    "preferred_language": "hinglish",
                },
                ["user_id"],
            )
            record = await session.scalar(
                select(RiderAssistSettingsRecord)
                .where(RiderAssistSettingsRecord.user_id == user_id)
                .with_for_update()
            )
            assert record is not None
            if change.enabled is not None:
                record.enabled = change.enabled
            if change.notes is not None:
                record.landmark = change.notes.landmark
                record.directions = change.notes.directions
                record.handoff = change.notes.handoff
                record.preferred_language = change.notes.preferred_language
            await session.commit()
            await session.refresh(record)
            return (record.enabled, _notes(record))

    async def add_call(self, user_id: str, call: RiderCallSummary) -> RiderCall:
        record = RiderCallRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            started_at=call.started_at,
            ended_at=call.ended_at,
            caller=call.caller,
            delivery_call=call.delivery_call,
            outcome=call.outcome,
            urgency=call.urgency,
            rider_said=redact_secrets(call.rider_said) if call.rider_said else None,
            summary=redact_secrets(call.summary),
            answered_from=list(call.answered_from),
        )
        async with self.sessions() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return _call(record)

    async def update_call(self, user_id: str, call_id: str, call: RiderCallSummary) -> bool:
        async with self.sessions() as session:
            result = await session.execute(
                update(RiderCallRecord)
                .where(RiderCallRecord.user_id == user_id, RiderCallRecord.id == uuid.UUID(call_id))
                .values(
                    ended_at=call.ended_at,
                    delivery_call=call.delivery_call,
                    outcome=call.outcome,
                    urgency=call.urgency,
                    rider_said=redact_secrets(call.rider_said) if call.rider_said else None,
                    summary=redact_secrets(call.summary),
                    answered_from=list(call.answered_from),
                )
            )
            await session.commit()
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list_calls(self, user_id: str, limit: int = MAX_CALLS_LISTED) -> list[RiderCall]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(RiderCallRecord)
                    .where(RiderCallRecord.user_id == user_id)
                    .order_by(RiderCallRecord.started_at.desc())
                    .limit(limit)
                )
            ).all()
        return [_call(row) for row in rows]

    async def mark_seen(self, user_id: str, call_id: uuid.UUID) -> bool:
        async with self.sessions() as session:
            result = await session.execute(
                update(RiderCallRecord)
                .where(
                    RiderCallRecord.user_id == user_id,
                    RiderCallRecord.id == call_id,
                    RiderCallRecord.seen_at.is_(None),
                )
                .values(seen_at=datetime.now(UTC))
            )
            await session.commit()
            if result.rowcount:  # type: ignore[attr-defined]
                return True
            exists = await session.scalar(
                select(RiderCallRecord.id).where(
                    RiderCallRecord.user_id == user_id, RiderCallRecord.id == call_id
                )
            )
            return exists is not None

    async def delete_call(self, user_id: str, call_id: uuid.UUID) -> bool:
        async with self.sessions() as session:
            result = await session.execute(
                delete(RiderCallRecord).where(
                    RiderCallRecord.user_id == user_id, RiderCallRecord.id == call_id
                )
            )
            await session.commit()
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def delete_all_calls(self, user_id: str) -> int:
        async with self.sessions() as session:
            result = await session.execute(
                delete(RiderCallRecord).where(RiderCallRecord.user_id == user_id)
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]


def outcome_text(call: RiderCallSummary) -> str:
    """How the call went, in a few words (same wording as the app's rider-call card)."""

    used = set(call.answered_from)
    if call.outcome == "assistant_unavailable":
        return "the assistant couldn't answer"
    if call.outcome == "not_delivery":
        return "not a delivery call, message taken"
    if call.outcome == "in_progress":
        return "the rider is on the line"
    if used == {"notes", "order"}:
        return "answered from your notes and order"
    if "notes" in used:
        return "answered from your notes"
    if "order" in used:
        return "answered from your order"
    if call.outcome == "message_taken":
        return "message taken"
    return "call ended"
