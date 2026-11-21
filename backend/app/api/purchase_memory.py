"""Consent-gated purchase graph APIs. No endpoint places or mutates an order."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.swiggy import _raise_http
from app.dependencies import SwiggyServiceDependency
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency
from app.memory.errors import MemoryNotFoundError, MemoryPermissionError
from app.memory.purchase_graph import (
    PurchaseGraphService,
    PurchaseImportResult,
    ReorderSuggestion,
)


router = APIRouter()


@router.post("/purchase-history/sync", response_model=PurchaseImportResult)
async def sync_purchase_history(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    swiggy: SwiggyServiceDependency,
) -> PurchaseImportResult:
    """Explicitly sync recent read-only history; never called automatically."""
    try:
        return await PurchaseGraphService(session).sync(actor, swiggy)
    except MemoryPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.delete("/purchase-history", status_code=status.HTTP_204_NO_CONTENT)
async def purge_purchase_history(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> None:
    await PurchaseGraphService(session).purge(actor)


@router.get("/reorder-suggestions", response_model=list[ReorderSuggestion])
async def reorder_suggestions(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
    timezone_offset_minutes: Annotated[
        int, Query(alias="timezoneOffsetMinutes", ge=-840, le=840)
    ] = 0,
) -> list[ReorderSuggestion]:
    try:
        return await PurchaseGraphService(session).suggestions(
            actor,
            limit=limit,
            context_offset_minutes=timezone_offset_minutes,
        )
    except MemoryPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post(
    "/reorder-suggestions/{product_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def dismiss_reorder_suggestion(
    product_id: UUID,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    timezone_offset_minutes: Annotated[
        int, Query(alias="timezoneOffsetMinutes", ge=-840, le=840)
    ] = 0,
) -> None:
    try:
        await PurchaseGraphService(session).dismiss_suggestion(
            actor, product_id, timezone_offset_minutes
        )
    except MemoryPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except MemoryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/reorder-suggestions/{product_id}/review",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def review_reorder_suggestion(
    product_id: UUID,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    timezone_offset_minutes: Annotated[
        int, Query(alias="timezoneOffsetMinutes", ge=-840, le=840)
    ] = 0,
) -> None:
    try:
        await PurchaseGraphService(session).review_suggestion(
            actor, product_id, timezone_offset_minutes
        )
    except MemoryPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except MemoryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
