"""Inbound delivery line (specs/inbound-delivery-assist.md)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.inbound_delivery.models import (
    DeliveryDirections,
    DeliveryLine,
    DirectionsUpdate,
    InboundDeliveryConfig,
    LineSettingsUpdate,
    PrepareRequest,
    SavedDirections,
)
from app.inbound_delivery.service import InboundDeliveryError, InboundDeliveryService

__all__ = [
    "DeliveryDirections",
    "DeliveryLine",
    "DirectionsUpdate",
    "InboundDeliveryConfig",
    "InboundDeliveryError",
    "InboundDeliveryService",
    "InboundDeliveryServiceDependency",
    "LineSettingsUpdate",
    "PrepareRequest",
    "SavedDirections",
    "get_inbound_delivery_service",
]


@lru_cache
def get_inbound_delivery_service() -> InboundDeliveryService:
    from app.commerce.demo import DemoAddressBook
    from app.commerce.provider_state import SavedAddressBook
    from app.core.config import get_settings
    from app.database.session import get_session_factory
    from app.dependencies import get_push_notifier
    from app.inbound_delivery.store import DeliveryLineStore
    from app.user_profile import DatabaseProfileReader

    settings = get_settings()
    sessions = get_session_factory()
    return InboundDeliveryService(
        settings,
        DeliveryLineStore(sessions),
        DemoAddressBook() if settings.commerce_mode == "demo" else SavedAddressBook(sessions),
        DatabaseProfileReader(sessions),
        notifier=get_push_notifier(),
    )


InboundDeliveryServiceDependency = Annotated[
    InboundDeliveryService, Depends(get_inbound_delivery_service)
]
