from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, status
from pydantic import BaseModel

from app.auth import UserIdDependency
from app.rider.service import RiderAssistService, get_rider_service
from app.rider.store import RiderCall, RiderSettings, RiderSettingsUpdate


router = APIRouter()
RiderServiceDependency = Annotated[RiderAssistService, Depends(get_rider_service)]


class RiderCalls(BaseModel):
    calls: list[RiderCall]


@router.get("/settings", response_model=RiderSettings)
async def rider_settings(user_id: UserIdDependency, service: RiderServiceDependency) -> RiderSettings:
    return await service.settings_for(user_id)


@router.put("/settings", response_model=RiderSettings)
async def update_rider_settings(
    body: RiderSettingsUpdate, user_id: UserIdDependency, service: RiderServiceDependency
) -> RiderSettings:
    await service.store.update(user_id, body)
    return await service.settings_for(user_id)


@router.get("/calls", response_model=RiderCalls)
async def rider_calls(user_id: UserIdDependency, service: RiderServiceDependency) -> RiderCalls:
    return RiderCalls(calls=await service.store.list_calls(user_id))


@router.post("/calls/{call_id}/seen", status_code=status.HTTP_204_NO_CONTENT)
async def mark_rider_call_seen(
    call_id: UUID, user_id: UserIdDependency, service: RiderServiceDependency
) -> Response:
    if not await service.store.mark_seen(user_id, call_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rider call not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/calls", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rider_calls(user_id: UserIdDependency, service: RiderServiceDependency) -> Response:
    await service.store.delete_all_calls(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/calls/{call_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rider_call(
    call_id: UUID, user_id: UserIdDependency, service: RiderServiceDependency
) -> Response:
    if not await service.store.delete_call(user_id, call_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rider call not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.websocket("/stream")
async def rider_stream(websocket: WebSocket, service: RiderServiceDependency) -> None:
    """Exotel Voicebot applet stream. Basic auth + feature flag + owner switch, see service."""

    await service.handle(websocket)
