"""Prepare caller context for the deployed inbound Sarvam delivery line, and read call outcomes."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.auth import UserIdDependency
from app.dependencies import InboundCallStoreDependency
from app.inbound_calls import InboundRiderCalls
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


@router.get("/calls", response_model=InboundRiderCalls)
async def list_inbound_calls(
    store: InboundCallStoreDependency, user_id: UserIdDependency
) -> InboundRiderCalls:
    """This user's recent calls to the delivery line (stored summaries; no Sarvam request)."""

    return InboundRiderCalls(calls=await store.list_calls(user_id))


@router.delete("/calls", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbound_calls(
    store: InboundCallStoreDependency, user_id: UserIdDependency
) -> Response:
    await store.delete_all(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/calls/{call_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbound_call(
    call_id: UUID, store: InboundCallStoreDependency, user_id: UserIdDependency
) -> Response:
    if not await store.delete_call(user_id, call_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
