from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from app.dependencies import VoiceSessionDependency
from app.voice.client import RealtimeSessionError
from app.voice.schemas import RealtimeClientSecret


router = APIRouter()


@router.post("/session", response_model=RealtimeClientSecret)
async def create_voice_session(
    voice: VoiceSessionDependency,
    user_id: Annotated[str, Header(alias="X-User-Id", min_length=1, max_length=200)],
) -> RealtimeClientSecret:
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise HTTPException(status_code=422, detail="X-User-Id cannot be blank.")
    try:
        return await voice.create_client_secret(normalized_user_id)
    except RealtimeSessionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
