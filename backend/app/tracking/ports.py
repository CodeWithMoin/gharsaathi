from typing import Protocol

from app.tracking.models import TrackingProvider, TrackingSnapshot


class OrderTrackingProvider(Protocol):
    """Read-only provider boundary.  No mutation method belongs here."""

    provider: TrackingProvider

    async def fetch_delivery_status(
        self, *, order_id: str, address_id: str | None
    ) -> TrackingSnapshot: ...


class Clock(Protocol):
    def now(self): ...

    async def sleep(self, seconds: float) -> None: ...
