from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MemorySubject
from app.database.session import get_db_session
from app.core.config import get_settings
from app.memory.extraction import MemoryExtractor, create_memory_extractor
from app.memory.service import MemoryService
from app.auth import UserIdDependency


DbSessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


async def get_memory_actor(
    session: DbSessionDependency,
    user_id: UserIdDependency,
) -> MemorySubject:
    return await MemoryService(session).actor_for_external_id(user_id)


MemoryActorDependency = Annotated[MemorySubject, Depends(get_memory_actor)]


@lru_cache
def get_memory_extractor() -> MemoryExtractor:
    settings = get_settings()
    return create_memory_extractor(
        settings.openai_api_key.get_secret_value(),
        settings.memory_extraction_model,
    )


MemoryExtractorDependency = Annotated[MemoryExtractor, Depends(get_memory_extractor)]
