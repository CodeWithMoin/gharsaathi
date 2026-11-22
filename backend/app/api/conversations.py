from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.conversations.repository import ConversationRepository
from app.conversations.schemas import (
    ConversationStateResponse,
    ConversationStateUpsert,
    ConversationSummary,
)
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency
from app.database.models import Conversation
from app.database.session import get_session_factory
from app.memory.choice_signals import run_choice_check


router = APIRouter()


def _summary(conversation: Conversation) -> ConversationSummary:
    state = conversation.state
    messages = state.get("messages") if isinstance(state, dict) else None
    user_messages = [
        item.get("content", "").strip()
        for item in messages or []
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    all_messages = [
        item.get("content", "").strip()
        for item in messages or []
        if isinstance(item, dict)
    ]
    fallback = str(state.get("submitted_query", "")).strip() if isinstance(state, dict) else ""
    title_source = next((text for text in user_messages if text), fallback or "Shopping chat")
    preview_source = next((text for text in reversed(all_messages) if text), title_source)
    return ConversationSummary(
        id=conversation.id,
        title=title_source[:80],
        preview=preview_source[:160],
        updated_at=conversation.updated_at,
        is_active=conversation.archived_at is None,
    )


@router.get("", response_model=list[ConversationSummary])
async def recent_conversations(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=30, ge=1, le=50),
) -> list[ConversationSummary]:
    conversations = await ConversationRepository(session).list_recent(actor.id, limit=50)
    summaries = [_summary(conversation) for conversation in conversations]
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
    background: BackgroundTasks,
) -> ConversationStateResponse:
    conversation = await ConversationRepository(session).upsert(
        actor.id,
        body.model_dump(mode="json"),
    )
    # Repeated basket choices may become a pending memory suggestion (never active).
    background.add_task(run_choice_check, get_session_factory(), actor.id)
    return ConversationStateResponse(
        id=conversation.id,
        updated_at=conversation.updated_at,
        **conversation.state,
    )


@router.post("/new", status_code=status.HTTP_204_NO_CONTENT)
async def start_new_conversation(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    background: BackgroundTasks,
) -> None:
    await ConversationRepository(session).archive_current(actor.id)
    background.add_task(run_choice_check, get_session_factory(), actor.id)


@router.post("/{conversation_id}/open", response_model=ConversationStateResponse)
async def open_conversation(
    conversation_id: UUID,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> ConversationStateResponse:
    conversation = await ConversationRepository(session).open(actor.id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return ConversationStateResponse(
        id=conversation.id,
        updated_at=conversation.updated_at,
        **conversation.state,
    )
