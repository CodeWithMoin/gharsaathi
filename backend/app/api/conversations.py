from fastapi import APIRouter, status

from app.conversations.repository import ConversationRepository
from app.conversations.schemas import ConversationStateResponse, ConversationStateUpsert
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency


router = APIRouter()


@router.get("/current", response_model=ConversationStateResponse | None)
async def current_conversation(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> ConversationStateResponse | None:
    conversation = await ConversationRepository(session).current(actor.id)
    if conversation is None:
        return None
    return ConversationStateResponse(
        id=conversation.id,
        updated_at=conversation.updated_at,
        **conversation.state,
    )


@router.put("/current", response_model=ConversationStateResponse)
async def save_current_conversation(
    body: ConversationStateUpsert,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> ConversationStateResponse:
    conversation = await ConversationRepository(session).upsert(
        actor.id,
        body.model_dump(mode="json"),
    )
    return ConversationStateResponse(
        id=conversation.id,
        updated_at=conversation.updated_at,
        **conversation.state,
    )


@router.post("/new", status_code=status.HTTP_204_NO_CONTENT)
async def start_new_conversation(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> None:
    await ConversationRepository(session).archive_current(actor.id)
