from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.conversations.repository import ConversationRepository
from app.conversations.summary import (
    conversation_title,
    discussed_items,
    is_empty_conversation,
    message_count,
)
from app.dependencies import ConversationSummariesDependency
from app.conversations.schemas import (
    ConversationStateResponse,
    ConversationStateUpsert,
    ConversationSummary,
)
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency
from app.database.models import Conversation


router = APIRouter()


def _summary(conversation: Conversation) -> ConversationSummary:
    state = conversation.state
    messages = state.get("messages") if isinstance(state, dict) else None
    all_messages = [
        " ".join(str(item.get("content", "")).split())
        for item in messages or []
        if isinstance(item, dict)
    ]
    title = conversation_title(state)
    preview_source = next((text for text in reversed(all_messages) if text), title)
    return ConversationSummary(
        id=conversation.id,
        title=title,
        preview=preview_source[:160],
        updated_at=conversation.updated_at,
        is_active=conversation.archived_at is None,
        message_count=message_count(state),
        items=discussed_items(state),
    )


@router.get("", response_model=list[ConversationSummary])
async def recent_conversations(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=30, ge=1, le=50),
    exclude_empty: bool = Query(
        default=False,
        description="Hide chats with no message, search result or basket card.",
    ),
) -> list[ConversationSummary]:
    conversations = await ConversationRepository(session).list_recent(actor.id, limit=100)
    summaries = [
        _summary(conversation)
        for conversation in conversations
        if not (exclude_empty and is_empty_conversation(conversation.state))
    ]
    clean_query = query.strip().casefold()
    if clean_query:
        summaries = [
            item for item in summaries
            if clean_query in item.title.casefold() or clean_query in item.preview.casefold()
        ]
    return summaries[:limit]


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
    summaries: ConversationSummariesDependency,
) -> ConversationStateResponse:
    conversation = await ConversationRepository(session).upsert(
        actor.id,
        body.model_dump(mode="json"),
    )
    summaries.after_save(actor.id, conversation.id, conversation.state, conversation.summary)
    return ConversationStateResponse(
        id=conversation.id,
        updated_at=conversation.updated_at,
        **conversation.state,
    )


@router.post("/new", status_code=status.HTTP_204_NO_CONTENT)
async def start_new_conversation(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    summaries: ConversationSummariesDependency,
) -> None:
    repository = ConversationRepository(session)
    current = await repository.current(actor.id)
    await repository.archive_current(actor.id)
    if current is not None:
        summaries.after_archive(actor.id, [current.id])


@router.post("/{conversation_id}/open", response_model=ConversationStateResponse)
async def open_conversation(
    conversation_id: UUID,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    summaries: ConversationSummariesDependency,
) -> ConversationStateResponse:
    repository = ConversationRepository(session)
    previous = await repository.current(actor.id)
    previous_id = previous.id if previous is not None else None
    conversation = await repository.open(actor.id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if previous_id is not None and previous_id != conversation.id:
        summaries.after_archive(actor.id, [previous_id])
    return ConversationStateResponse(
        id=conversation.id,
        updated_at=conversation.updated_at,
        **conversation.state,
    )
