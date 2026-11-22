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
            conversation = Conversation(
                subject_id=subject_id,
                state=state,
                updated_at=datetime.now(UTC),
            )
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

    async def get(self, subject_id: UUID, conversation_id: UUID) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.subject_id == subject_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_archived_since(
        self, subject_id: UUID, since: datetime, limit: int = 20
    ) -> list[Conversation]:
        """The user's own archived chats updated since ``since``, newest first."""

        result = await self.session.execute(
            select(Conversation)
            .where(
                Conversation.subject_id == subject_id,
                Conversation.archived_at.is_not(None),
                Conversation.updated_at >= since,
            )
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def store_summary(
        self, subject_id: UUID, conversation_id: UUID, summary: dict[str, object]
    ) -> bool:
        """Write the summary only to this user's row; ``updated_at`` is unchanged."""

        result = await self.session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.subject_id == subject_id,
            )
            .values(summary=summary, updated_at=Conversation.updated_at)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def list_recent(self, subject_id: UUID, limit: int = 50) -> list[Conversation]:
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.subject_id == subject_id)
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def open(self, subject_id: UUID, conversation_id: UUID) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.subject_id == subject_id,
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            return None

        now = datetime.now(UTC)
        await self.session.execute(
            update(Conversation)
            .where(
                Conversation.subject_id == subject_id,
                Conversation.archived_at.is_(None),
                Conversation.id != conversation_id,
            )
            .values(archived_at=now, updated_at=now)
        )
        conversation.archived_at = None
        conversation.updated_at = now
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation
