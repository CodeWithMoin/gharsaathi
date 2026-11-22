import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.chat.service import (
    ChatConfigurationError,
    ChatProviderError,
    ChatTimeout,
)
from app.conversations.repository import ConversationRepository
from app.database.session import get_session_factory
from app.dependencies import ChatServiceDependency, PhotoStoreDependency
from app.legal_consent import ConsentedUserIdDependency
from app.memory.service import MemoryService
from app.photos.images import MAX_UPLOAD_BYTES, PhotoRejected, make_thumbnail, sanitize_photo
from app.photos.store import TTL_SECONDS
from app.voice.ui_ack import UiAck


logger = logging.getLogger(__name__)
router = APIRouter()

MAX_TYPED_MESSAGE_CHARS = 2_000
ATTACHMENT_ID_PATTERN = r"^ph_[A-Za-z0-9_-]{16,40}$"
# Multipart framing on top of the 8 MB image.
MAX_MULTIPART_BYTES = MAX_UPLOAD_BYTES + 64 * 1024


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    # May be empty only when a photo is attached.
    message: str = Field(default="", max_length=MAX_TYPED_MESSAGE_CHARS)
    attachment_id: str | None = Field(default=None, pattern=ATTACHMENT_ID_PATTERN)
    swiggy_address_id: str | None = Field(default=None, min_length=1, max_length=256)
    zepto_address_id: str | None = Field(default=None, min_length=1, max_length=256)
    # What the browser actually showed after the previous reply (address form).
    ui_acks: list[UiAck] = Field(default_factory=list, max_length=4)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def message_or_photo(self) -> "ChatTurnRequest":
        if not self.message and self.attachment_id is None:
            raise ValueError("message must not be empty")
        return self


class AttachmentResponse(BaseModel):
    attachment_id: str
    expires_in_seconds: int
    width: int
    height: int
    # Small metadata-free JPEG data URL for the chat bubble; saved with the chat.
    thumbnail: str | None = None


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
            attachment_id=body.attachment_id,
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


@router.post("/attachments", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    request: Request,
    file: UploadFile,
    store: PhotoStoreDependency,
    # A photo turns into searches and basket suggestions, like any typed turn.
    user_id: ConsentedUserIdDependency,
) -> AttachmentResponse:
    """Accept one photo, strip every bit of metadata, and keep it for 15 minutes at most."""

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_MULTIPART_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The photo is larger than 8 MB.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    try:
        clean = sanitize_photo(data)
    except PhotoRejected as exc:
        code = {
            "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
            "unsupported_type": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        }.get(exc.reason, status.HTTP_422_UNPROCESSABLE_CONTENT)
        logger.info("Photo upload refused: %s", exc.reason)
        raise HTTPException(code, str(exc)) from exc
    photo = store.put(user_id, clean.jpeg)
    return AttachmentResponse(
        attachment_id=photo.attachment_id,
        expires_in_seconds=int(TTL_SECONDS),
        width=clean.width,
        height=clean.height,
        thumbnail=make_thumbnail(clean.jpeg),
    )


@router.delete(
    "/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_attachment(
    attachment_id: str,
    store: PhotoStoreDependency,
    user_id: ConsentedUserIdDependency,
) -> Response:
    """Remove a pending photo (the chip's remove button). Idempotent; never reveals others' ids."""

    store.discard(user_id, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
