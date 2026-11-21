from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.tracking.errors import TrackingConflict, TrackingNotFound, TrackingProviderError
from app.tracking.models import OrderWatch, StartTrackingRequest, TrackingRead
from app.tracking.service import OrderTrackingService


router = APIRouter()
LOCAL_HOUSEHOLD_ID = "local-household"


def get_tracking_service(request: Request) -> OrderTrackingService:
    """Integration seam: app startup attaches the single tracking service here."""

    service = getattr(request.app.state, "order_tracking_service", None)
    if not isinstance(service, OrderTrackingService):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Live order tracking is not configured yet.",
        )
    return service


def local_household(
    x_user_id: Annotated[str | None, Header()] = None,
) -> str:
    """Temporary local actor identity; multi-user auth must replace this boundary."""

    actor = (x_user_id or LOCAL_HOUSEHOLD_ID).strip()
    if not actor or len(actor) > 128:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid household.")
    return actor


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, TrackingNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, TrackingConflict):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, TrackingProviderError):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raise exc


TrackingServiceDependency = Annotated[OrderTrackingService, Depends(get_tracking_service)]
HouseholdDependency = Annotated[str, Depends(local_household)]


@router.post("/monitors", response_model=TrackingRead, status_code=status.HTTP_201_CREATED)
async def start_monitor(
    body: StartTrackingRequest,
    service: TrackingServiceDependency,
    household_id: HouseholdDependency,
) -> TrackingRead:
    try:
        return await service.start(household_id, body)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/monitors", response_model=list[OrderWatch])
async def list_monitors(
    service: TrackingServiceDependency,
    household_id: HouseholdDependency,
    active_only: bool = False,
) -> list[OrderWatch]:
    return service.list(household_id, active_only=active_only)


@router.get("/monitors/{monitor_id}", response_model=TrackingRead)
async def get_monitor(
    monitor_id: str,
    service: TrackingServiceDependency,
    household_id: HouseholdDependency,
) -> TrackingRead:
    try:
        return service.get(household_id, monitor_id)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/monitors/{monitor_id}/refresh", response_model=TrackingRead)
async def refresh_monitor(
    monitor_id: str,
    service: TrackingServiceDependency,
    household_id: HouseholdDependency,
) -> TrackingRead:
    try:
        # This obeys next_check_at; it cannot force a new provider call.
        return await service.refresh(household_id, monitor_id)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.delete("/monitors/{monitor_id}", response_model=TrackingRead)
async def stop_monitor(
    monitor_id: str,
    service: TrackingServiceDependency,
    household_id: HouseholdDependency,
) -> TrackingRead:
    try:
        return service.stop(household_id, monitor_id)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")
