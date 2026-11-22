"""How the signed-in user asked to be addressed. Always user-chosen, never inferred."""

import unicodedata
from datetime import datetime
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import UserProfileRecord
from app.database.upsert import insert_if_absent


AddressForm = Literal["masculine", "feminine", "neutral"]
ReplyLanguage = Literal["hinglish", "hindi", "english"]
ReplyStyle = Literal["quick", "detailed"]

_NAME_PUNCTUATION = set(" .'-")


class UserProfile(BaseModel):
    first_name: str | None = None
    address_form: AddressForm = "neutral"
    language: ReplyLanguage = "hinglish"
    reply_style: ReplyStyle = "quick"
    updated_at: datetime | None = None


class UserProfileUpdate(BaseModel):
    """Only the fields present in the request are changed."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=40)
    address_form: AddressForm | None = None
    language: ReplyLanguage | None = None
    reply_style: ReplyStyle | None = None

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(unicodedata.normalize("NFC", value).split())
        if not cleaned:
            return None
        # The name is spoken and placed in voice instructions: letters only.
        if not unicodedata.category(cleaned[0]).startswith("L") or not all(
            unicodedata.category(char)[0] in {"L", "M"} or char in _NAME_PUNCTUATION
            for char in cleaned
        ):
            raise ValueError("first_name may contain only letters, spaces, apostrophes, dots and hyphens")
        return cleaned


async def get_profile_record(session: AsyncSession, user_id: str) -> UserProfileRecord | None:
    result = await session.execute(
        select(UserProfileRecord).where(UserProfileRecord.user_id == user_id)
    )
    return result.scalar_one_or_none()


def profile_for(record: UserProfileRecord | None) -> UserProfile:
    if record is None:
        return UserProfile()
    return UserProfile.model_validate(
        {
            "first_name": record.first_name,
            "address_form": record.address_form,
            "language": record.language,
            "reply_style": record.reply_style or "quick",
            "updated_at": record.updated_at,
        }
    )


async def update_profile(
    session: AsyncSession, user_id: str, update: UserProfileUpdate
) -> UserProfileRecord:
    """Race-safe get-or-create, then a locked read-modify-write of the row.

    The row lock serialises concurrent partial updates so neither is lost.
    """

    await insert_if_absent(
        session,
        UserProfileRecord,
        {
            "id": uuid4(),
            "user_id": user_id,
            "address_form": "neutral",
            "language": "hinglish",
            "reply_style": "quick",
        },
        ["user_id"],
    )
    record = await session.scalar(
        select(UserProfileRecord)
        .where(UserProfileRecord.user_id == user_id)
        .with_for_update()
    )
    if record is None:  # pragma: no cover - the insert or its winner committed a row
        raise RuntimeError("User profile was not created.")
    fields = update.model_fields_set
    if "first_name" in fields:
        record.first_name = update.first_name
    if "address_form" in fields:
        record.address_form = update.address_form or "neutral"
    if "language" in fields:
        record.language = update.language or "hinglish"
    if "reply_style" in fields:
        record.reply_style = update.reply_style or "quick"
    await session.commit()
    await session.refresh(record)
    return record


class ProfileReader(Protocol):
    async def read(self, user_id: str) -> UserProfile | None: ...


class DatabaseProfileReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def read(self, user_id: str) -> UserProfile | None:
        async with self.session_factory() as session:
            record = await get_profile_record(session, user_id)
        return profile_for(record) if record is not None else None
