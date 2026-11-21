from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MemorySubject
from app.database.session import get_db_session
from app.core.config import get_settings
from app.memory.extraction import MemoryExtractor, create_memory_extractor
from app.memory.service import MemoryService


DbSessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


async def get_memory_actor(
    session: DbSessionDependency,
    user_id: Annotated[
        str,
        Header(
            alias="X-User-Id",
            min_length=1,
            max_length=128,
            description=(
                "Local-development identity only. Replace with an authenticated user ID "
                "before multi-user deployment."
            ),
        ),
    ],
) -> MemorySubject:
    normalized = user_id.strip()
    if not normalized:
        raise HTTPException(
            422,
            "X-User-Id must not be blank.",
        )
    return await MemoryService(session).actor_for_external_id(normalized)


MemoryActorDependency = Annotated[MemorySubject, Depends(get_memory_actor)]


@lru_cache
def get_memory_extractor() -> MemoryExtractor:
    settings = get_settings()
    return create_memory_extractor(
        settings.openai_api_key,
        settings.memory_extraction_model,
    )


MemoryExtractorDependency = Annotated[MemoryExtractor, Depends(get_memory_extractor)]
