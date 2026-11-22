from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.chat.service import (
    ChatConfigurationError,
    ChatProviderError,
    ChatTimeout,
    HistoryMessage,
)
from app.conversations.repository import ConversationRepository
from app.database.session import get_session_factory
from app.dependencies import ChatServiceDependency
from app.legal_consent import ConsentedUserIdDependency
from app.memory.service import MemoryService


router = APIRouter()

MAX_TYPED_MESSAGE_CHARS = 2_000


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=MAX_TYPED_MESSAGE_CHARS)
    swiggy_address_id: str | None = Field(default=None, min_length=1, max_length=256)
    zepto_address_id: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be empty")
        return stripped


class ChatTurnResponse(BaseModel):
    conversation_id: UUID | None
    reply: str
    ui_events: list[dict[str, Any]]


def _saved_messages(state: object) -> list[HistoryMessage]:
    messages = state.get("messages") if isinstance(state, dict) else None
    return [
        HistoryMessage(item["role"], item["content"].strip())
        for item in messages or []
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]


async def _conversation_history(
    user_id: str, conversation_id: UUID | None
) -> tuple[UUID | None, list[HistoryMessage]]:
    # A short-lived session: the model call below can take many seconds and
    # must not hold a database connection open.
    async with get_session_factory()() as session:
        actor = await MemoryService(session).actor_for_external_id(user_id)
        repository = ConversationRepository(session)
        if conversation_id is None:
            conversation = await repository.current(actor.id)
        else:
            conversation = await repository.get(actor.id, conversation_id)
            if conversation is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        if conversation is None:
            return None, []
        return conversation.id, _saved_messages(conversation.state)


@router.post("/turn", response_model=ChatTurnResponse)
async def chat_turn(
    body: ChatTurnRequest,
    service: ChatServiceDependency,
    # A typed turn can edit baskets and request bills, exactly like a voice session.
    user_id: ConsentedUserIdDependency,
) -> ChatTurnResponse:
    conversation_id, history = await _conversation_history(user_id, body.conversation_id)
    try:
        result = await service.run_turn(
            user_id,
            conversation_id,
            body.message,
            history,
            body.swiggy_address_id,
            body.zepto_address_id,
        )
    except ChatConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ChatTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc)) from exc
    except ChatProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ChatTurnResponse(
        conversation_id=conversation_id, reply=result.reply, ui_events=result.ui_events
    )
