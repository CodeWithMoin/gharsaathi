"""Prepare caller context for the deployed inbound Sarvam delivery line."""

from fastapi import APIRouter

from app.auth import UserIdDependency
from app.inbound_delivery import (
    DeliveryContextRequest,
    DeliveryContextStatus,
    InboundDeliveryConfig,
    InboundDeliveryServiceDependency,
)
from app.legal_consent import ConsentedUserIdDependency


router = APIRouter()


@router.get("/config", response_model=InboundDeliveryConfig)
async def get_inbound_config(
    service: InboundDeliveryServiceDependency, user_id: UserIdDependency
) -> InboundDeliveryConfig:
    del user_id
    return service.config()


@router.post("/contexts", response_model=DeliveryContextStatus)
async def prepare_inbound_context(
    body: DeliveryContextRequest,
    service: InboundDeliveryServiceDependency,
    user_id: ConsentedUserIdDependency,
) -> DeliveryContextStatus:
    return await service.prepare(user_id, body)


@router.get("/contexts/{cohort_id}", response_model=DeliveryContextStatus)
async def get_inbound_context(
    cohort_id: str,
    service: InboundDeliveryServiceDependency,
    user_id: UserIdDependency,
) -> DeliveryContextStatus:
    return await service.status(user_id, cohort_id)
