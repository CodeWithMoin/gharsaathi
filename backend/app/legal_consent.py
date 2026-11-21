"""Versioned legal-consent persistence; legal acceptance is never inferred."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import LegalConsentRecord


CURRENT_TERMS_VERSION = "2026-11-22"


class LegalConsentSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terms_accepted: bool
    personalization_allowed: bool = False


class LegalConsentStatus(BaseModel):
    terms_version: str = Field(min_length=1, max_length=64)
    accepted: bool
    accepted_at: datetime | None = None
    personalization_allowed: bool = False
    personalization_decided_at: datetime | None = None


async def current_consent(
    session: AsyncSession, user_id: str
) -> LegalConsentRecord | None:
    result = await session.execute(
        select(LegalConsentRecord).where(
            LegalConsentRecord.user_id == user_id,
            LegalConsentRecord.terms_version == CURRENT_TERMS_VERSION,
        )
    )
    return result.scalar_one_or_none()


def status_for(record: LegalConsentRecord | None) -> LegalConsentStatus:
    return LegalConsentStatus(
        terms_version=CURRENT_TERMS_VERSION,
        accepted=record is not None,
        accepted_at=record.accepted_at if record else None,
        personalization_allowed=record.personalization_allowed if record else False,
        personalization_decided_at=record.personalization_decided_at if record else None,
    )


async def record_consent(
    session: AsyncSession, user_id: str, submission: LegalConsentSubmission
) -> LegalConsentRecord:
    """Persist an explicit acceptance once per user and legal version."""

    record = await current_consent(session, user_id)
    if record is not None:
        return record

    now = datetime.now(UTC)
    record = LegalConsentRecord(
        user_id=user_id,
        terms_version=CURRENT_TERMS_VERSION,
        accepted_at=now,
        personalization_allowed=submission.personalization_allowed,
        personalization_decided_at=now,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record
