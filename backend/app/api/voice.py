import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.dependencies import SidebandHubDependency, VoiceServiceDependency
from app.voice.sideband import FINISHED
from app.voice.service import LiveSession, VoiceConfigurationError, VoiceProviderError
from app.legal_consent import ConsentedUserIdDependency


router = APIRouter()

KEEPALIVE_SECONDS = 15


class SessionRequest(BaseModel):
    sdp: str = Field(min_length=1, max_length=64 * 1024)
    swiggy_address_id: str | None = Field(default=None, min_length=1, max_length=256)
    zepto_address_id: str | None = Field(default=None, min_length=1, max_length=256)
    # Id of the browser's pre-negotiated ``oai-events`` data channel (WARP).
    dcid: int | None = Field(default=None, ge=0, le=65534)

    @field_validator("sdp")
    @classmethod
    def must_contain_audio(cls, value: str) -> str:
        if "m=audio" not in value:
            raise ValueError("SDP offer must include an audio track")
        return value


def _check_origin(request: Request) -> None:
    if request.headers.get("origin") not in get_settings().allowed_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unexpected request origin.")


@router.post(
    "/session",
    response_model=LiveSession,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    body: SessionRequest,
    request: Request,
    service: VoiceServiceDependency,
    # Voice sessions can edit baskets and request bills.
    user_id: ConsentedUserIdDependency,

) -> LiveSession:
    _check_origin(request)
    try:
        return await service.create(
            body.sdp,
            body.swiggy_address_id,
            body.zepto_address_id,
            user_id,
            data_channel_id=body.dcid,
        )
    except VoiceConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except VoiceProviderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/prewarm", status_code=status.HTTP_204_NO_CONTENT)
async def prewarm_session(
    request: Request,
    service: VoiceServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> None:
    """Open the OpenAI connection while the user reaches for the mic.

    Sends no user data and creates no session; repeated calls are throttled.
    """

    del user_id
    _check_origin(request)
    await service.prewarm()


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: Annotated[str, Path(min_length=1, max_length=256)],
    request: Request,
    hub: SidebandHubDependency,
    token: Annotated[str | None, Query(max_length=256)] = None,
) -> StreamingResponse:
    """Stream UI events for a voice session to the user who created it.

    EventSource cannot send the Clerk bearer, so the stream is authorised by
    the per-session ``events_token`` returned (to the verified user) from
    ``POST /api/voice/session``. A missing, wrong or expired token gets the
    same 404 as an unknown session, so session ids cannot be probed.
    """

    _check_origin(request)
    channel = hub.authorized_channel(session_id, token)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown voice session.")

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    backlog = list(channel.history)
    if not channel.finished:
        channel.subscribers.append(queue)

    async def stream() -> AsyncIterator[str]:
        try:
            for event in backlog:
                yield _sse(event)
                if event is FINISHED:
                    return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
                if event is FINISHED:
                    return
        finally:
            if queue in channel.subscribers:
                channel.subscribers.remove(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
