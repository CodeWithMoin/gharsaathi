"""Consent-gated purchase graph APIs. No endpoint places or mutates an order."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.swiggy import _raise_http as _raise_swiggy_http
from app.api.zepto import _raise_http as _raise_zepto_http
from app.commerce.demo import DemoSwiggyService, DemoZeptoService
from app.commerce.swiggy.history import SwiggyOrderHistoryAdapter
from app.commerce.zepto.purchase_history import ZeptoPurchaseHistoryAdapter
from app.core.config import get_settings
from app.dependencies import SwiggyServiceDependency, ZeptoServiceDependency
from app.legal_consent import require_accepted_consent
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency
from app.memory.errors import MemoryNotFoundError, MemoryPermissionError
from app.memory.history_reading import HistoryReader
from app.memory.purchase_graph import (
    AutoSyncOutcome,
    FullResyncUnavailable,
    HistoryCooldown,
    PurchaseGraphService,
    PurchaseImportResult,
    PurchaseSyncStatus,
    ReorderSuggestion,
)


router = APIRouter()

Provider = Literal["swiggy", "zepto"]


class AutoSyncSetting(BaseModel):
    provider: Provider
    enabled: bool


def _is_demo(provider: Provider, swiggy: object, zepto: object) -> bool:
    return (provider == "swiggy" and isinstance(swiggy, DemoSwiggyService)) or (
        provider == "zepto" and isinstance(zepto, DemoZeptoService)
    )


def _reader(provider: Provider, swiggy, zepto) -> HistoryReader:  # type: ignore[no-untyped-def]
    if provider == "zepto":
        return ZeptoPurchaseHistoryAdapter(zepto)
    return SwiggyOrderHistoryAdapter(swiggy.client)


async def _swiggy_cooldown(swiggy: object) -> int:
    client = getattr(swiggy, "client", None)
    cooldown = getattr(client, "cooldown_remaining", None)
    return await cooldown() if cooldown is not None else 0


@router.post(
    "/purchase-history/sync",
    response_model=PurchaseImportResult,
    dependencies=[Depends(require_accepted_consent)],
)
async def sync_purchase_history(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    swiggy: SwiggyServiceDependency,
    zepto: ZeptoServiceDependency,
    provider: Annotated[Provider, Query()] = "swiggy",
    full: Annotated[bool, Query()] = False,
) -> PurchaseImportResult:
    """Explicitly sync read-only history; ``full`` re-walks it (once per 24 h)."""
    if _is_demo(provider, swiggy, zepto):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Order-history import is unavailable in safe demo mode.",
        )
    try:
        return await PurchaseGraphService(session).sync(
            actor,
            _reader(provider, swiggy, zepto),
            provider=provider,
            full=full,
            lookback_days=get_settings().purchase_history_lookback_days,
        )
    except MemoryPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except FullResyncUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except HistoryCooldown as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except Exception as exc:
        if provider == "zepto":
            _raise_zepto_http(exc)
        _raise_swiggy_http(exc)
        raise AssertionError("unreachable")


@router.get("/purchase-history/sync-state", response_model=list[PurchaseSyncStatus])
async def purchase_history_sync_state(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> list[PurchaseSyncStatus]:
    """No provider call: what was imported, coverage, and when re-imports are allowed."""
    return await PurchaseGraphService(session).sync_statuses(actor)


@router.put("/purchase-history/auto-sync", response_model=PurchaseSyncStatus)
async def set_purchase_history_auto_sync(
    body: AutoSyncSetting,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> PurchaseSyncStatus:
    try:
        return await PurchaseGraphService(session).set_auto_sync(
            actor, body.provider, body.enabled
        )
    except MemoryPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post(
    "/purchase-history/auto-sync/run",
    response_model=AutoSyncOutcome,
    dependencies=[Depends(require_accepted_consent)],
)
async def run_purchase_history_auto_sync(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    swiggy: SwiggyServiceDependency,
    zepto: ZeptoServiceDependency,
    provider: Annotated[Provider, Query()] = "swiggy",
) -> AutoSyncOutcome:
    """Called when the app opens; syncs only if opted in and a week has passed."""
    if _is_demo(provider, swiggy, zepto):
        return AutoSyncOutcome(provider=provider, ran=False, reason="disabled")
    return await PurchaseGraphService(session).auto_sync(
        actor,
        _reader(provider, swiggy, zepto),
        provider,
        provider_cooldown_seconds=await _swiggy_cooldown(swiggy) if provider == "swiggy" else 0,
        lookback_days=get_settings().purchase_history_lookback_days,
    )


@router.delete("/purchase-history", status_code=status.HTTP_204_NO_CONTENT)
async def purge_purchase_history(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> None:
    await PurchaseGraphService(session).purge(actor)


@router.get("/usual-suggestions", response_model=list[ReorderSuggestion])
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
    "/usual-suggestions/{product_id}/dismiss",
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
    "/usual-suggestions/{product_id}/review",
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
