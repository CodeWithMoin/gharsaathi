"""Versioned legal-consent persistence; legal acceptance is never inferred."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UserIdDependency
from app.database.models import LegalConsentRecord
from app.database.session import get_session_factory
from app.database.upsert import insert_if_absent


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
    """Persist an explicit acceptance once per user and legal version.

    Idempotent under concurrency: a double-submitted form returns the record
    written by whichever request committed first.
    """

    record = await current_consent(session, user_id)
    if record is not None:
        return record

    now = datetime.now(UTC)
    await insert_if_absent(
        session,
        LegalConsentRecord,
        {
            "id": uuid4(),
            "user_id": user_id,
            "terms_version": CURRENT_TERMS_VERSION,
            "accepted_at": now,
            "personalization_allowed": submission.personalization_allowed,
            "personalization_decided_at": now,
        },
        ["user_id", "terms_version"],
    )
    await session.commit()
    record = await current_consent(session, user_id)
    if record is None:  # pragma: no cover - the insert or its winner committed a row
        raise RuntimeError("Legal consent was not recorded.")
    return record


LEGAL_CONSENT_REQUIRED = "legal_consent_required"


class LegalConsentRequired(Exception):
    """The verified user has not accepted the current terms version."""

    code = LEGAL_CONSENT_REQUIRED
    message = "Accept the current Terms and Privacy Policy before continuing."


async def has_accepted_consent(user_id: UserIdDependency) -> bool:
    """Whether the verified user accepted the current terms version.

    Uses its own short-lived session so a slow provider call later in the
    request does not hold a database connection open in a transaction.
    """

    async with get_session_factory()() as session:
        return await current_consent(session, user_id) is not None


ConsentAcceptedDependency = Annotated[bool, Depends(has_accepted_consent)]


async def require_accepted_consent(
    user_id: UserIdDependency, accepted: ConsentAcceptedDependency
) -> str:
    """Gate consequential actions on server-side, current-version consent."""

    if not accepted:
        raise LegalConsentRequired()
    return user_id


ConsentedUserIdDependency = Annotated[str, Depends(require_accepted_consent)]
