"""Sarvam inbound agent's API tool: live delivery context (specs/inbound-delivery-assist.md).

No Clerk session: Sarvam calls this during a phone call. Authenticated by a static
token (``X-GharSaathi-Tool-Token``, or ``?tool_token=`` for builders without
headers), compared in constant time; 404 unless SARVAM_LIVE_CONTEXT_ENABLED.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.inbound_delivery.live_context import (
    TOKEN_HEADER,
    TOKEN_QUERY,
    UNAVAILABLE_MESSAGE,
    LiveContextError,
    LiveDeliveryContextService,
)

router = APIRouter()
MAX_BODY_BYTES = 4096


@lru_cache
def get_live_context_service() -> LiveDeliveryContextService:
    from app.commerce.demo import DemoAddressBook
    from app.commerce.provider_state import SavedAddressBook
    from app.core.config import get_settings
    from app.database.session import get_session_factory
    from app.inbound_delivery.live_context import DatabaseOrderAddresses
    from app.inbound_delivery.store import DeliveryLineStore
    from app.tracking.store import OrderTrackingStore
    from app.user_profile import DatabaseProfileReader

    settings = get_settings()
    sessions = get_session_factory()
    return LiveDeliveryContextService(
        settings,
        OrderTrackingStore(settings.tracking_data_path),
        DemoAddressBook() if settings.commerce_mode == "demo" else SavedAddressBook(sessions),
        DeliveryLineStore(sessions),
        DatabaseProfileReader(sessions),
        order_addresses=DatabaseOrderAddresses(sessions),
    )


LiveContextDependency = Annotated[LiveDeliveryContextService, Depends(get_live_context_service)]


async def _body(request: Request) -> dict[str, object]:
    raw = await request.body()
    if not raw or len(raw) > MAX_BODY_BYTES:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.post("/delivery-context", include_in_schema=False)
async def delivery_context(request: Request, service: LiveContextDependency) -> JSONResponse:
    token = request.headers.get(TOKEN_HEADER) or request.query_params.get(TOKEN_QUERY)
    try:
        service.authorise(token)
    except LiveContextError as exc:
        if exc.status_code == 404:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        headers = {"Retry-After": "60"} if exc.status_code == 429 else None
        return JSONResponse(
            {"detail": exc.code, "active": "no", "message_for_agent": UNAVAILABLE_MESSAGE},
            status_code=exc.status_code,
            headers=headers,
        )
    body = await _body(request)
    # rider_location_hint is accepted for the agent's benefit only: never used or logged.
    context = await service.context(body.get("provider"))
    return JSONResponse(context.model_dump(), headers={"Cache-Control": "no-store"})
