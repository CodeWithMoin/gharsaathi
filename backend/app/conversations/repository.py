from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current(self, subject_id: UUID) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation)
            .where(
                Conversation.subject_id == subject_id,
                Conversation.archived_at.is_(None),
            )
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert(self, subject_id: UUID, state: dict[str, object]) -> Conversation:
        conversation = await self.current(subject_id)
        if conversation is None:
            conversation = Conversation(subject_id=subject_id, state=state)
            self.session.add(conversation)
        else:
            conversation.state = state
            conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def archive_current(self, subject_id: UUID) -> None:
        now = datetime.now(UTC)
        await self.session.execute(
            update(Conversation)
            .where(
                Conversation.subject_id == subject_id,
                Conversation.archived_at.is_(None),
            )
            .values(archived_at=now, updated_at=now)
        )
        await self.session.commit()
