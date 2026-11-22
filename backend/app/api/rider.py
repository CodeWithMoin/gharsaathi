from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, status
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


async def _form(request: Request) -> dict[str, str]:
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - not a form body
        return {}
    return {key: str(value)[:256] for key, value in form.items() if isinstance(value, str)}


def _xml(reply) -> Response:
    if reply.xml is None:
        return Response(status_code=reply.status_code)
    return Response(content=reply.xml, status_code=reply.status_code, media_type="application/xml")


@router.post("/vobiz/answer", include_in_schema=False)
async def vobiz_answer(request: Request, service: RiderServiceDependency) -> Response:
    """Vobiz answer URL: signature-verified; returns <Stream> to this backend."""

    return _xml(await service.answer(await _form(request), request.headers))


@router.post("/vobiz/after-stream", include_in_schema=False)
async def vobiz_after_stream(request: Request, service: RiderServiceDependency) -> Response:
    """Runs after the stream ends: an apology if the assistant never answered, then hang up."""

    return _xml(await service.after_stream(await _form(request), request.headers))


@router.websocket("/stream/{call_id}/{token}")
async def rider_stream(
    websocket: WebSocket, call_id: str, token: str, service: RiderServiceDependency
) -> None:
    """Vobiz bidirectional <Stream>. The path is signed per call by the answer URL."""

    await service.handle(websocket, call_id, token)
