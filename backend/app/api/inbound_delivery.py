"""The inbound delivery line: config, saved directions, share, status and clear."""

from fastapi import APIRouter, Query, Response, status

from app.auth import UserIdDependency
from app.inbound_delivery import (
    DeliveryLine,
    DirectionsUpdate,
    InboundDeliveryConfig,
    InboundDeliveryServiceDependency,
    LineSettingsUpdate,
    PrepareRequest,
    SavedDirections,
)
from app.inbound_delivery.models import Provider
from app.legal_consent import ConsentedUserIdDependency


router = APIRouter()


@router.get("/config", response_model=InboundDeliveryConfig)
async def get_inbound_config(
    service: InboundDeliveryServiceDependency, user_id: UserIdDependency
) -> InboundDeliveryConfig:
    return service.config(user_id)


@router.get("/directions", response_model=SavedDirections | None)
async def get_directions(
    service: InboundDeliveryServiceDependency,
    user_id: UserIdDependency,
    provider: Provider,
    address_id: str = Query(min_length=1, max_length=128),
) -> SavedDirections | None:
    return await service.directions(user_id, provider, address_id)


@router.put("/directions", response_model=SavedDirections)
async def put_directions(
    body: DirectionsUpdate,
    service: InboundDeliveryServiceDependency,
    user_id: UserIdDependency,
) -> SavedDirections:
    return await service.save_directions(user_id, body)


@router.delete("/directions", status_code=status.HTTP_204_NO_CONTENT)
async def delete_directions(
    service: InboundDeliveryServiceDependency,
    user_id: UserIdDependency,
    provider: Provider,
    address_id: str = Query(min_length=1, max_length=128),
) -> Response:
    await service.delete_directions(user_id, provider, address_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/line", response_model=DeliveryLine)
async def get_line(
    service: InboundDeliveryServiceDependency, user_id: UserIdDependency
) -> DeliveryLine:
    return await service.line(user_id)


@router.put("/line/settings", response_model=DeliveryLine)
async def put_line_settings(
    body: LineSettingsUpdate,
    service: InboundDeliveryServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> DeliveryLine:
    return await service.update_settings(user_id, body)


@router.delete("/line", response_model=DeliveryLine)
async def clear_line(
    service: InboundDeliveryServiceDependency, user_id: UserIdDependency
) -> DeliveryLine:
    return await service.revoke(user_id, "user_cleared")


@router.post("/contexts", response_model=DeliveryLine)
async def prepare_inbound_context(
    body: PrepareRequest,
    service: InboundDeliveryServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> DeliveryLine:
    return await service.prepare(user_id, body)


@router.get("/contexts/{cohort_id}", response_model=DeliveryLine)
async def get_inbound_context(
    cohort_id: str,
    service: InboundDeliveryServiceDependency,
    user_id: UserIdDependency,
) -> DeliveryLine:
    return await service.status(user_id, cohort_id)
