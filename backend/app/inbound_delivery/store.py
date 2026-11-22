"""Saved per-address directions and the delivery line's last state, per user."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import DeliveryDirectionsRecord, DeliveryLineRecord
from app.database.upsert import insert_if_absent
from app.inbound_delivery.models import DeliveryLine, DirectionsUpdate, SavedDirections


def _directions(record: DeliveryDirectionsRecord) -> SavedDirections:
    return SavedDirections.model_construct(
        provider=record.provider,
        address_id=record.address_id,
        landmark=record.landmark,
        route_directions=[step for step in record.route_directions or [] if isinstance(step, str)],
        gate_note=record.gate_note,
        handover_instructions=record.handover_instructions,
        updated_at=record.updated_at,
    )


def _line(record: DeliveryLineRecord | None) -> DeliveryLine:
    if record is None:
        return DeliveryLine()
    return DeliveryLine.model_validate({
        "phase": record.phase,
        "provider": record.provider,
        "address_id": record.address_id,
        "cohort_id": record.cohort_id,
        "monitor_id": record.monitor_id,
        "reason": record.reason,
        "error": record.error,
        "auto_share": record.auto_share,
        "updated_at": record.updated_at,
    })


class DeliveryLineStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def directions(
        self, user_id: str, provider: str, address_id: str
    ) -> SavedDirections | None:
        async with self.sessions() as session:
            record = await session.scalar(
                select(DeliveryDirectionsRecord).where(
                    DeliveryDirectionsRecord.user_id == user_id,
                    DeliveryDirectionsRecord.provider == provider,
                    DeliveryDirectionsRecord.address_id == address_id,
                )
            )
        return _directions(record) if record is not None else None

    async def save_directions(self, user_id: str, change: DirectionsUpdate) -> SavedDirections:
        async with self.sessions() as session:
            await insert_if_absent(
                session,
                DeliveryDirectionsRecord,
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "provider": change.provider,
                    "address_id": change.address_id,
                    "landmark": change.landmark,
                    "route_directions": [],
                },
                ["user_id", "provider", "address_id"],
            )
            record = await session.scalar(
                select(DeliveryDirectionsRecord)
                .where(
                    DeliveryDirectionsRecord.user_id == user_id,
                    DeliveryDirectionsRecord.provider == change.provider,
                    DeliveryDirectionsRecord.address_id == change.address_id,
                )
                .with_for_update()
            )
            assert record is not None
            record.landmark = change.landmark
            record.route_directions = list(change.route_directions)
            record.gate_note = change.gate_note
            record.handover_instructions = change.handover_instructions
            record.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return _directions(record)

    async def delete_directions(self, user_id: str, provider: str, address_id: str) -> bool:
        async with self.sessions() as session:
            result = await session.execute(
                delete(DeliveryDirectionsRecord).where(
                    DeliveryDirectionsRecord.user_id == user_id,
                    DeliveryDirectionsRecord.provider == provider,
                    DeliveryDirectionsRecord.address_id == address_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def line(self, user_id: str) -> DeliveryLine:
        async with self.sessions() as session:
            record = await session.scalar(
                select(DeliveryLineRecord).where(DeliveryLineRecord.user_id == user_id)
            )
        return _line(record)

    async def update_line(self, user_id: str, **values: object) -> DeliveryLine:
        """Set only the given columns; creates the row on first use."""

        async with self.sessions() as session:
            await insert_if_absent(
                session,
                DeliveryLineRecord,
                {"id": uuid.uuid4(), "user_id": user_id, "auto_share": False, "phase": "none"},
                ["user_id"],
            )
            record = await session.scalar(
                select(DeliveryLineRecord)
                .where(DeliveryLineRecord.user_id == user_id)
                .with_for_update()
            )
            assert record is not None
            for name, value in values.items():
                setattr(record, name, value)
            record.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return _line(record)
