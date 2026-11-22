from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.chat.service import (
    ChatConfigurationError,
    ChatProviderError,
    ChatTimeout,
)
from app.conversations.repository import ConversationRepository
from app.database.session import get_session_factory
from app.dependencies import ChatServiceDependency
from app.legal_consent import ConsentedUserIdDependency
from app.memory.service import MemoryService
from app.voice.ui_ack import UiAck


router = APIRouter()

MAX_TYPED_MESSAGE_CHARS = 2_000


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=MAX_TYPED_MESSAGE_CHARS)
    swiggy_address_id: str | None = Field(default=None, min_length=1, max_length=256)
    zepto_address_id: str | None = Field(default=None, min_length=1, max_length=256)
    # What the browser actually showed after the previous reply (address form).
    ui_acks: list[UiAck] = Field(default_factory=list, max_length=4)

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


async def _resolve_conversation(user_id: str, conversation_id: UUID | None) -> tuple[UUID | None, bool]:
    """The user's own conversation id for this turn, and whether it is the current chat.

    The turn's context is read later by the shared conversation context loader,
    which reads the current chat. A short-lived session: the model call below
    can take many seconds and must not hold a database connection open.
    """

    async with get_session_factory()() as session:
        actor = await MemoryService(session).actor_for_external_id(user_id)
        repository = ConversationRepository(session)
        current = await repository.current(actor.id)
        if conversation_id is None:
            return (current.id if current is not None else None), True
        conversation = await repository.get(actor.id, conversation_id)
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        return conversation.id, current is not None and current.id == conversation.id


@router.post("/turn", response_model=ChatTurnResponse)
async def chat_turn(
    body: ChatTurnRequest,
    service: ChatServiceDependency,
    # A typed turn can edit baskets and request bills, exactly like a voice session.
    user_id: ConsentedUserIdDependency,
) -> ChatTurnResponse:
    conversation_id, current_chat = await _resolve_conversation(user_id, body.conversation_id)
    try:
        result = await service.run_turn(
            user_id,
            conversation_id,
            body.message,
            body.swiggy_address_id,
            body.zepto_address_id,
            current_chat=current_chat,
            ui_acks=body.ui_acks,
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
